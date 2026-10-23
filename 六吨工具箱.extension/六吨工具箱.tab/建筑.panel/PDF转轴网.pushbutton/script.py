# -*- coding: utf-8 -*-
"""PDF 转轴网

在 Revit 里直接选一张 **矢量 PDF** 图纸，自动调用外部转换器提取轴号/标高，
生成 CSV 后再按 CSV 的方式生成标高与轴网。

依赖：
  %LOCALAPPDATA%\\liudun-pdf\\pdf_to_csv.py   （PyMuPDF 转换器）
  %LOCALAPPDATA%\\liudun-pdf\\lib              （PyMuPDF 库）

只处理“有文字层”的 PDF；扫描件/图片型 PDF 会提示无法识别。
"""

from __future__ import print_function

import codecs
import math
import os
import re
import tempfile
import traceback

from System.Diagnostics import Process, ProcessStartInfo

from Autodesk.Revit.DB import (
    FilteredElementCollector,
    Grid,
    Level,
    Line,
    ViewFamily,
    ViewFamilyType,
    ViewPlan,
    XYZ,
)
from pyrevit import revit, forms, script

doc = revit.doc
output = script.get_output()

FT_TO_MM = 304.8
MARGIN = 1000.0

TOOL_DIR = os.path.join(os.environ.get("LOCALAPPDATA", ""), "liudun-pdf")
CONVERTER = os.path.join(TOOL_DIR, "pdf_to_csv.py")
PYTHON_CANDIDATES = [
    r"C:\Users\cyl\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe",
    "python",
]


