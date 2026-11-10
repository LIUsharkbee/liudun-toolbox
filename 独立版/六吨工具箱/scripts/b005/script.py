# -*- coding: utf-8 -*-
"""墙体重叠处理

针对警告：“高亮显示的墙重叠。Revit 查找房间边界时，其中一面墙可能会被忽略。”

三种模式：
  1. 只检查 / 定位：列出每对重叠墙的 ID、类型、长度、房间边界、重叠体积；
  2. 自动剪切：把较短的那面墙“嵌入”较长的那面（SolidSolidCutUtils.AddCutBetweenSolids），
     失败时自动退回“连接几何图形”；
  3. 关闭较短墙的房间边界：适合结构墙 / 建筑墙完全重合、又不该切的情况。

全部按对处理，每对（或每次修改）一个事务，Ctrl+Z 可撤销。
"""

from __future__ import print_function

import traceback

from Autodesk.Revit.DB import (
    BooleanOperationsType,
    BooleanOperationsUtils,
    BuiltInParameter,
    JoinGeometryUtils,
    Options,
    Solid,
    SolidSolidCutUtils,
    Wall,
)
from pyrevit import revit, forms, script

doc = revit.doc
output = script.get_output()

FT3_TO_MM3 = (0.3048 ** 3) * 1e9
FT_TO_MM = 304.8


