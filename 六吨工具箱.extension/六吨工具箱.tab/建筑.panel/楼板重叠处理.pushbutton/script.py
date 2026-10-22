# -*- coding: utf-8 -*-
"""楼板重叠处理

针对警告：“高亮显示的楼板重叠。”

五种模式：
  1. 只检查 / 定位：列出每对重叠板的 ID、类型、顶/底高程、厚度、面积、体积、重叠体积、判断；
  2. 自动剪切：把较薄的一块嵌入较厚的一块（失败自动退回“连接几何图形”）；
  3. 关闭较薄板的房间边界；
  4. 删除较薄的一块（疑似重复板时用，带确认）；
  5. 批量关闭支护 / 土方类板的房间边界（按类型名关键词，一次处理整批）。

每对楼板一个事务，Ctrl+Z 可撤销。
"""

from __future__ import print_function

import traceback

from Autodesk.Revit.DB import (
    BooleanOperationsType,
    BooleanOperationsUtils,
    BuiltInParameter,
    FilteredElementCollector,
    Floor,
    JoinGeometryUtils,
    Options,
    Solid,
    SolidSolidCutUtils,
)
from pyrevit import revit, forms, script

doc = revit.doc
output = script.get_output()

FT3_TO_M3 = 0.3048 ** 3
FT2_TO_M2 = 0.3048 ** 2
FT_TO_MM = 304.8


