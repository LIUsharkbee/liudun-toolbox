# -*- coding: utf-8 -*-
"""楼板重叠处理（可筛选混凝土 / 垫层，也可含支护层）

针对警告：“高亮显示的楼板重叠。”

三种模式：
  1. 只检查 / 定位：列出每对重叠板的 ID、类型、顶/底高程、厚度、面积、体积、重叠体积、判断；
  2. 关闭较薄板的房间边界：选中多对时**批量执行，一个事务**（最省事，几何不动）；
  3. 删除较薄的一块（疑似重复板时用，带确认）。

说明：楼板之间不能互相剪切，所以不提供“自动剪切”模式。
失败原因会以表格列出（编号 / 原因），便于排查锁定、工作共享等问题。
"""

from __future__ import print_function

import traceback

from Autodesk.Revit.DB import (
    BooleanOperationsType,
    BooleanOperationsUtils,
    BuiltInParameter,
    ElementTransformUtils,
    Floor,
    JoinGeometryUtils,
    Options,
    Solid,
    SolidSolidCutUtils,
    XYZ,
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


ROOM_BOUNDING_NAMES = (u"房间边界", u"Room Bounding")

def room_bounding_param(elem):
    """按内置参数名 / 参数中文名找“房间边界”，兼容不同版本与语言。"""
    for name in ("FLOOR_ATTR_ROOM_BOUNDING", "ROOM_BOUNDING", "HOST_ROOM_BOUNDING"):
        key = getattr(BuiltInParameter, name, None)
        if key is None:
            continue
        param = safe_call(lambda k=key: elem.get_Parameter(k), None)
        if param is not None:
            return param
    for param in safe_call(lambda: list(elem.Parameters), []):
        pname = safe_call(lambda p=param: p.Definition.Name, u"") or u""
        if pname in ROOM_BOUNDING_NAMES:
            return param
    return None


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
    param = room_bounding_param(elem)
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


def fit_delta(a, b):
    """返回 (状态, delta_mm, 方向, 目标标高)。

    状态：
      ok        —— 可以把薄板整体挪到厚板外侧；
      contained —— 薄板完全夹在厚板厚度范围内，整体移动是错误操作；
      unknown   —— 取不到标高。
    """
    thin, thick = thin_and_thick(a, b)
    if thin["bottom"] is None or thin["top"] is None:
        return ("unknown", None, None, None)
    if thick["top"] is None or thick["bottom"] is None:
        return ("unknown", None, None, None)

    tolerance = 0.5
    contained = (thin["bottom"] > thick["bottom"] + tolerance
                 and thin["top"] < thick["top"] - tolerance)
    if contained:
        return ("contained", None, u"夹在中间", None)

    if thin["bottom"] >= thick["bottom"]:
        target = thick["top"]
        return ("ok", target - thin["bottom"], u"上移", target)
    target = thick["bottom"]
    return ("ok", thick["bottom"] - thin["top"], u"下移", target)


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
    param = room_bounding_param(target["floor"])
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


INCLUDE_WORDS = (u"混凝土", u"垫层", u"底板", u"盖板")
EXCLUDE_WORDS = (u"土料", u"喷射", u"褥垫", u"碎石", u"土壤", u"天花板", u"素土")


def type_name_of(elem):
    type_elem = safe_call(lambda: doc.GetElement(elem.GetTypeId()), None)
    return safe_call(lambda: type_elem.Name, u"") if type_elem is not None else u""


def keep_pair(floor_a, floor_b):
    names = (type_name_of(floor_a), type_name_of(floor_b))
    for name in names:
        if any(word in name for word in EXCLUDE_WORDS):
            return False
    return all(any(word in name for word in INCLUDE_WORDS) for name in names)


def main():
    if doc is None:
        forms.alert(u"请先打开一个项目。", exitscript=True)

    all_pairs = floor_warning_pairs()
    if not all_pairs:
        forms.alert(u"当前没有“楼板重叠”类警告。")
        return

    scope = forms.CommandSwitchWindow.show(
        [u"只看混凝土 / 垫层（排除土方、喷锚、褥垫层）",
         u"包含喷锚 / 土方等支护层（显示全部重叠对）"],
        message=u"筛选范围：要看哪一类？")
    if not scope:
        return
    include_support = scope.startswith(u"包含")

    if include_support:
        pairs = list(all_pairs)
        skipped = 0
    else:
        pairs = [p for p in all_pairs if keep_pair(p[1], p[2])]
        skipped = len(all_pairs) - len(pairs)

    if not pairs:
        forms.alert(u"过滤后没有混凝土 / 垫层类的重叠，已跳过 {0} 对土方、支护类。".format(skipped))
        return

    mode = forms.CommandSwitchWindow.show(
        [u"只检查 / 定位",
         u"按标高贴合（把薄板移出厚板，先预览位移）",
         u"关闭较薄板的房间边界（不改几何，不会消除警告）",
         u"删除较薄的一块（重复板）"],
        message=u"楼板重叠处理：选择要做什么")
    if not mode:
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

    output.print_md(u"## 楼板重叠检查（{0}）".format(
        u"含支护层" if include_support else u"只列混凝土 / 垫层"))
    if include_support:
        output.print_md(u"共 {0} 对（未过滤，含喷锚、土方、褥垫层等）。".format(len(data)))
    else:
        output.print_md(u"共 {0} 对；已过滤掉 {1} 对土方、喷锚、褥垫层等。".format(len(data), skipped))
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
    if mode.startswith(u"关闭较薄板"):
        side = forms.SelectFromList.show(
            [u"自动（关闭薄板）", u"关闭 A 侧", u"关闭 B 侧"],
            title=u"关闭哪一块的房间边界？", multiselect=False) or u"自动"

    ok_count = 0
    failures = []

    if mode.startswith(u"按标高贴合"):
        preview_rows = []
        todo = []
        manual = []
        for index, a, b, _ in targets:
            status, delta, direction, target = fit_delta(a, b)
            thin, thick = thin_and_thick(a, b)
            if status == "unknown":
                failures.append((index, u"{0} 取不到标高".format(thin["eid"])))
                continue

            base_row = [
                index,
                output.linkify(thin["floor"].Id),
                output.linkify(thick["floor"].Id),
                u"{0:.0f} / {1:.0f}".format(thin["bottom"], thin["top"]),
                u"{0:.0f} / {1:.0f}".format(thick["bottom"], thick["top"]),
            ]

            if status == "contained":
                manual.append(index)
                preview_rows.append(base_row + [u"夹在中间", u"—"])
                continue

            preview_rows.append(base_row + [direction, u"{0:+.0f}".format(delta)])
            if abs(delta) > 0.5:
                todo.append((index, thin, delta))

        output.print_table(
            preview_rows, title=u"贴合预览（移动薄板，单位 mm）",
            columns=[u"编号", u"薄板", u"厚板", u"薄板 底/顶",
                     u"厚板 底/顶", u"方向", u"位移"])

        if manual:
            output.print_md(u"其中 {0} 对（编号 {1}）的薄板**夹在厚板厚度范围内**，"
                            u"整体移动会把它抬到厚板外面，属于错误操作，已自动跳过。\n"
                            u"这几对应在剖面里用「修改子图元」把重叠区域压到厚板底以下，"
                            u"或拆分后删掉重叠块。".format(
                                len(manual),
                                u"、".join(u"#{0}".format(i) for i in manual)))

        output.print_md(u"注意：移动的是**整块板**。若两块板只在局部重叠，"
                        u"整体升降可能让板在别处悬空或与别的构件相交——"
                        u"先看预览的位移量，必要时只挑部分配对处理。")

        if not todo:
            output.print_md(u"没有需要移动的板（位移都小于 0.5 mm）。")
        else:
            choice = forms.SelectFromList.show(
                [u"确认移动这 {0} 块板".format(len(todo)), u"取消"],
                title=u"将按预览表的位移整体升降薄板\nCtrl+Z 可整体撤销",
                multiselect=False)
            if not choice or not choice.startswith(u"确认"):
                return
            try:
                with revit.Transaction(u"按标高贴合楼板"):
                    for index, thin, delta in todo:
                        ElementTransformUtils.MoveElement(
                            doc, thin["floor"].Id,
                            XYZ(0.0, 0.0, delta / FT_TO_MM))
                        ok_count += 1
            except Exception as ex:
                ok_count = 0
                failures.append((0, u"批量移动失败：{0}".format(ex)))

    elif mode.startswith(u"关闭较薄板"):
        todo = []
        for index, a, b, _ in targets:
            if side == u"关闭 A 侧":
                target, keep = a, b
            elif side == u"关闭 B 侧":
                target, keep = b, a
            else:
                target, keep = thin_and_thick(a, b)

            param = room_bounding_param(target["floor"])
            if param is None:
                failures.append((index, u"{0} 没有房间边界参数".format(target["eid"])))
                continue
            if safe_call(lambda p=param: p.IsReadOnly, False):
                failures.append((index, u"{0} 的房间边界只读".format(target["eid"])))
                continue
            todo.append((index, target, param))

        if todo:
            try:
                with revit.Transaction(u"批量关闭楼板房间边界"):
                    for index, target, param in todo:
                        param.Set(0)
                        ok_count += 1
            except Exception as ex:
                ok_count = 0
                failures.append((0, u"批量事务失败：{0}".format(ex)))

    else:
        pinned = []
        for index, a, b, _ in targets:
            thin, _thick = thin_and_thick(a, b)
            if safe_call(lambda f=thin["floor"]: f.Pinned, False):
                pinned.append((index, thin))

        unpin = False
        if pinned:
            choice = forms.SelectFromList.show(
                [u"先解锁这 {0} 块，再删除".format(len(pinned)),
                 u"跳过锁定的，只删没锁的",
                 u"取消"],
                title=u"有 {0} 块楼板是锁定的（图钉）：\n{1}".format(
                    len(pinned),
                    u"、".join(u"#{0}".format(i) for i, _ in pinned)),
                multiselect=False)
            if not choice or not choice.startswith(u"取消"):
                unpin = choice.startswith(u"先解锁")
            else:
                return

        for index, a, b, _ in targets:
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
        output.print_md(u"\n提示：楼板之间不能互相剪切；删除失败通常是图元被锁定、"
                        u"工作共享未检出，或属于链接模型。这类情况改用「关闭房间边界」即可。")


try:
    main()
except Exception:
    output.print_md(u"脚本出错：\n```\n{0}\n```".format(traceback.format_exc()))
    forms.alert(u"脚本出错，详见输出窗口。", title=u"楼板重叠处理")
