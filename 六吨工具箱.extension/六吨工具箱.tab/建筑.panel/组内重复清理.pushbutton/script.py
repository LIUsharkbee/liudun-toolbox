# -*- coding: utf-8 -*-
"""组内重复图元清理

针对警告：“同一位置处具有相同实例。这将导致在明细表中重复计算。”

做法：
  1. 遍历项目里所有组（Group）；
  2. 取组内成员（GetMemberIds），按「类别 + 类型 + 包围盒中心」做指纹；
  3. 指纹相同的即为同位置重复实例，列出组、类别、类型、数量和坐标；
  4. 可选：一键删除重复（每组每组类型每位置只保留一个），带确认、可 Ctrl+Z。

只处理组内重复；不在组里的重复图元不碰（避免误删）。
"""

from __future__ import print_function

import traceback

from Autodesk.Revit.DB import FilteredElementCollector, Group

from pyrevit import revit, forms, script

doc = revit.doc
output = script.get_output()

FT_TO_MM = 304.8
TOL_MM = 2.0


def safe_call(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


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


def center_key(elem):
    box = safe_call(lambda: elem.get_BoundingBox(None), None)
    if box is None:
        return None
    x = (box.Min.X + box.Max.X) / 2.0 * FT_TO_MM
    y = (box.Min.Y + box.Max.Y) / 2.0 * FT_TO_MM
    z = (box.Min.Z + box.Max.Z) / 2.0 * FT_TO_MM
    return (round(x / TOL_MM), round(y / TOL_MM), round(z / TOL_MM), x, y, z)


def elem_names(elem):
    category = safe_call(lambda: elem.Category.Name, u"") or u""
    type_elem = safe_call(lambda: doc.GetElement(elem.GetTypeId()), None)
    type_name = safe_call(lambda: type_elem.Name, u"") if type_elem is not None else u""
    return category, type_name


def scan():
    groups = list(FilteredElementCollector(doc).OfClass(Group))
    results = []
    for group in groups:
        members = as_list(safe_call(lambda g=group: g.GetMemberIds(), []))
        buckets = {}
        for eid in members:
            elem = safe_call(lambda eid=eid: doc.GetElement(eid), None)
            if elem is None:
                continue
            key = center_key(elem)
            if key is None:
                continue
            type_id = safe_call(lambda e=elem: e.GetTypeId().IntegerValue, -1)
            full_key = (type_id, key[0], key[1], key[2])
            buckets.setdefault(full_key, []).append((elem, key))
        for full_key, items in buckets.items():
            if len(items) < 2:
                continue
            elem = items[0][0]
            category, type_name = elem_names(elem)
            results.append({
                "group": group,
                "group_eid": safe_call(lambda g=group: g.Id.IntegerValue, -1),
                "group_name": safe_call(lambda g=group: g.Name, u"") or u"",
                "category": category,
                "type": type_name,
                "count": len(items),
                "ids": [safe_call(lambda e=e: e.Id.IntegerValue, -1) for e, _ in items],
                "pos": items[0][1][3:],
            })
    return results


def main():
    if doc is None:
        forms.alert(u"请先打开一个项目。", exitscript=True)

    data = scan()
    if not data:
        output.print_md(u"## 组内重复图元")
        output.print_md(u"没有发现组内同位置重复的图元。")
        return

    total_dup = sum(x["count"] - 1 for x in data)
    rows = []
    for item in data:
        rows.append([
            output.linkify(item["group"].Id),
            item["group_name"],
            item["category"],
            item["type"],
            item["count"],
            u"{0:.0f}, {1:.0f}, {2:.0f}".format(
                float(item["pos"][0]), float(item["pos"][1]), float(item["pos"][2])),
        ])

    output.print_md(u"## 组内重复图元")
    output.print_md(u"共 {0} 组位置重复，可清理 {1} 个重复实例。".format(
        len(data), total_dup))
    output.print_table(
        rows[:200], title=u"重复清单（同一组内 类别+类型+位置 相同）",
        columns=[u"组 ID", u"组名", u"类别", u"类型", u"实例数", u"位置 (mm)"])

    choice = forms.SelectFromList.show(
        [u"删除重复，每组只留一个（Ctrl+Z 可撤销）", u"只看看，不动"],
        title=u"要清理这些重复吗？", multiselect=False)
    if not choice or not choice.startswith(u"删除"):
        return

    deleted = 0
    failed = []
    try:
        with revit.Transaction(u"清理组内重复图元"):
            for item in data:
                for eid in item["ids"][1:]:
                    try:
                        doc.Delete(eid)
                        deleted += 1
                    except Exception as ex:
                        failed.append(u"{0}：{1}".format(eid, ex))
    except Exception as ex:
        forms.alert(u"清理失败：{0}".format(ex))
        return

    output.print_md(u"### 结果")
    output.print_md(u"已删除 {0} 个重复实例。".format(deleted))
    if failed:
        output.print_md(u"失败：\n" + u"\n".join(u"- " + f for f in failed[:10]))
    output.print_md(u"\n删错了可以 Ctrl+Z；删完看「管理 → 警告」里那条是否消失。")


try:
    main()
except Exception:
    output.print_md(u"脚本出错：\n```\n{0}\n```".format(traceback.format_exc()))
    forms.alert(u"脚本出错，详见输出窗口。", title=u"组内重复图元清理")