def safe_call(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def bip(name):
    """按名字取 BuiltInParameter，取不到返回 None。"""
    return getattr(BuiltInParameter, name, None)


def solid_of(elem):
    options = Options()
    options.ComputeReferences = False
    geometry = safe_call(lambda: elem.get_Geometry(options), None)
    if geometry is None:
        return None
    best = None
    for item in geometry:
        if isinstance(item, Solid) and safe_call(lambda: item.Volume, 0.0) > 1e-9:
            if best is None or item.Volume > best.Volume:
                best = item
    return best


def z_range(elem):
    box = safe_call(lambda: elem.get_BoundingBox(None), None)
    if box is None:
        return None
    return (box.Min.Z, box.Max.Z)


def param_number(elem, name, factor=1.0):
    key = bip(name)
    if key is None:
        return None
    param = safe_call(lambda: elem.get_Parameter(key), None)
    if param is None:
        return None
    value = safe_call(lambda: param.AsDouble(), None)
    if value is None:
        return None
    return value * factor


def room_bounding(elem):
    key = bip("FLOOR_ATTR_ROOM_BOUNDING")
    if key is None:
        return u"?"
    param = safe_call(lambda: elem.get_Parameter(key), None)
    if param is None:
        return u"?"
    return u"是" if param.AsInteger() else u"否"


def floor_info(floor):
    info = {
        "floor": floor,
        "eid": floor.Id.IntegerValue,
        "type": u"",
        "level": u"",
        "top": None,
        "bottom": None,
        "thickness": 0.0,
        "area": None,
        "volume": None,
        "room_bounding": room_bounding(floor),
        "solid": solid_of(floor),
    }
    type_elem = safe_call(lambda: doc.GetElement(floor.GetTypeId()), None)
    if type_elem is not None:
        info["type"] = safe_call(lambda: type_elem.Name, u"") or u""
    level = safe_call(lambda: doc.GetElement(floor.LevelId), None)
    if level is not None:
        info["level"] = safe_call(lambda: level.Name, u"") or u""
    zr = z_range(floor)
    if zr is not None:
        info["bottom"] = zr[0] * FT_TO_MM
        info["top"] = zr[1] * FT_TO_MM
        info["thickness"] = (zr[1] - zr[0]) * FT_TO_MM
    info["area"] = param_number(floor, "HOST_AREA_COMPUTED", FT2_TO_M2)
    info["volume"] = param_number(floor, "HOST_VOLUME_COMPUTED", FT3_TO_M3)
    return info


def overlap_volume(a, b):
    if a["solid"] is None or b["solid"] is None:
        return None
    result = safe_call(
        lambda: BooleanOperationsUtils.ExecuteBooleanOperation(
            a["solid"], b["solid"], BooleanOperationsType.Intersect), None)
    if result is None:
        return 0.0
    return safe_call(lambda: result.Volume, 0.0) * (0.3048 ** 3) * 1e9


def floor_warning_pairs():
    pairs = []
    warnings = safe_call(lambda: list(doc.GetWarnings()), [])
    for failure in warnings:
        desc = safe_call(lambda f=failure: f.GetDescriptionText(), u"") or u""
        if u"楼板" not in desc or u"重叠" not in desc:
            continue
        floors = []
        for eid in safe_call(lambda f=failure: list(f.GetFailingElements()), []):
            elem = safe_call(lambda eid=eid: doc.GetElement(eid), None)
            if isinstance(elem, Floor):
                floors.append(elem)
        if len(floors) >= 2:
            pairs.append((desc, floors[0], floors[1]))
    return pairs


def classify(a, b, volume):
    if volume is None:
        return u"取不到实体"
    if volume <= 0.0:
        return u"实体不相交"
    small = min(a["volume"] or 0.0, b["volume"] or 0.0)
    same_area = (a["area"] is not None and b["area"] is not None
                 and abs(a["area"] - b["area"]) <= max(a["area"], b["area"]) * 0.05)
    if same_area and small > 0 and volume / 1e9 >= small * 0.5:
        return u"疑似重复板"
    return u"局部重叠"


def thin_and_thick(a, b):
    return (a, b) if a["thickness"] <= b["thickness"] else (b, a)


def cut_pair(a, b):
    thin, thick = thin_and_thick(a, b)
    if thin["solid"] is None or thick["solid"] is None:
        return False, u"取不到板实体"
    try:
        with revit.Transaction(u"剪切楼板 {0} → {1}".format(thin["eid"], thick["eid"])):
            SolidSolidCutUtils.AddCutBetweenSolids(doc, thin["floor"], thick["floor"])
        return True, u"{0} 已嵌入 {1}".format(thin["eid"], thick["eid"])
    except Exception as ex:
        try:
            with revit.Transaction(u"连接楼板 {0} / {1}".format(thin["eid"], thick["eid"])):
                JoinGeometryUtils.JoinGeometry(doc, thin["floor"], thick["floor"])
            return True, u"剪切失败，已改为连接：{0}".format(ex)
        except Exception as ex2:
            return False, u"剪切失败：{0}；连接也失败：{1}".format(ex, ex2)


def unbound_floor(a, b, side=u"自动"):
    if side == u"关闭 A 侧":
        target, keep = a, b
    elif side == u"关闭 B 侧":
        target, keep = b, a
    else:
        target, keep = thin_and_thick(a, b)
    key = bip("FLOOR_ATTR_ROOM_BOUNDING")
    if key is None:
        return False, u"当前版本没有楼板房间边界参数"
    param = target["floor"].get_Parameter(key)
    if param is None:
        return False, u"{0} 没有房间边界参数".format(target["eid"])
    if safe_call(lambda: param.IsReadOnly, False):
        return False, u"{0} 的房间边界只读".format(target["eid"])
    try:
        with revit.Transaction(u"关闭房间边界 {0}".format(target["eid"])):
            param.Set(0)
        return True, u"已关闭 {0} 的房间边界（保留 {1}）".format(target["eid"], keep["eid"])
    except Exception as ex:
        return False, u"设置失败：{0}".format(ex)


def delete_floor(a, b, unpin=False):
    thin, _ = thin_and_thick(a, b)
    floor = thin["floor"]
    is_pinned = bool(safe_call(lambda: floor.Pinned, False))
    if is_pinned and not unpin:
        return False, u"{0} 已锁定，未解锁，已跳过".format(thin["eid"])
    try:
        with revit.Transaction(u"删除楼板 {0}".format(thin["eid"])):
            if is_pinned and unpin:
                floor.Pinned = False
            doc.Delete(floor.Id)
        suffix = u"（已先解锁）" if is_pinned and unpin else u""
        return True, u"已删除 {0}（{1}）{2}".format(thin["eid"], thin["type"], suffix)
    except Exception as ex:
        return False, u"删除失败：{0}".format(ex)


SUPPORT_WORDS = (u"喷射", u"喷锚", u"土方", u"土料", u"褥垫", u"碎石",
                 u"土壤", u"素土", u"支护", u"回填")

def type_name_of(floor):
    type_elem = safe_call(lambda: doc.GetElement(floor.GetTypeId()), None)
    return safe_call(lambda: type_elem.Name, u"") if type_elem is not None else u""

def batch_unbind_support():
    floors = [f for f in FilteredElementCollector(doc).OfClass(Floor)
              if any(w in type_name_of(f) for w in SUPPORT_WORDS)]
    if not floors:
        forms.alert(u"没有找到名称含支护 / 土方关键词的楼板。")
        return

    key = bip("FLOOR_ATTR_ROOM_BOUNDING")
    if key is None:
        forms.alert(u"当前 Revit 版本没有楼板“房间边界”参数。")
        return

    rows = []
    targets = []
    for floor in floors:
        param = safe_call(lambda f=floor: f.get_Parameter(key), None)
        if param is None:
            continue
        is_on = bool(param.AsInteger())
        rows.append([output.linkify(floor.Id), type_name_of(floor),
                     u"是" if is_on else u"否"])
        if is_on:
            targets.append((floor, param))

    output.print_md(u"## 支护 / 土方类楼板")
    output.print_table(rows, title=u"匹配到的楼板",
                       columns=[u"ID", u"类型", u"房间边界"])
    if not targets:
        output.print_md(u"这些板的房间边界已经是“否”，无需处理。")
        return

    choice = forms.SelectFromList.show(
        [u"确认关闭 {0} 块的房间边界".format(len(targets)), u"取消"],
        title=u"将把列表中“房间边界=是”的 {0} 块板改成“否”".format(len(targets)),
        multiselect=False)
    if not choice or not choice.startswith(u"确认"):
        return

    ok = 0
    failures = []
    with revit.Transaction(u"批量关闭支护类房间边界"):
        for floor, param in targets:
            try:
                param.Set(0)
                ok += 1
            except Exception as ex:
                failures.append(u"- {0}：{1}".format(floor.Id.IntegerValue, ex))

    left = len(floor_warning_pairs())
    output.print_md(u"### 结果")
    output.print_md(u"成功 {0} 个，失败 {1} 个；当前剩 {2} 对楼板重叠警告。".format(
        ok, len(failures), left))
    if failures:
        output.print_md(u"\n".join(failures))


def main():
    if doc is None:
        forms.alert(u"请先打开一个项目。", exitscript=True)

    pairs = floor_warning_pairs()
    if not pairs:
        forms.alert(u"当前没有“楼板重叠”类警告。")
        return

    mode = forms.CommandSwitchWindow.show(
        [u"只检查 / 定位",
         u"自动剪切（薄板嵌入厚板）",
         u"关闭较薄板的房间边界",
         u"删除较薄的一块（重复板）",
         u"批量关闭支护 / 土方类板的房间边界"],
        message=u"楼板重叠处理：选择要做什么")
    if not mode:
        return

    if mode == u"批量关闭支护 / 土方类板的房间边界":
        batch_unbind_support()
        return

    rows = []
    data = []
    for index, (desc, fa, fb) in enumerate(pairs, 1):
        a = floor_info(fa)
        b = floor_info(fb)
        volume = overlap_volume(a, b)
        data.append((index, a, b, volume))
        rows.append([
            index,
            output.linkify(fa.Id),
            output.linkify(fb.Id),
            a["type"],
            b["type"],
            u"{0:.0f} / {1:.0f}".format(a["thickness"], b["thickness"]),
            u"{0:.2f} / {1:.2f}".format(a["volume"] or 0.0, b["volume"] or 0.0),
            u"{0:.0f}".format(volume) if volume is not None else u"?",
            classify(a, b, volume),
        ])

    output.print_md(u"## 楼板重叠检查")
    output.print_md(u"共 {0} 对重叠楼板。".format(len(data)))
    output.print_table(
        rows, title=u"重叠楼板",
        columns=[u"编号", u"板 A", u"板 B", u"A 类型", u"B 类型",
                 u"厚度(mm) A/B", u"体积(m³) A/B", u"重叠(mm³)", u"判断"])

    if mode == u"只检查 / 定位":
        output.print_md(u"点 ID 链接定位；判断列里“疑似重复板”的，通常删掉薄的那块即可。")
        return

    labels = [u"{0}: {1} / {2}".format(i, a["eid"], b["eid"]) for i, a, b, _ in data]
    picked = forms.SelectFromList.show(
        labels, title=u"选择要处理的楼板对（Ctrl+Z 可撤销）",
        multiselect=True, button_name=u"执行")
    if not picked:
        return

    targets = [data[labels.index(p)] for p in picked]

    if mode == u"删除较薄的一块（重复板）":
        detail = u"\n".join(u"- {0}（{1}）".format(thin["eid"], thin["type"])
                           for _, a, b, _ in targets
                           for thin in [thin_and_thick(a, b)[0]])
        choice = forms.SelectFromList.show(
            [u"确认删除这 {0} 块".format(len(targets)), u"取消"],
            title=u"将删除：\n" + detail, multiselect=False)
        if not choice or not choice.startswith(u"确认"):
            return

    side = u"自动"
    if mode == u"关闭较薄板的房间边界":
        side = forms.SelectFromList.show(
            [u"自动（关闭薄板）", u"关闭 A 侧", u"关闭 B 侧"],
            title=u"关闭哪一块的房间边界？", multiselect=False) or u"自动"

    unpin = False
    if mode == u"删除较薄的一块（重复板）":
        pinned = []
        for index, a, b, _ in targets:
            thin, _thick = thin_and_thick(a, b)
            if safe_call(lambda f=thin["floor"]: f.Pinned, False):
                pinned.append((index, thin))
        if pinned:
            choice = forms.SelectFromList.show(
                [u"先解锁这 {0} 块，再删除".format(len(pinned)),
                 u"跳过锁定的，只删没锁的",
                 u"取消"],
                title=u"有 {0} 块楼板是锁定的（图钉）：\n{1}".format(
                    len(pinned),
                    u"、".join(u"#{0}".format(i) for i, _ in pinned)),
                multiselect=False)
            if not choice or choice.startswith(u"取消"):
                return
            unpin = choice.startswith(u"先解锁")

    ok_count = 0
    failures = []
    for index, a, b, _ in targets:
        if mode == u"自动剪切（薄板嵌入厚板）":
            ok, msg = cut_pair(a, b)
        elif mode == u"关闭较薄板的房间边界":
            ok, msg = unbound_floor(a, b, side)
        else:
            ok, msg = delete_floor(a, b, unpin=unpin)
        if ok:
            ok_count += 1
        else:
            failures.append((index, msg))

    left = len(floor_warning_pairs())
    output.print_md(u"### 结果")
    output.print_md(u"成功 {0} 个，失败 {1} 个；当前剩 {2} 对楼板重叠警告。".format(
        ok_count, len(failures), left))
    if failures:
        output.print_table(
            [[i, reason] for i, reason in failures[:80]],
            title=u"失败明细", columns=[u"编号", u"原因"])


try:
    main()
except Exception:
    output.print_md(u"脚本出错：\n```\n{0}\n```".format(traceback.format_exc()))
    forms.alert(u"脚本出错，详见输出窗口。", title=u"楼板重叠处理")
