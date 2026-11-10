# -*- coding: utf-8 -*-
"""楼梯踏面体检

针对警告：“实际楼梯踏板深度小于在楼梯类型中指定的最小踏板深度。”

只读诊断，不改模型：
  1. 遍历项目里所有楼梯；
  2. 逐个梯段读实际踏板深度、实际踢面高度、踏步数（Run 参数）；
  3. 从楼梯类型读最小踏板深度、最大踢面高度；
  4. 算出“踏面差多少”和“梯段需要加长多少”，并给出踢面是否超限。

参数 ID：
  STAIRS_RUN_ACTUAL_TREAD_DEPTH       每跑实际踏面
  STAIRS_RUN_ACTUAL_RISER_HEIGHT      每跑实际踢面
  STAIRS_RUN_ACTUAL_NUMBER_OF_TREADS  每跑踏步数
  STAIRSTYPE_MINIMUM_TREAD_DEPTH      类型最小踏面
  STAIRSTYPE_MAXIMUM_RISER_HEIGHT     类型最大踢面
"""

from __future__ import print_function

import traceback

from Autodesk.Revit.DB import BuiltInParameter, FilteredElementCollector, StorageType
from Autodesk.Revit.DB.Architecture import Stairs

from pyrevit import revit, forms, script

doc = revit.doc
output = script.get_output()

FT_TO_MM = 304.8
TOL_MM = 1.0


