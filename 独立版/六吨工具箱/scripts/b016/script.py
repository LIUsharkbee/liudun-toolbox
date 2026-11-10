# -*- coding: utf-8 -*-
"""源ID 对照

读取图元上的共享参数「源ID」，与当前 ElementId 对照，导出 CSV，并统计：
  * 有源ID / 无源ID 的图元数（按类别）；
  * 重复的源ID（同一原号出现多次，说明被复制过）。

CSV 列：源ID, 当前ID, 类别, 类型, 标高
导出到桌面，UTF-8 带 BOM，Excel 直接打开不乱码。
"""

from __future__ import print_function

import codecs
import datetime
import os
import traceback

from System import Environment

from Autodesk.Revit.DB import BuiltInParameter, CategoryType, FilteredElementCollector
from pyrevit import revit, forms, script

doc = revit.doc
output = script.get_output()

PARAM_NAME = u"源ID"

try:
    text_type = unicode
except NameError:
    text_type = str


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


def elem_type_name(elem):
    type_elem = safe_call(lambda: doc.GetElement(elem.GetTypeId()), None)
    return safe_call(lambda: type_elem.Name, u"") if type_elem is not None else u""


def elem_level_name(elem):
    level_id = safe_call(lambda: elem.LevelId, None)
    if level_id is None:
        return u""
    level = safe_call(lambda: doc.GetElement(level_id), None)
    return safe_call(lambda: level.Name, u"") if level is not None else u""


def csv_cell(value):
    text = u"" if value is None else text_type(value)
    text = text.replace(u"\r", u" ").replace(u"\n", u" ")
    if u"," in text or u'"' in text:
        text = u'"' + text.replace(u'"', u'""') + u'"'
    return text


def write_csv(path, rows):
    header = [u"源ID", u"当前ID", u"类别", u"类型", u"标高", u"来源"]
    with codecs.open(path, "w", "utf-8-sig") as handle:
        handle.write(u",".join(header) + u"\r\n")
        for row in rows:
            handle.write(u",".join(csv_cell(c) for c in row) + u"\r\n")


def main():
    if doc is None:
        forms.alert(u"请先打开一个项目。", exitscript=True)

    rows = []
    stats = {}
    source_ids = {}
    total = 0
    with_param = 0
    non_id = 0

    for elem in FilteredElementCollector(doc).WhereElementIsNotElementType():
        category = safe_call(lambda e=elem: e.Category, None)
        if category is None:
            continue
        if safe_call(lambda c=category: c.CategoryType, None) != CategoryType.Model:
            continue
        param = safe_call(lambda e=elem: e.LookupParameter(PARAM_NAME), None)
        source = u"源ID"
        if param is None:
            param = safe_call(
                lambda e=elem: e.get_Parameter(BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS),
                None)
            source = u"注释"
        if param is None:
            param = safe_call(
                lambda e=elem: e.get_Parameter(BuiltInParameter.ALL_MODEL_MARK), None)
            source = u"标记"
        total += 1
        category_name = safe_call(lambda c=category: c.Name, u"") or u"?"
        entry = stats.setdefault(category_name, {"has": 0, "none": 0})
        value = u""
        if param is not None:
            value = safe_call(lambda p=param: p.AsString(), u"") or u""
        value = value.strip()
        if value and not value.isdigit():
            # 注释里可能是原有文字，不当作源 ID
            non_id += 1
            value = u""
        if value:
            with_param += 1
            entry["has"] += 1
            source_ids.setdefault(value, []).append(elem.Id.IntegerValue)
            rows.append([value, elem.Id.IntegerValue, category_name,
                         elem_type_name(elem), elem_level_name(elem), source])
        else:
            entry["none"] += 1

    if not rows:
        forms.alert(u"没有找到带「{0}」参数的图元。\n先运行「写入源ID」。".format(PARAM_NAME))
        return

    duplicates = {k: v for k, v in source_ids.items() if len(v) > 1}

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(desktop_dir(), u"源ID对照_{0}.csv".format(stamp))
    try:
        write_csv(path, rows)
    except Exception as ex:
        path = u"（写入失败：{0}）".format(ex)

    output.print_md(u"## 源ID 对照")
    output.print_md(u"模型图元 {0} 个，其中 **{1}** 个有源ID，**{2}** 个没有。".format(
        total, with_param, total - with_param))
    if non_id:
        output.print_md(u"另有 {0} 个图元的注释不是纯数字，已跳过（视为无源ID）。".format(non_id))

    summary = []
    for name in sorted(stats.keys(), key=lambda n: -(stats[n]["has"] + stats[n]["none"])):
        entry = stats[name]
        if entry["has"] == 0:
            continue
        summary.append([name, entry["has"], entry["none"]])
    if summary:
        output.print_table(
            summary[:40], title=u"按类别统计（只列有源ID的类别）",
            columns=[u"类别", u"有源ID", u"无源ID"])

    if duplicates:
        output.print_md(u"\n**重复源ID {0} 个**（同一原号对应多个当前图元，通常是被复制过）：".format(
            len(duplicates)))
        preview = list(duplicates.items())[:10]
        for key, ids in preview:
            output.print_md(u"- 源ID `{0}` → 当前 {1}".format(
                key, u"、".join(str(i) for i in ids)))
    else:
        output.print_md(u"\n没有重复的源ID，对照关系是一对一的。")

    output.print_md(u"\n**CSV 已导出：** `{0}`（{1} 行）".format(path, len(rows)))


try:
    main()
except Exception:
    output.print_md(u"脚本出错：\n```\n{0}\n```".format(traceback.format_exc()))
    forms.alert(u"脚本出错，详见输出窗口。", title=u"源ID 对照")