def safe_call(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def safe_get(obj, name, default=None):
    try:
        return getattr(obj, name)
    except Exception:
        return default


def wall_solid(wall):
    options = Options()
    options.ComputeReferences = False
    geometry = safe_call(lambda: wall.get_Geometry(options), None)
    if geometry is None:
        return None
    best = None
    for item in geometry:
        if isinstance(item, Solid) and safe_call(lambda: item.Volume, 0.0) > 1e-9:
            if best is None or item.Volume > best.Volume:
                best = item
    return best


def wall_info(wall):
    info = {
        "wall": wall,
        "eid": wall.Id.IntegerValue,
        "type": safe_call(lambda: wall.WallType.Name, u"") or u"",
        "width": safe_call(lambda: wall.Width, 0.0) * FT_TO_MM,
        "length": 0.0,
        "level": u"",
        "room_bounding": u"?",
        "structural": u"",
        "solid": wall_solid(wall),
        "volume": 0.0,
    }
    if info["solid"] is not None:
        info["volume"] = safe_call(lambda: info["solid"].Volume, 0.0) * FT3_TO_MM3
    loc = safe_get(wall, "Location", None)
    curve = safe_get(loc, "Curve", None) if loc is not None else None
    if curve is not None:
        info["length"] = safe_call(lambda: curve.Length, 0.0) * FT_TO_MM
    level = safe_call(lambda: doc.GetElement(wall.LevelId), None)
    if level is not None:
        info["level"] = safe_call(lambda: level.Name, u"") or u""
    param = wall.get_Parameter(BuiltInParameter.WALL_ATTR_ROOM_BOUNDING)
    if param is not None:
        info["room_bounding"] = u"是" if param.AsInteger() else u"否"
    usage = safe_call(lambda: wall.StructuralUsage, None)
    if usage is not None:
        info["structural"] = str(usage)
    return info


def overlap_volume(a, b):
    if a["solid"] is None or b["solid"] is None:
        return None
    result = safe_call(
        lambda: BooleanOperationsUtils.ExecuteBooleanOperation(
            a["solid"], b["solid"], BooleanOperationsType.Intersect), None)
    if result is None:
        return 0.0
    return safe_call(lambda: result.Volume, 0.0) * FT3_TO_MM3


def wall_warning_pairs():
    pairs = []
    warnings = safe_call(lambda: list(doc.GetWarnings()), [])
    for failure in warnings:
        desc = safe_call(lambda f=failure: f.GetDescriptionText(), u"") or u""
        low = desc.lower()
        if u"墙" not in desc or (u"重叠" not in desc and "overlap" not in low):
            continue
        walls = []
        for eid in safe_call(lambda f=failure: list(f.GetFailingElements()), []):
            elem = safe_call(lambda eid=eid: doc.GetElement(eid), None)
            if isinstance(elem, Wall):
                walls.append(elem)
        if len(walls) >= 2:
            pairs.append((desc, walls[0], walls[1]))
    return pairs


def cut_pair(a, b):
    short, long_ = (a, b) if a["length"] <= b["length"] else (b, a)
    if short["solid"] is None or long_["solid"] is None:
        return False, u"取不到墙实体，无法剪切"
    try:
        with revit.Transaction(u"剪切墙体 {0} → {1}".format(short["eid"], long_["eid"])):
            SolidSolidCutUtils.AddCutBetweenSolids(doc, short["wall"], long_["wall"])
        return True, u"{0} 已嵌入 {1}".format(short["eid"], long_["eid"])
    except Exception as ex:
        try:
            with revit.Transaction(u"连接墙体 {0} / {1}".format(short["eid"], long_["eid"])):
                JoinGeometryUtils.JoinGeometry(doc, short["wall"], long_["wall"])
            return True, u"剪切失败，已改为连接：{0}".format(ex)
        except Exception as ex2:
            return False, u"剪切失败：{0}；连接也失败：{1}".format(ex, ex2)


def unbound_short(a, b, side=u"自动"):
    if side == u"关闭 A 侧":
        target, keep = a, b
    elif side == u"关闭 B 侧":
        target, keep = b, a
    else:
        target, keep = (a, b) if a["length"] <= b["length"] else (b, a)
    param = target["wall"].get_Parameter(BuiltInParameter.WALL_ATTR_ROOM_BOUNDING)
    if param is None:
        return False, u"{0} 没有“房间边界”参数".format(target["eid"])
    if safe_call(lambda: param.IsReadOnly, False):
        return False, u"{0} 的房间边界参数只读".format(target["eid"])
    try:
        with revit.Transaction(u"关闭房间边界 {0}".format(target["eid"])):
            param.Set(0)
        return True, u"已关闭 {0} 的房间边界（保留 {1}）".format(target["eid"], keep["eid"])
    except Exception as ex:
        return False, u"设置失败：{0}".format(ex)


def classify(a, b, volume):
    if volume is None:
        return u"取不到实体"
    if volume <= 0.0:
        return u"实体不相交"
    small = min(a["volume"], b["volume"])
    same_len = abs(a["length"] - b["length"]) < 50
    if same_len and small > 0 and volume >= small * 0.5:
        return u"疑似重复墙"
    return u"局部相交（可剪切）"


def main():
    if doc is None:
        forms.alert(u"请先打开一个项目。", exitscript=True)

    pairs = wall_warning_pairs()
    if not pairs:
        forms.alert(u"当前没有“墙重叠”类警告。")
        return

    mode = forms.CommandSwitchWindow.show(
        [u"只检查 / 定位",
         u"自动剪切（短墙嵌入长墙）",
         u"关闭较短墙的房间边界"],
        message=u"墙重叠处理：选择要做什么")
    if not mode:
        return

    rows = []
    data = []
    for index, (desc, wa, wb) in enumerate(pairs, 1):
        a = wall_info(wa)
        b = wall_info(wb)
        volume = overlap_volume(a, b)
        judge = classify(a, b, volume)
        data.append((index, a, b, volume))
        rows.append([
            index,
            output.linkify(wa.Id),
            output.linkify(wb.Id),
            u"{0}".format(a["type"]),
            u"{0}".format(b["type"]),
            u"{0:.0f} / {1:.0f}".format(a["length"], b["length"]),
            u"{0:.0f}".format(volume) if volume is not None else u"?",
            judge,
            u"{0} / {1}".format(a["room_bounding"], b["room_bounding"]),
        ])

    output.print_md(u"## 墙体重叠检查")
    output.print_md(u"共 {0} 对重叠墙。".format(len(data)))
    output.print_table(
        rows, title=u"重叠墙对",
        columns=[u"编号", u"墙 A", u"墙 B", u"A 类型", u"B 类型",
                 u"长度(mm) A/B", u"重叠体积(mm³)", u"判断", u"房间边界 A/B"])

    if mode == u"只检查 / 定位":
        output.print_md(u"点 ID 链接定位；确认哪面该保留、哪面该嵌入。")
        return

    labels = [u"{0}: {1} / {2}".format(i, a["eid"], b["eid"]) for i, a, b, _ in data]
    picked = forms.SelectFromList.show(
        labels, title=u"选择要处理的墙对（Ctrl+Z 可撤销）",
        multiselect=True, button_name=u"执行")
    if not picked:
        return

    side = u"自动（关闭短墙）"
    if mode == u"关闭较短墙的房间边界":
        side = forms.SelectFromList.show(
            [u"自动（关闭短墙）", u"关闭 A 侧", u"关闭 B 侧"],
            title=u"关闭哪一面墙的房间边界？", multiselect=False) or side

    ok_count = 0
    failures = []
    for label in picked:
        item = data[labels.index(label)]
        index, a, b, _ = item
        if mode == u"自动剪切（短墙嵌入长墙）":
            ok, msg = cut_pair(a, b)
        else:
            ok, msg = unbound_short(a, b, side)
        if ok:
            ok_count += 1
        else:
            failures.append(u"- {0}：{1}".format(index, msg))

    left = len(wall_warning_pairs())
    output.print_md(u"### 结果")
    output.print_md(u"成功 {0} 个，失败 {1} 个；当前剩 {2} 对墙重叠警告。".format(
        ok_count, len(failures), left))
    if failures:
        output.print_md(u"\n".join(failures))


try:
    main()
except Exception:
    output.print_md(u"脚本出错：\n```\n{0}\n```".format(traceback.format_exc()))
    forms.alert(u"脚本出错，详见输出窗口。", title=u"墙重叠处理")