def safe_call(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def parse_numbers(text):
    if not text:
        return []
    cleaned = text
    for sep in (u"，", u"、", u";", u"\t", u"\r", u"\n"):
        cleaned = cleaned.replace(sep, u" ")
    cleaned = cleaned.replace(u",", u" ")
    out = []
    for token in cleaned.split():
        try:
            out.append(float(token))
        except ValueError:
            continue
    return out


def format_level_name(mm):
    if abs(mm) < 0.5:
        return u"±0.000"
    sign = u"+" if mm > 0 else u"-"
    return u"{0}{1:.3f}".format(sign, abs(mm) / 1000.0)


def letter_name(index):
    name = u""
    n = index
    while n > 0:
        n, rem = divmod(n - 1, 26)
        name = chr(65 + rem) + name
    return name


def unique_name(base, used):
    if base not in used:
        used.add(base)
        return base
    index = 2
    while u"{0}-{1}".format(base, index) in used:
        index += 1
    name = u"{0}-{1}".format(base, index)
    used.add(name)
    return name


def find_python():
    for candidate in PYTHON_CANDIDATES:
        if candidate == "python":
            return candidate
        if os.path.isfile(candidate):
            return candidate
    return "python"


def run_converter(pdf_path, csv_path, scale):
    """调用外部 Python 转换 PDF，返回 (成功, 输出文本)。"""
    if not os.path.isfile(CONVERTER):
        return False, u"未找到转换器：{0}".format(CONVERTER)

    info = ProcessStartInfo()
    info.FileName = find_python()
    info.Arguments = '"{0}" "{1}" "{2}" {3}'.format(
        CONVERTER, pdf_path, csv_path, scale)
    info.UseShellExecute = False
    info.CreateNoWindow = True
    info.RedirectStandardOutput = True
    info.RedirectStandardError = True
    info.StandardOutputEncoding = None
    try:
        process = Process.Start(info)
        text = process.StandardOutput.ReadToEnd()
        error = process.StandardError.ReadToEnd()
        process.WaitForExit(180000)
        code = process.ExitCode
        return (code == 0), (text or u"") + (error or u"")
    except Exception as ex:
        return False, u"{0}".format(ex)


def read_csv_rows(path):
    text = None
    for encoding in ("utf-8-sig", "gbk", "utf-8"):
        try:
            with codecs.open(path, "r", encoding) as handle:
                text = handle.read()
            break
        except Exception:
            continue
    if text is None:
        return []
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        cells = line.replace(u";", u",").replace(u"\t", u",").split(u",")
        rows.append([c.strip() for c in cells])
    return rows


def spec_from_csv(path):
    rows = read_csv_rows(path)
    levels = []
    x_lines = []
    y_lines = []
    auto_x = 0
    auto_y = 0
    last_x = None
    last_y = None

    for cells in rows:
        if not cells or not cells[0]:
            continue
        kind = cells[0].strip().lower()
        name = cells[1].strip() if len(cells) > 1 else u""
        coord_text = cells[2].strip() if len(cells) > 2 else u""
        gap_text = cells[3].strip() if len(cells) > 3 else u""

        if kind in (u"类型", u"type", u"分类"):
            continue

        coord = None
        if coord_text:
            try:
                coord = float(coord_text)
            except ValueError:
                coord = None

        if kind in (u"标高", u"level", u"l"):
            if coord is None:
                continue
            levels.append((name or None, coord))
        elif kind in (u"x", u"纵", u"竖向"):
            if coord is None and gap_text:
                gap = safe_call(lambda: float(gap_text), None)
                if gap is not None:
                    coord = (last_x if last_x is not None else 0.0) + gap
            if coord is None and last_x is None:
                coord = 0.0
            if coord is None:
                continue
            last_x = coord
            auto_x += 1
            x_lines.append((name or u"X{0}".format(auto_x), coord))
        elif kind in (u"y", u"横", u"横向"):
            if coord is None and gap_text:
                gap = safe_call(lambda: float(gap_text), None)
                if gap is not None:
                    coord = (last_y if last_y is not None else 0.0) + gap
            if coord is None and last_y is None:
                coord = 0.0
            if coord is None:
                continue
            last_y = coord
            auto_y += 1
            y_lines.append((name or letter_name(auto_y), coord))

    return {"levels": levels, "x": x_lines, "y": y_lines}


def rotate(x_mm, y_mm, angle_deg):
    rad = math.radians(angle_deg)
    cos_a = math.cos(rad)
    sin_a = math.sin(rad)
    return (x_mm * cos_a - y_mm * sin_a, x_mm * sin_a + y_mm * cos_a)


def to_xyz(x_mm, y_mm, z_mm, origin, angle):
    x, y = rotate(x_mm, y_mm, angle)
    return XYZ((x + origin[0]) / FT_TO_MM,
               (y + origin[1]) / FT_TO_MM,
               z_mm / FT_TO_MM)


def floor_plan_type_id():
    types = safe_call(
        lambda: list(FilteredElementCollector(doc).OfClass(ViewFamilyType)), [])
    for item in types:
        if safe_call(lambda item=item: item.ViewFamily, None) == ViewFamily.FloorPlan:
            return item.Id
    return None


def main():
    if doc is None:
        forms.alert(u"请先打开一个项目。", exitscript=True)

    if not os.path.isfile(CONVERTER):
        forms.alert(u"未找到 PDF 转换器：\n{0}\n\n"
                    u"请先运行过一次「liudun-pdf」工具的安装。".format(CONVERTER))
        return

    picker = safe_call(lambda: forms.pick_file, None)
    if picker is None:
        forms.alert(u"当前 pyRevit 不支持选文件。")
        return
    pdf_path = picker(file_ext="pdf")
    if not pdf_path:
        return

    scale_text = forms.ask_for_string(
        default=u"100",
        prompt=u"图纸比例（1:100 填 100；1:50 填 50）：",
        title=u"PDF 转轴网")
    if scale_text is None:
        return
    scale = safe_call(lambda: float((scale_text or u"100").strip()), 100.0) or 100.0

    temp_dir = safe_call(lambda: tempfile.mkdtemp(prefix="ld_pdf_"), None)
    if not temp_dir:
        forms.alert(u"无法创建临时目录。")
        return
    csv_path = os.path.join(temp_dir, "extracted.csv")

    output.print_md(u"## PDF 转轴网")
    output.print_md(u"正在解析：`{0}`（比例 1:{1:.0f}）…".format(pdf_path, scale))

    ok, log_text = run_converter(pdf_path, csv_path, scale)
    if log_text:
        output.print_md(u"```\n{0}\n```".format(log_text[-1500:]))
    if not ok or not os.path.isfile(csv_path):
        forms.alert(u"转换失败（这份 PDF 可能没有矢量文字层）。\n详见输出窗口。",
                    title=u"PDF 转轴网")
        return

    spec = spec_from_csv(csv_path)
    if not spec["levels"] and not spec["x"] and not spec["y"]:
        forms.alert(u"CSV 里没有解析到轴号或标高。", title=u"PDF 转轴网")
        return

    output.print_md(u"识别结果：标高 **{0}** 个、X 轴 **{1}** 条、Y 轴 **{2}** 条。".format(
        len(spec["levels"]), len(spec["x"]), len(spec["y"])))
    if spec["x"]:
        output.print_md(u"X 轴：{0}".format(
            u"、".join(n for n, _ in spec["x"])))
    if spec["y"]:
        output.print_md(u"Y 轴：{0}".format(
            u"、".join(n for n, _ in spec["y"])))

    answer = forms.alert(
        u"识别到 标高 {0} 个、X 轴 {1} 条、Y 轴 {2} 条。\n\n"
        u"直接在本项目里生成吗？（一次事务，可 Ctrl+Z 撤销）".format(
            len(spec["levels"]), len(spec["x"]), len(spec["y"])),
        title=u"PDF 转轴网", yes=True, no=True)
    if answer not in (True, u"Yes", u"yes", u"是"):
        output.print_md(u"\nCSV 已生成：`{0}`".format(csv_path))
        return

    origin_text = forms.ask_for_string(
        default=u"0, 0", prompt=u"轴网起点 X,Y（mm）：", title=u"起点")
    origin_nums = parse_numbers(origin_text or u"") or [0.0, 0.0]
    origin = (origin_nums[0],
              origin_nums[1] if len(origin_nums) > 1 else 0.0)

    angle_text = forms.ask_for_string(
        default=u"0", prompt=u"轴网旋转角度（度，逆时针为正）：", title=u"旋转")
    angle_nums = parse_numbers(angle_text or u"")
    angle = angle_nums[0] if angle_nums else 0.0

    existing = {}
    for level in safe_call(lambda: list(FilteredElementCollector(doc).OfClass(Level)), []):
        z = safe_call(lambda l=level: l.Elevation, None)
        if z is not None:
            existing[round(z * FT_TO_MM)] = safe_call(lambda l=level: l.Name, u"")

    used_level_names = set(existing.values())
    used_grid_names = set(
        safe_call(lambda g=g: g.Name, u"")
        for g in safe_call(lambda: list(FilteredElementCollector(doc).OfClass(Grid)), []))

    created_levels = []
    created_grids = []
    skipped = []
    failed = []

    xs = [c for _, c in spec["x"]]
    ys = [c for _, c in spec["y"]]
    z_base = 0.0

    with revit.Transaction(u"PDF 转轴网"):
        for name, mm in spec["levels"]:
            key = round(mm)
            if key in existing:
                skipped.append(format_level_name(mm))
                continue
            try:
                level = Level.Create(doc, mm / FT_TO_MM)
                level.Name = unique_name(
                    name or format_level_name(mm), used_level_names)
                created_levels.append(level)
                existing[key] = level.Name
            except Exception as ex:
                failed.append(u"标高 {0}：{1}".format(format_level_name(mm), ex))

        if xs:
            y0 = (min(ys) if ys else 0.0) - MARGIN
            y1 = (max(ys) if ys else 0.0) + MARGIN
            for name, coord in spec["x"]:
                try:
                    line = Line.CreateBound(
                        to_xyz(coord, y0, z_base, origin, angle),
                        to_xyz(coord, y1, z_base, origin, angle))
                    grid = Grid.Create(doc, line)
                    grid.Name = unique_name(name, used_grid_names)
                    created_grids.append(grid)
                except Exception as ex:
                    failed.append(u"轴线 {0}：{1}".format(name, ex))

        if ys:
            x0 = (min(xs) if xs else 0.0) - MARGIN
            x1 = (max(xs) if xs else 0.0) + MARGIN
            for name, coord in spec["y"]:
                try:
                    line = Line.CreateBound(
                        to_xyz(x0, coord, z_base, origin, angle),
                        to_xyz(x1, coord, z_base, origin, angle))
                    grid = Grid.Create(doc, line)
                    grid.Name = unique_name(name, used_grid_names)
                    created_grids.append(grid)
                except Exception as ex:
                    failed.append(u"轴线 {0}：{1}".format(name, ex))

    output.print_md(u"\n### 结果")
    output.print_md(u"新建标高 **{0}** 个、轴网 **{1}** 条。".format(
        len(created_levels), len(created_grids)))
    if skipped:
        output.print_md(u"跳过已存在的标高：" + u"、".join(skipped))
    if failed:
        output.print_md(u"失败：\n" + u"\n".join(u"- " + f for f in failed))
    output.print_md(u"\n按 **ZF** 缩放到全部即可看到。CSV 留在：`{0}`".format(csv_path))


try:
    main()
except Exception:
    output.print_md(u"脚本出错：\n```\n{0}\n```".format(traceback.format_exc()))
    forms.alert(u"脚本出错，详见输出窗口。", title=u"PDF 转轴网")
