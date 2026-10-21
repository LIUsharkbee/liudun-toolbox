# -*- coding: utf-8 -*-
"""流向检查 v4 —— 排查“无法计算流量，因为流动方向不匹配”

v4 改动：
  * 方向：改读 Connector.Direction（Revit 2020 没有 FlowDirection 属性），
    读不到就报“方向读取失败”，不再把两个 "?" 当成“进出口同向”；
  * 取图元：从选中图元出发做连接网络 BFS（沿 AllRefs 走遍整张网），
    不再依赖 MEPSystem.Elements 这个常常为空的集合；
  * 诊断：补 HasPlaceholders / IsMultipleNetwork / SectionsCount，
    并把“系统成员数”和“网络图元数”分开列；
  * 系统只有 1 个图元时会直接提示：风管可能在链接模型里，或属于别的系统。

用法：
  1) 最准的用法：在警告框点“显示(S)”，Revit 会高亮出问题的图元，保持选中再点本按钮；
  2) 也可以不选，按钮会列出系统让你挑；
  3) 看输出窗口，桌面会生成 CSV。
"""

from __future__ import print_function

import codecs
import datetime
import os
import traceback

from System import Environment

from Autodesk.Revit.DB import (
    BuiltInCategory,
    ConnectorType,
    Domain,
    ElementId,
    FamilyInstance,
    FilteredElementCollector,
    MEPCurve,
    StorageType,
)
from Autodesk.Revit.DB.Mechanical import MechanicalSystem

from pyrevit import revit, script, forms

doc = revit.doc
uidoc = revit.uidoc
output = script.get_output()

FT3_TO_M3H = 0.028316846592 * 3600.0
FT_TO_M = 0.3048

try:
    text_type = unicode
except NameError:
    text_type = str

ERRORS = []


def safe_get(obj, name, default=None):
    try:
        return getattr(obj, name)
    except Exception:
        return default


