# -*- coding: utf-8 -*-
"""族草图体检

针对警告：“草图-中的线稍微偏离了轴，可能会导致不精确。”

做法：
  * 读取当前项目实施中的所有警告，挑出描述里同时含“偏离”和“轴”的；
  * 把警告点名的图元解析成项目里的族实例，按「族名 / 类型名」汇总；
  * 输出每个族涉及多少实例、示例 ID，并导出 CSV。

这类警告来自**族内部的拉伸草图**，修法是在族编辑器里把草图线对齐/重画，
改一次族，项目里所有实例一起生效。

只读，不改模型。
"""

from __future__ import print_function

import codecs
import datetime
import os
import traceback

from System import Environment

from Autodesk.Revit.DB import (
    BuiltInParameter,
    Element,
    FamilyInstance,
)
from pyrevit import revit, forms, script

doc = revit.doc
output = script.get_output()


def safe_call(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def desktop_dir():
    def _get():
        path = Environment.GetFolderPath(Environment.SpecialFolder.Desktop)
        return path if path and os.path.isdir(path) else u""
    return safe_call(_get, u"") or safe_call(lambda: os.path.expanduser(u"~"), u".")


def family_and_type(instance):
    sym = safe_call(lambda: instance.Symbol, None)
    if sym is not None:
        fam = safe_call(lambda: sym.Family.Name, u"")
        typ = safe_call(lambda: sym.Name, u"")
        return (fam or u"?", typ or u"?")
    fam = safe_call(
        lambda: instance.get_Parameter(
            BuiltInParameter.ELEM_FAMILY_PARAM).AsValueString(), u"")
    typ = safe_call(
        lambda: instance.get_Parameter(
            BuiltInParameter.ELEM_TYPE_PARAM).AsValueString(), u"")
    return (fam or u"?", typ or u"?")


def sketch_warnings():
    """返回 (匹配列表, 全部描述列表)，匹配“偏离轴”类。"""
    all_descs = []
    matched = []
    for warning in safe_call(lambda: list(doc.GetWarnings()), []):
        desc = safe_call(lambda w=warning: w.Description, u"") or u""
        all_descs.append(desc)
        if u"偏离" in desc and u"轴" in desc:
            matched.append((warning, desc))

    if not matched:
        # 放宽：只要提到“偏离”或“草图 + 轴”
        for warning, desc in [(w, safe_call(lambda w=w: w.Description, u"") or u"")
                              for w in safe_call(lambda: list(doc.GetWarnings()), [])]:
            if u"偏离" in desc or (u"草图" in desc and u"轴" in desc):
                matched.append((warning, desc))

    return matched, all_descs


def csv_cell(value):
    text = u"" if value is None else u"{0}".format(value)
    text = text.replace(u"\r", u" ").replace(u"\n", u" ")
    if u"," in text or u'"' in text:
        text = u'"' + text.replace(u'"', u'""') + u'"'
    return text


def write_csv(path, rows):
    header = [u"族名", u"类型名", u"实例数", u"示例实例ID", u"族内图元数"]
    with codecs.open(path, "w", "utf-8-sig") as handle:
        handle.write(u",".join(header) + u"\r\n")
        for row in rows:
            handle.write(u",".join(csv_cell(c) for c in row) + u"\r\n")


def main():
    if doc is None:
        forms.alert(u"请先打开一个项目。", exitscript=True)

    warnings, all_descs = sketch_warnings()
    if not warnings:
        output.print_md(u"## 族草图体检（未匹配）")
        output.print_md(u"实施中的警告总数：**{0}**".format(len(all_descs)))
        if all_descs:
            output.print_md(u"\n前 15 条警告原文（用于核对用词）：")
            for desc in all_descs[:15]:
                output.print_md(u"- {0}".format((desc or u"").replace(u"\n", u" ")[:80]))
        else:
            output.print_md(u"`GetWarnings()` 返回空——可能当前没有未处理的警告，"
                            u"或警告已被清空。")
        output.print_md(u"\n如果上面出现过“偏离轴”字样，请把它原样发我，我按实际文字匹配。")
        return

    groups = {}
    unresolved = 0
    warning_count = len(warnings)

    for warning, desc in warnings:
        ids = safe_call(lambda w=warning: list(w.GetFailingElements()), [])
        for eid in ids:
            elem = safe_call(lambda e=eid: doc.GetElement(e), None)
            if elem is None:
                # 族内部图元（草图线、拉伸）在项目里取不到
                unresolved += 1
                continue
            if not isinstance(elem, FamilyInstance):
                continue
            key = family_and_type(elem)
            entry = groups.setdefault(key, {"ids": [], "unresolved": 0})
            entry["ids"].append(elem.Id.IntegerValue)

    rows = []
    for (fam, typ), entry in groups.items():
        ids = sorted(set(entry["ids"]))
        rows.append([
            fam, typ, len(ids),
            u"、".join(str(i) for i in ids[:5]),
            warning_count,
        ])
    rows.sort(key=lambda r: -r[2])

    output.print_md(u"## 族草图体检")
    output.print_md(u"共 **{0}** 条“草图线偏离轴”警告，"
                    u"归入 **{1}** 个族类型；族内图元（草图线/拉伸）"
                    u"无法在项目中解析的有 {2} 个。".format(
                        warning_count, len(rows), unresolved))

    if rows:
        table = [[r[0], r[1], r[2], r[3]] for r in rows[:60]]
        output.print_table(
            table, title=u"按族 / 类型汇总（按实例数排序）",
            columns=[u"族名", u"类型名", u"实例数", u"示例实例 ID"])
        output.print_md(u"\n修法：选中任一实例 → **编辑族** → 找到该拉伸 → "
                        u"编辑草图 → 用「对齐」把线拉回轴线，或删掉重画（开约束）；"
                        u"载入项目覆盖后，所有实例一起生效。")
    else:
        output.print_md(u"警告里的图元都是族内部图元，没有解析到项目实例。"
                        u"请按警告树里的族名手工定位。")

    path = os.path.join(
        desktop_dir(),
        u"族草图体检_{0}.csv".format(
            datetime.datetime.now().strftime("%Y%m%d_%H%M%S")))
    try:
        write_csv(path, rows)
        output.print_md(u"\nCSV 已导出：`{0}`（{1} 行）".format(path, len(rows)))
    except Exception as ex:
        output.print_md(u"\nCSV 导出失败：{0}".format(ex))


try:
    main()
except Exception:
    output.print_md(u"脚本出错：\n```\n{0}\n```".format(traceback.format_exc()))
    forms.alert(u"脚本出错，详见输出窗口。", title=u"族草图体检")