def safe_call(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def bip(name):
    return getattr(BuiltInParameter, name, None)


def as_list(collection):
    if collection is None:
        return []
    out = []
    try:
        for item in collection:
            out.append(item)
    except Exception:
        return out
    return out


def as_elements(items):
    """GetStairsRuns 返回 ElementId 集合，统一转成元素。"""
    out = []
    for item in as_list(items):
        if hasattr(item, "GetTypeId"):
            out.append(item)
            continue
        elem = safe_call(lambda i=item: doc.GetElement(i), None)
        if elem is not None:
            out.append(elem)
    return out


def value_mm(elem, name):
    if elem is None:
        return None
    key = bip(name)
    if key is None:
        return None
    param = safe_call(lambda: elem.get_Parameter(key), None)
    if param is None:
        return None
    value = safe_call(lambda: param.AsDouble(), None)
    return None if value is None else value * FT_TO_MM


def value_int(elem, name):
    if elem is None:
        return None
    key = bip(name)
    if key is None:
        return None
    param = safe_call(lambda: elem.get_Parameter(key), None)
    if param is None:
        return None
    return safe_call(lambda: param.AsInteger(), None)


def type_name(elem):
    type_elem = safe_call(lambda: doc.GetElement(elem.GetTypeId()), None)
    return safe_call(lambda: type_elem.Name, u"") if type_elem is not None else u""


def first_not_none(*values):
    for value in values:
        if value is not None:
            return value
    return None


def first_mm(elem, names):
    for name in names:
        value = value_mm(elem, name)
        if value is not None:
            return value
    return None


def stairs_type_name(stairs):
    name = type_name(stairs)
    if name:
        return name
    key = bip("ELEM_TYPE_PARAM")
    if key is not None:
        param = safe_call(lambda: stairs.get_Parameter(key), None)
        if param is not None:
            eid = safe_call(lambda: param.AsElementId(), None)
            elem = safe_call(lambda: doc.GetElement(eid), None)
            if elem is not None:
                return safe_call(lambda: elem.Name, u"") or u""
    return u""


def dump_params(elem, title):
    rows = []
    if elem is None:
        output.print_md(u"（{0}：无元素）".format(title))
        return
    for param in elem.Parameters:
        name = safe_call(lambda p=param: p.Definition.Name, u"")
        if not name:
            continue
        storage = safe_call(lambda p=param: p.StorageType, None)
        value = u""
        try:
            if storage == StorageType.Double:
                value = u"{0:.1f}".format((safe_call(lambda p=param: p.AsDouble(), 0.0) or 0.0) * FT_TO_MM)
            elif storage == StorageType.Integer:
                value = str(safe_call(lambda p=param: p.AsInteger(), u""))
            elif storage == StorageType.ElementId:
                value = str(safe_call(lambda p=param: p.AsElementId().IntegerValue, u""))
            else:
                value = safe_call(lambda p=param: p.AsString(), u"") or u""
        except Exception:
            value = u""
        pid = safe_call(lambda p=param: p.Id.IntegerValue, u"")
        rows.append([name, value, pid])
    output.print_table(
        rows[:120], title=title,
        columns=[u"参数", u"值（长度 mm）", u"参数 ID"])


def inspect_stairs(stairs):
    stairs_eid = safe_call(lambda: stairs.Id.IntegerValue, -1)
    stairs_type = stairs_type_name(stairs)
    type_id = safe_call(lambda: stairs.GetTypeId(), None)
    type_elem = safe_call(lambda: doc.GetElement(type_id), None) if type_id is not None else None

    min_tread = first_mm(type_elem, ("STAIRSTYPE_MINIMUM_TREAD_DEPTH",
                                     "STAIRS_ATTR_MINIMUM_TREAD_DEPTH"))
    if min_tread is None:
        min_tread = first_mm(stairs, ("STAIRS_ATTR_MINIMUM_TREAD_DEPTH",
                                      "STAIRSTYPE_MINIMUM_TREAD_DEPTH"))
    max_riser = first_mm(type_elem, ("STAIRSTYPE_MAXIMUM_RISER_HEIGHT",
                                     "STAIRS_ATTR_MAX_RISER_HEIGHT"))
    if max_riser is None:
        max_riser = first_mm(stairs, ("STAIRS_ATTR_MAX_RISER_HEIGHT",
                                      "STAIRSTYPE_MAXIMUM_RISER_HEIGHT"))

    fallback_tread = value_mm(stairs, "STAIRS_ACTUAL_TREAD_DEPTH")
    fallback_riser = value_mm(stairs, "STAIRS_ACTUAL_RISER_HEIGHT")

    rows = []
    runs = as_elements(safe_call(lambda: stairs.GetStairsRuns(), []))
    if not runs:
        rows.append({
            "stairs": stairs, "stairs_eid": stairs_eid, "type": stairs_type,
            "run_eid": None, "tread": fallback_tread, "min_tread": min_tread,
            "riser": fallback_riser, "max_riser": max_riser,
            "treads": None, "short": None, "need": None, "flag": False,
            "note": u"读不到梯段",
        })
        return rows

    for run in runs:
        tread = first_not_none(value_mm(run, "STAIRS_RUN_ACTUAL_TREAD_DEPTH"), fallback_tread)
        riser = first_not_none(value_mm(run, "STAIRS_RUN_ACTUAL_RISER_HEIGHT"), fallback_riser)
        treads = value_int(run, "STAIRS_RUN_ACTUAL_NUMBER_OF_TREADS")

        short = None
        need = None
        if tread is not None and min_tread is not None:
            short = min_tread - tread
            if short > TOL_MM and treads:
                need = short * treads

        notes = []
        if min_tread is None:
            notes.append(u"类型无最小踏面参数")
        if short is not None and short > TOL_MM:
            notes.append(u"踏面不足 {0:.0f} mm".format(short))
        if riser is not None and max_riser is not None and riser > max_riser + TOL_MM:
            notes.append(u"踢面超限 {0:.0f} mm".format(riser - max_riser))

        rows.append({
            "stairs": stairs,
            "stairs_eid": stairs_eid,
            "type": stairs_type,
            "run_eid": safe_call(lambda r=run: r.Id.IntegerValue, None),
            "tread": tread,
            "min_tread": min_tread,
            "riser": riser,
            "max_riser": max_riser,
            "treads": treads,
            "short": short,
            "need": need,
            "flag": bool(short is not None and short > TOL_MM),
            "note": u"；".join(notes),
        })
    return rows


def fmt(value, pattern=u"{0:.0f}"):
    if value is None:
        return u"-"
    return pattern.format(float(value))


def main():
    if doc is None:
        forms.alert(u"请先打开一个项目。", exitscript=True)

    stairs_list = list(FilteredElementCollector(doc).OfClass(Stairs))
    if not stairs_list:
        forms.alert(u"当前项目里没有楼梯。")
        return

    data = []
    for stairs in stairs_list:
        data.extend(inspect_stairs(stairs))

    bad = [x for x in data if x["flag"]]

    output.print_md(u"## 楼梯踏面体检")
    output.print_md(u"共 {0} 条梯段（{1} 部楼梯），踏面不足 {2} 条。".format(
        len(data), len(stairs_list), len(bad)))

    rows = []
    for item in data[:300]:
        rows.append([
            output.linkify(item["stairs"].Id),
            item["type"],
            fmt(item["tread"]),
            fmt(item["min_tread"]),
            fmt(item["short"]) if item["short"] is not None else u"-",
            fmt(item["riser"]),
            fmt(item["max_riser"]),
            fmt(item["treads"], u"{0:.0f}") if item["treads"] is not None else u"-",
            fmt(item["need"]) if item["need"] is not None else u"-",
            item["note"] or (u"正常" if not item["flag"] else u"踏面不足"),
        ])

    output.print_table(
        rows, title=u"逐梯段数据（长度单位 mm）",
        columns=[u"楼梯 ID", u"类型", u"实际踏面", u"最小踏面", u"差",
                 u"实际踢面", u"最大踢面", u"踏步数", u"建议加长", u"说明"])

    missing = [x for x in data if x["min_tread"] is None]
    if missing:
        output.print_md(
            u"\n**诊断**：{0} 条梯段读不到「最小踏板深度」。下面是第一条楼梯的类型 / 实例参数清单，"
            u"用来看这个值到底挂在哪个参数上。".format(len(missing)))
        sample = missing[0]["stairs"]
        tid = safe_call(lambda: sample.GetTypeId(), None)
        if tid is not None:
            output.print_md(u"类型 ID：{0}".format(safe_call(lambda: tid.IntegerValue, u"-")))
            dump_params(safe_call(lambda: doc.GetElement(tid), None), u"类型参数")
        dump_params(sample, u"实例参数")

    if bad:
        output.print_md(
            u"\n**怎么改**：优先加长梯段（`建议加长` 就是需要的增量）；"
            u"加长会压缩平台，改完回到「平台深度检查」再核一次。"
            u"只有在洞口定死、无法加长时，才考虑降低类型里的「最小踏板深度」——"
            u"那是迁就现状，合规性要自己判断。")
    else:
        output.print_md(u"\n没有发现踏面不足的梯段。")

    if bad:
        labels = [u"{0}  差 {1:.0f} mm".format(x["stairs_eid"], float(x["short"] or 0))
                  for x in bad]
        picked = forms.SelectFromList.show(
            labels, title=u"选中并定位到问题楼梯", multiselect=False, button_name=u"定位")
        if picked:
            target = bad[labels.index(picked)]
            try:
                revit.get_selection().set_to([target["stairs"]])
                revit.uidoc.ShowElements(target["stairs"].Id)
            except Exception as ex:
                output.print_md(u"定位失败：{0}".format(ex))


try:
    main()
except Exception:
    output.print_md(u"脚本出错：\n```\n{0}\n```".format(traceback.format_exc()))
    forms.alert(u"脚本出错，详见输出窗口。", title=u"楼梯踏面体检")
