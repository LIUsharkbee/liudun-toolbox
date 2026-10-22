# -*- coding: utf-8 -*-
"""楼板厚度体检

针对警告：“由于对形状进行了过度的编辑，此楼板的厚度可能不太精确。”

做法：对每块楼板比较
  * 类型厚度：楼板类型「结构」里各层宽度之和；
  * 实际平均厚度：计算体积 / 计算面积（对坡面、形状编辑过的板同样成立）。

偏差超过阈值（默认 5 mm）的板会被列出，并标注它是否被 Revit 警告点名。

只读，不改模型。CSV 导出到桌面。
"""

from __future__ import print_function

import codecs
import datetime
import os
import traceback

from System import Environment

from Autodesk.Revit.DB import (
    BuiltInParameter,
    FilteredElementCollector,
    Floor,
)
from pyrevit import revit, forms, script

doc = revit.doc
output = script.get_output()

FT_TO_MM = 304.8
FT3_TO_M3 = 0.3048 ** 3
FT2_TO_M2 = 0.3048 ** 2


def safe_call(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def bip(name):
    return getattr(BuiltInParameter, name, None)


def desktop_dir():
    def _get():
        path = Environment.GetFolderPath(Environment.SpecialFolder.Desktop)
        return path if path and os.path.isdir(path) else u""
    return safe_call(_get, u"") or safe_call(lambda: os.path.expanduser(u"~"), u".")


def type_name_of(floor):
    type_elem = safe_call(lambda: doc.GetElement(floor.GetTypeId()), None)
    return safe_call(lambda: type_elem.Name, u"") if type_elem is not None else u""


def level_name_of(floor):
    level = safe_call(lambda: doc.GetElement(floor.LevelId), None)
    return safe_call(lambda: level.Name, u"") if level is not None else u""


def type_thickness_mm(floor):
    """类型厚度 = 复合结构各层宽度之和（mm）。"""
    type_elem = safe_call(lambda: doc.GetElement(floor.GetTypeId()), None)
    if type_elem is None:
        return None
    cs = safe_call(lambda: type_elem.GetCompoundStructure(), None)
    if cs is None:
        return None
    layers = safe_call(lambda: list(cs.GetLayers()), [])
    total = 0.0
    for layer in layers:
        total += safe_call(lambda l=layer: l.Width, 0.0) or 0.0
    return total * FT_TO_MM if total > 0 else None


def computed_number(floor, param_name):
    key = bip(param_name)
    if key is None:
        return None
    param = safe_call(lambda: floor.get_Parameter(key), None)
    if param is None:
        return None
    return safe_call(lambda: param.AsDouble(), None)


def warned_floor_ids():
    """被“厚度可能不太精确”类警告点名的楼板 ID。"""
    ids = set()
    for warning in safe_call(lambda: list(doc.GetWarnings()), []):
        desc = safe_call(lambda w=warning: w.Description, u"") or u""
        if u"厚度" not in desc:
            continue
        if u"不精确" not in desc and u"过度编辑" not in desc and u"过度" not in desc:
            continue
        for eid in safe_call(lambda w=warning: list(w.GetFailingElements()), []):
            ids.add(eid.IntegerValue)
    return ids


def csv_cell(value):
    text = u"" if value is None else u"{0}".format(value)
    text = text.replace(u"\r", u" ").replace(u"\n", u" ")
    if u"," in text or u'"' in text:
        text = u'"' + text.replace(u'"', u'""') + u'"'
    return text


def write_csv(path, rows):
    header = [u"ID", u"类型", u"标高", u"类型厚度mm", u"实际平均厚度mm",
              u"偏差mm", u"面积m2", u"体积m3", u"被警告点名"]
    with codecs.open(path, "w", "utf-8-sig") as handle:
        handle.write(u",".join(header) + u"\r\n")
        for row in rows:
            handle.write(u",".join(csv_cell(c) for c in row) + u"\r\n")


def main():
    if doc is None:
        forms.alert(u"请先打开一个项目。", exitscript=True)

    ask = safe_call(lambda: forms.ask_for_string, None)
    threshold = 5.0
    if ask is not None:
        text = ask(default=u"5", prompt=u"厚度偏差阈值（mm，超过则列出）：",
                    title=u"楼板厚度体检")
        if text is None:
            return
        try:
            threshold = float(text.strip() or u"5")
        except ValueError:
            threshold = 5.0

    warned = warned_floor_ids()

    rows = []
    records = []
    no_type_thickness = 0
    floors = safe_call(
        lambda: list(FilteredElementCollector(doc).OfClass(Floor)), [])

    for floor in floors:
        type_mm = type_thickness_mm(floor)
        volume = computed_number(floor, u"HOST_VOLUME_COMPUTED")
        area = computed_number(floor, u"HOST_AREA_COMPUTED")

        actual_mm = None
        if volume and area and area > 0:
            actual_mm = (volume / area) * FT_TO_MM

        if type_mm is None:
            no_type_thickness += 1
            continue

        diff = None
        if actual_mm is not None:
            diff = actual_mm - type_mm

        flagged = floor.Id.IntegerValue in warned
        records.append({
            "floor": floor,
            "eid": floor.Id.IntegerValue,
            "type": type_name_of(floor),
            "level": level_name_of(floor),
            "type_mm": type_mm,
            "actual_mm": actual_mm,
            "diff": diff,
            "volume": volume,
            "area": area,
            "warned": flagged,
        })

    for rec in records:
        is_bad = rec["warned"]
        if rec["diff"] is not None and abs(rec["diff"]) > threshold:
            is_bad = True
        if is_bad:
            rows.append(rec)

    rows.sort(key=lambda r: -abs(r["diff"]) if r["diff"] is not None else 0)

    output.print_md(u"## 楼板厚度体检")
    output.print_md(u"共检查 **{0}** 块楼板，阈值 **{1:.1f} mm**；"
                    u"超出阈值或被警告点名 **{2}** 块。".format(
                        len(records), threshold, len(rows)))
    if no_type_thickness:
        output.print_md(u"另有 {0} 块楼板取不到类型厚度（非复合结构），已跳过。".format(
            no_type_thickness))

    table = []
    for rec in rows[:60]:
        table.append([
            output.linkify(rec["floor"].Id),
            rec["type"],
            rec["level"],
            u"{0:.0f}".format(rec["type_mm"]) if rec["type_mm"] is not None else u"?",
            u"{0:.1f}".format(rec["actual_mm"]) if rec["actual_mm"] is not None else u"?",
            u"{0:+.1f}".format(rec["diff"]) if rec["diff"] is not None else u"?",
            u"{0:.1f}".format((rec["area"] or 0.0) * FT2_TO_M2),
            u"{0:.2f}".format((rec["volume"] or 0.0) * FT3_TO_M3),
            u"是" if rec["warned"] else u"",
        ])
    if table:
        output.print_table(
            table, title=u"厚度异常的楼板（按偏差绝对值排序，最多 60 行）",
            columns=[u"ID", u"类型", u"标高", u"类型厚度(mm)", u"实际平均(mm)",
                     u"偏差(mm)", u"面积(m²)", u"体积(m³)", u"被警告点名"])

    # CSV
    csv_rows = []
    for rec in rows:
        csv_rows.append([
            rec["eid"], rec["type"], rec["level"],
            u"{0:.1f}".format(rec["type_mm"]) if rec["type_mm"] is not None else u"",
            u"{0:.1f}".format(rec["actual_mm"]) if rec["actual_mm"] is not None else u"",
            u"{0:+.1f}".format(rec["diff"]) if rec["diff"] is not None else u"",
            u"{0:.2f}".format((rec["area"] or 0.0) * FT2_TO_M2),
            u"{0:.3f}".format((rec["volume"] or 0.0) * FT3_TO_M3),
            u"是" if rec["warned"] else u"",
        ])
    path = os.path.join(
        desktop_dir(),
        u"楼板厚度体检_{0}.csv".format(datetime.datetime.now().strftime("%Y%m%d_%H%M%S")))
    try:
        write_csv(path, csv_rows)
        output.print_md(u"\nCSV 已导出：`{0}`（{1} 行）".format(path, len(csv_rows)))
    except Exception as ex:
        output.print_md(u"\nCSV 导出失败：{0}".format(ex))

    output.print_md(u"\n“实际平均厚度”= 计算体积 ÷ 计算面积，对坡面同样成立；"
                    u"偏差大说明这块板被形状编辑改过，厚度标注别直接引用类型厚度。")


try:
    main()
except Exception:
    output.print_md(u"脚本出错：\n```\n{0}\n```".format(traceback.format_exc()))
    forms.alert(u"脚本出错，详见输出窗口。", title=u"楼板厚度体检")