def safe_call(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def bc(name):
    value = getattr(BuiltInCategory, name, None)
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


SCAN_CATS = tuple(
    cat for cat in (
        getattr(BuiltInCategory, "OST_DuctCurves", None),
        getattr(BuiltInCategory, "OST_FlexDuctCurves", None),
        getattr(BuiltInCategory, "OST_DuctFitting", None),
        getattr(BuiltInCategory, "OST_DuctFittings", None),
        getattr(BuiltInCategory, "OST_DuctAccessory", None),
        getattr(BuiltInCategory, "OST_DuctTerminal", None),
        getattr(BuiltInCategory, "OST_MechanicalEquipment", None),
    ) if cat is not None
)

FILTER_CAT_IDS = tuple(
    cid for cid in (bc("OST_DuctTerminal"), bc("OST_MechanicalEquipment"))
    if cid is not None
)


def set_size(collection):
    if collection is None:
        return -1
    try:
        return int(safe_get(collection, "Size", -1))
    except Exception:
        return -1


def to_list(collection, tag=u""):
    if collection is None:
        return []
    for meth in ("ForwardIterator", "GetEnumerator"):
        getter = safe_get(collection, meth, None)
        if getter is None:
            continue
        try:
            it = getter()
        except Exception as ex:
            ERRORS.append(u"{0}.{1}() 失败：{2}".format(tag, meth, ex))
            continue
        if it is None:
            continue
        items = []
        try:
            while it.MoveNext():
                items.append(it.Current)
        except Exception as ex:
            ERRORS.append(u"{0}.{1} 遍历失败：{2}".format(tag, meth, ex))
            continue
        return items
    try:
        return list(collection)
    except Exception as ex:
        ERRORS.append(u"{0} list() 失败：{1}".format(tag, ex))
        return []


def dedupe(elems):
    found = {}
    for e in elems:
        eid = safe_call(lambda e=e: e.Id.IntegerValue, None)
        if eid is not None:
            found[eid] = e
    return list(found.values())


def system_type_name(ms):
    def _get():
        te = doc.GetElement(ms.GetTypeId())
        return te.Name if te is not None else u""
    return safe_call(_get, u"")


def connector_direction(c):
    """Revit 2020：Connector.Direction；老版本/别名兜底 FlowDirection。"""
    for name in ("Direction", "FlowDirection"):
        value = safe_get(c, name, None)
        if value is not None:
            return text_type(value)
    return u"?"


def dir_param(elem):
    for p in to_list(safe_get(elem, "Parameters", None), u"Parameters"):
        name = safe_call(lambda p=p: p.Definition.Name, u"") or u""
        if not name:
            continue
        low = name.lower()
        if (u"流向" not in name) and (u"方向" not in name) and (u"flow direction" not in low):
            continue
        val = safe_call(lambda p=p: p.AsValueString(), u"") or u""
        if not val:
            def _raw(p=p):
                if p.StorageType == StorageType.String:
                    return p.AsString() or u""
                if p.StorageType == StorageType.Integer:
                    return text_type(p.AsInteger())
                return u""
            val = safe_call(_raw, u"") or u""
        if val:
            return u"{0}={1}".format(name, val)
    return u""


def connectors_of(elem):
    cm = None
    if isinstance(elem, MEPCurve):
        cm = safe_get(elem, "ConnectorManager", None)
    else:
        mpm = safe_get(elem, "MEPModel", None)
        if mpm is not None:
            cm = safe_get(mpm, "ConnectorManager", None)
    if cm is None:
        return []
    return to_list(safe_get(cm, "Connectors", None), u"Connectors")


def duct_connectors(elem):
    out = []
    for c in connectors_of(elem):
        if safe_get(c, "Domain", None) != Domain.DomainHvac:
            continue
        if safe_get(c, "ConnectorType", None) == ConnectorType.Logical:
            continue
        out.append(c)
    return out


def elem_system_name(elem):
    sysm = safe_get(elem, "MEPSystem", None)
    if sysm is None:
        for c in connectors_of(elem):
            sysm = safe_get(c, "MEPSystem", None)
            if sysm is not None:
                break
    if sysm is None:
        return u""
    return safe_call(lambda: sysm.Name, u"") or u""


def elem_labels(elem):
    cat = u""
    cat_id = -1
    category = safe_get(elem, "Category", None)
    if category is not None:
        cat = safe_call(lambda: category.Name, u"") or u""
        cat_id = safe_call(lambda: category.Id.IntegerValue, -1)
    fam = u""
    if isinstance(elem, FamilyInstance):
        fam = safe_call(lambda: elem.Symbol.Family.Name, u"") or u""
    typ = u""

    def _type_name():
        tid = elem.GetTypeId()
        if tid != ElementId.InvalidElementId:
            te = doc.GetElement(tid)
            if te is not None:
                return te.Name
        return u""

    typ = safe_call(_type_name, u"") or u""
    if not typ:
        typ = safe_call(lambda: elem.Name, u"") or u""
    return cat, cat_id, fam, typ


def fmt_num(value, factor):
    if value is None:
        return u""
    try:
        return u"{0:.2f}".format(float(value) * factor)
    except Exception:
        return u""


def connected_network(seeds, limit=3000):
    seen = {}
    queue = [e for e in dedupe(seeds) if e is not None]
    while queue:
        elem = queue.pop(0)
        eid = safe_call(lambda elem=elem: elem.Id.IntegerValue, None)
        if eid is None or eid in seen:
            continue
        seen[eid] = elem
        if len(seen) > limit:
            ERRORS.append(u"连接网络超过 {0} 个图元，已截断".format(limit))
            break
        for c in connectors_of(elem):
            for ref in to_list(safe_get(c, "AllRefs", None), u"AllRefs"):
                owner = safe_get(ref, "Owner", None)
                if owner is None:
                    continue
                oid = safe_call(lambda owner=owner: owner.Id.IntegerValue, None)
                if oid is not None and oid not in seen:
                    queue.append(owner)
    return list(seen.values())


def element_system_id(elem):
    sysm = safe_get(elem, "MEPSystem", None)
    if sysm is None:
        for c in connectors_of(elem):
            sysm = safe_get(c, "MEPSystem", None)
            if sysm is not None:
                break
    if sysm is None:
        return None
    return safe_call(lambda: sysm.Id.IntegerValue, None)


def scan_by_system(sys_id):
    found = {}
    for cat in SCAN_CATS:
        if sys_id is None:
            break
        try:
            coll = FilteredElementCollector(doc).OfCategory(cat).WhereElementIsNotElementType()
        except Exception as ex:
            ERRORS.append(u"扫描类别 {0} 失败：{1}".format(cat, ex))
            continue
        for e in to_list(coll, u"类别{0}".format(cat)):
            if element_system_id(e) == sys_id:
                eid = safe_call(lambda e=e: e.Id.IntegerValue, None)
                if eid is not None:
                    found[eid] = e
    return list(found.values())


def system_elements(ms, notes):
    sys_id = safe_call(lambda: ms.Id.IntegerValue, None)
    elements_set = safe_get(ms, "Elements", None)
    n_elements = set_size(elements_set)
    n_network = set_size(safe_get(ms, "DuctNetwork", None))

    elems = dedupe(to_list(elements_set, u"Elements"))
    src = u"Elements"
    if not elems:
        elems = dedupe(to_list(safe_get(ms, "DuctNetwork", None), u"DuctNetwork"))
        src = u"DuctNetwork"
    if not elems:
        elems = dedupe(scan_by_system(sys_id))
        src = u"按类别反查"

    notes.append(u"Elements.Size={0}，DuctNetwork.Size={1}，IsWellConnected={2}，"
                 u"IsEmpty={3}，HasPlaceholders={4}，IsMultipleNetwork={5}，"
                 u"SectionsCount={6}，来源：{7}，系统成员 {8}".format(
                     n_elements, n_network,
                     safe_get(ms, "IsWellConnected", None),
                     safe_get(ms, "IsEmpty", None),
                     safe_get(ms, "HasPlaceholders", None),
                     safe_get(ms, "IsMultipleNetwork", None),
                     safe_get(ms, "SectionsCount", None),
                     src, len(elems)))
    return elems


def collect_records(elems):
    recs = []
    for elem in elems:
        cat, cat_id, fam, typ = elem_labels(elem)
        rec = {
            "eid": safe_call(lambda: elem.Id.IntegerValue, -1),
            "system": elem_system_name(elem),
            "cat": cat,
            "cat_id": cat_id,
            "fam": fam,
            "typ": typ,
            "dir_param": dir_param(elem),
            "conns": [],
            "notes": [],
        }
        for c in duct_connectors(elem):
            rec["conns"].append({
                "idx": len(rec["conns"]) + 1,
                "ctype": text_type(safe_get(c, "ConnectorType", u"")),
                "dir": connector_direction(c),
                "flow": safe_get(c, "Flow", None),
                "vel": safe_get(c, "Velocity", None),
                "connected": u"是" if safe_get(c, "IsConnected", False) else u"否",
            })
        recs.append(rec)
    annotate(recs)
    return recs


def annotate(recs):
    for rec in recs:
        conns = rec["conns"]
        known = [c["dir"] for c in conns if c["dir"] in (u"In", u"Out")]
        unknown = [c for c in conns if c["dir"] not in (u"In", u"Out", u"Bidirectional")]
        if len(conns) >= 2 and len(known) >= 2 and len(set(known)) == 1:
            rec["notes"].append(u"进出口同向（{0}）".format(known[0]))
        if unknown:
            rec["notes"].append(u"方向读取失败（{0} 个）".format(len(unknown)))
        if conns:
            if all(c["connected"] == u"否" for c in conns):
                rec["notes"].append(u"未连接任何图元")
            elif any(c["connected"] == u"否" for c in conns):
                rec["notes"].append(u"有连接件未连上")
        if len(conns) == 1 and conns[0]["dir"] == u"Bidirectional":
            rec["notes"].append(u"单个连接件为双向")
        if u"送风" in rec["system"] and u"回" in rec["dir_param"]:
            rec["notes"].append(u"流向参数为回风，与送风系统不符")
        if u"回风" in rec["system"] and u"送" in rec["dir_param"]:
            rec["notes"].append(u"流向参数为送风，与回风系统不符")

    for cat_id in FILTER_CAT_IDS:
        group = [r for r in recs
                 if r["cat_id"] == cat_id and len(r["conns"]) == 1
                 and r["conns"][0]["dir"] in (u"In", u"Out")]
        if len(group) < 2:
            continue
        counts = {}
        for r in group:
            d = r["conns"][0]["dir"]
            counts[d] = counts.get(d, 0) + 1
        major, best = u"", 0
        for d, n in counts.items():
            if n > best:
                major, best = d, n
        if best == len(group):
            continue
        for r in group:
            if r["conns"][0]["dir"] != major:
                r["notes"].append(u"与同类多数（{0}）不一致".format(major))


HEADER = [u"系统", u"图元ID", u"类别", u"族", u"类型", u"连接件序号",
          u"连接件类型", u"流向", u"已连接", u"流量(m³/h)", u"风速(m/s)",
          u"流向参数", u"疑似问题"]


def build_rows(recs):
    rows = []
    for rec in recs:
        note = u"; ".join(rec["notes"])
        if not rec["conns"]:
            rows.append([rec["system"], rec["eid"], rec["cat"], rec["fam"], rec["typ"],
                         u"", u"", u"", u"", u"", u"", rec["dir_param"],
                         note or u"无风管连接件"])
            continue
        for c in rec["conns"]:
            rows.append([
                rec["system"], rec["eid"], rec["cat"], rec["fam"], rec["typ"],
                c["idx"], c["ctype"], c["dir"], c["connected"],
                fmt_num(c["flow"], FT3_TO_M3H), fmt_num(c["vel"], FT_TO_M),
                rec["dir_param"], note,
            ])
    return rows


def csv_cell(value):
    s = u"" if value is None else text_type(value)
    s = s.replace(u"\r", u" ").replace(u"\n", u" ")
    if u"," in s or u'"' in s:
        s = u'"' + s.replace(u'"', u'""') + u'"'
    return s


def output_dir():
    def _desktop():
        p = Environment.GetFolderPath(Environment.SpecialFolder.Desktop)
        return p if p and os.path.isdir(p) else u""
    return safe_call(_desktop, u"") or safe_call(lambda: os.path.expanduser(u"~"), u".")


def write_csv(path, rows):
    with codecs.open(path, "w", "utf-8-sig") as f:
        f.write(u",".join(csv_cell(h) for h in HEADER) + u"\r\n")
        for row in rows:
            f.write(u",".join(csv_cell(v) for v in row) + u"\r\n")


def selected_elements():
    out = []
    for eid in to_list(safe_call(lambda: uidoc.Selection.GetElementIds(), None), u"Selection"):
        elem = doc.GetElement(eid)
        if elem is not None:
            out.append(elem)
    return out


def all_systems():
    return dedupe(to_list(
        FilteredElementCollector(doc).OfClass(MechanicalSystem), u"MechanicalSystem"))


def pick_systems():
    systems = all_systems()
    if not systems:
        forms.alert(u"当前项目里没有风系统（机械系统）。", exitscript=True)
    labels = [u"{0}  [{1}]".format(safe_get(ms, "Name", u""), system_type_name(ms))
              for ms in systems]
    picked = forms.SelectFromList.show(
        labels, title=u"选择要检查的风系统（可多选）",
        multiselect=True, button_name=u"开始检查")
    if not picked:
        return []
    return [systems[labels.index(p)] for p in picked]


ADVICE = [
    (u"进出口同向", u"选中该图元按空格旋转 180°；旋转后仍报错，说明族里两个连接件的流向定义错了，进族编辑器把其中一个改成反方向。"),
    (u"与同类多数", u"该风口 / 设备的族用错了（送风族用到了回风系统，或反之），换族或改族的连接件流向后重新载入。"),
    (u"未连接任何图元", u"图元是“孤岛”。如果整个系统都这样，说明风管根本不在这个模型里（链接模型？）或属于别的系统。"),
    (u"有连接件未连上", u"用“分析 → 检查系统 → 检查风管系统”找断点，再用“连接到”补上。"),
    (u"方向读取失败", u"该连接件的 Direction 读不出来，多半是逻辑连接件或族异常，可忽略。"),
    (u"与送风系统不符", u"族参数里的流向写成了回风，改族参数或换族。"),
]


def main():
    groups = []

    seeds = selected_elements()
    if seeds:
        net = connected_network(seeds)
        groups.append({
            "title": u"选中网络",
            "elements": net,
            "diag": u"选中 {0} 个图元，连接网络 {1} 个图元".format(len(seeds), len(net)),
        })
    else:
        for ms in pick_systems():
            notes = []
            elems = system_elements(ms, notes)
            net = connected_network(elems)
            notes.append(u"网络图元 {0}".format(len(net)))
            groups.append({
                "title": safe_get(ms, "Name", u""),
                "elements": net,
                "diag": u"；".join(notes),
                "system": ms,
            })
        if not groups:
            return

    output.print_md(u"## 流向检查 v4")
    output.print_md(u"> 只读检查，不会改动模型。最准的用法：先在警告框点“显示(S)”，保持选中再点按钮。")

    all_rows = []
    summary = []
    diag_lines = []
    flagged = []
    for grp in groups:
        recs = collect_records(grp["elements"])
        rows = build_rows(recs)
        all_rows.extend(rows)
        bad = [r for r in recs if r["notes"]]
        n_conn = sum(len(r["conns"]) for r in recs)
        summary.append([grp["title"], len(recs), n_conn, len(bad)])
        diag_lines.append(u"- **{0}**：{1}".format(grp["title"], grp["diag"]))
        for r in bad:
            flagged.append([r["system"] or grp["title"], r["eid"], r["cat"],
                            r["fam"], r["typ"], u"; ".join(r["notes"])])
        if len(recs) <= 1:
            diag_lines.append(u"- ⚠️ **{0}** 只有 {1} 个图元，基本等于空系统："
                              u"风管可能在链接模型里，或这段属于别的系统。".format(
                                  grp["title"], len(recs)))

    output.print_table(summary, title=u"概况",
                       columns=[u"分组", u"图元数", u"连接件数", u"疑似数"])

    output.print_md(u"### 诊断")
    output.print_md(u"\n".join(diag_lines))
    if ERRORS:
        seen = []
        for e in ERRORS:
            if e not in seen:
                seen.append(e)
        output.print_md(u"\n读取报错：\n" + u"\n".join(u"- `{0}`".format(x) for x in seen[:6]))

    if flagged:
        output.print_table(flagged[:80],
                           title=u"疑似问题图元（共 {0} 个）".format(len(flagged)),
                           columns=[u"系统", u"图元ID", u"类别", u"族", u"类型", u"疑似问题"])
        output.print_md(u"### 处理建议")
        tips = []
        for key, tip in ADVICE:
            if any(key in (r[5] or u"") for r in flagged):
                tips.append(u"- **{0}**：{1}".format(key, tip))
        if tips:
            output.print_md(u"\n".join(tips))
        output.print_md(u"\n按图元 ID 用“管理 → 按 ID 选择”定位最快。")
    elif all_rows:
        output.print_md(u"未发现明显可疑项 —— 问题可能在族内部连接件定义，或两个不同系统的图元被连在了一起。")

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    name = groups[0]["title"] or u"风系统"
    for ch in u'\\/:*?"<>|':
        name = name.replace(ch, u"_")
    path = os.path.join(output_dir(), u"流向检查_{0}_{1}.csv".format(name, stamp))
    try:
        write_csv(path, all_rows)
        output.print_md(u"**CSV 已导出：** `{0}`（共 {1} 行）".format(path, len(all_rows)))
    except Exception:
        output.print_md(u"CSV 写入失败：\n```\n{0}\n```".format(traceback.format_exc()))


try:
    main()
except Exception:
    output.print_md(u"脚本出错：\n```\n{0}\n```".format(traceback.format_exc()))
    forms.alert(u"脚本出错，详见输出窗口。", title=u"流向检查")
