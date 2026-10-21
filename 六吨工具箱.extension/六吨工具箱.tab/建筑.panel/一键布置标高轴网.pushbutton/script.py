# -*- coding: utf-8 -*-
"""一键布置标高轴网

两种输入方式：
  1. 参数生成：依次输入标高序列、X/Y 轴距、轴号前缀；
  2. 从 CSV 导入：一张表同时给标高、轴号、坐标 / 间距。

两种方式都支持：
  * 指定轴网起点（局部坐标原点）；
  * 轴网旋转角度（度，逆时针为正），用于斜轴网。

CSV 格式（四列，逗号 / 制表符分隔，可带表头）：

    类型,名称,坐标mm,间距mm
    标高,±0.000,0,
    标高,+3.600,3600,
    X,1,0,
    X,2,6000,
    Y,A,0,
    Y,B,,8000

  * 类型：标高 / X（竖向轴线）/ Y（横向轴线），也认 level / x / y；
  * 名称：留空则自动命名（标高按 ±0.000 格式，轴线按 1、2、A、B…）；
  * 坐标：该轴线的局部坐标（相对起点），留空则用“间距”累加；
  * 间距：与坐标二选一。

生成前会跳过已存在的同标高 / 同名轴号，不会重复创建；全部在一个事务里。
"""

from __future__ import print_function

import codecs
import math
import traceback

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
MARGIN = 1000.0            # 轴网端头外挑，mm


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


def spec_from_params(ask):
    elevations = parse_numbers(ask(
        default=u"0, 3600, 7200, 10800",
        prompt=u"标高（mm，逗号分隔，从下往上）：",
        title=u"一键布置标高轴网 - 标高"))
    if not elevations:
        forms.alert(u"没有解析到标高值。")
        return None

    x_gaps = parse_numbers(ask(
        default=u"6000, 6000, 6000",
        prompt=u"X 向轴距（mm；留空则跳过 X 轴）：",
        title=u"一键布置标高轴网 - X 向"))
    y_gaps = parse_numbers(ask(
        default=u"8000, 8000",
        prompt=u"Y 向轴距（mm；留空则跳过 Y 轴）：",
        title=u"一键布置标高轴网 - Y 向"))

    prefixes = ask(
        default=u"1; A",
        prompt=u"轴号前缀（X;Y，用分号分隔，例如 1;A）：",
        title=u"一键布置标高轴网 - 轴号") or u"1; A"
    parts = prefixes.replace(u"，", u";").replace(u",", u";").split(u";")
    x_prefix = parts[0].strip() if parts else u"1"
    y_prefix = parts[1].strip() if len(parts) > 1 else u"A"

    x_lines = []
    coord = 0.0
    for index in range(len(x_gaps) + 1):
        x_lines.append((u"{0}{1}".format(x_prefix, index + 1), coord))
        if index < len(x_gaps):
            coord += x_gaps[index]

    y_lines = []
    coord = 0.0
    for index in range(len(y_gaps) + 1):
        y_lines.append((u"{0}{1}".format(y_prefix, letter_name(index + 1)), coord))
        if index < len(y_gaps):
            coord += y_gaps[index]

    return {"levels": [(None, z) for z in elevations],
            "x": x_lines, "y": y_lines}


def rotate(x_mm, y_mm, angle_deg):
    rad = math.radians(angle_deg)
    cos_a = math.cos(rad)
    sin_a = math.sin(rad)
    return (x_mm * cos_a - y_mm * sin_a, x_mm * sin_a + y_mm * cos_a)


def to_xyz(x_mm, y_mm, z_mm, origin, angle):
    x, y = rotate(x_mm, y_mm, angle)
    return XYZ((x + origin[0]) / FT_TO_MM, (y + origin[1]) / FT_TO_MM, z_mm / FT_TO_MM)


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

    ask = safe_call(lambda: forms.ask_for_string, None)
    if ask is None:
        forms.alert(u"当前 pyRevit 不支持输入框。", exitscript=True)

    mode = forms.CommandSwitchWindow.show(
        [u"参数生成（标高序列 + 轴距）", u"从 CSV 导入"],
        message=u"一键布置标高轴网：选择输入方式")
    if not mode:
        return

    origin_text = ask(default=u"0, 0",
                      prompt=u"轴网起点坐标 X,Y（mm）：",
                      title=u"起点")
    origin_nums = parse_numbers(origin_text) or [0.0, 0.0]
    origin = (origin_nums[0],
              origin_nums[1] if len(origin_nums) > 1 else 0.0)

    angle_text = ask(default=u"0",
                     prompt=u"轴网旋转角度（度，逆时针为正，0 表示正交）：",
                     title=u"旋转角度")
    angle_nums = parse_numbers(angle_text)
    angle = angle_nums[0] if angle_nums else 0.0

    if mode.startswith(u"从 CSV"):
        picker = safe_call(lambda: forms.pick_file, None)
        if picker is None:
            forms.alert(u"当前 pyRevit 不支持选文件。")
            return
        path = picker(file_ext="csv")
        if not path:
            return
        spec = spec_from_csv(path)
        if not spec["levels"] and not spec["x"] and not spec["y"]:
            forms.alert(u"CSV 里没有解析到有效数据。\n需要四列：类型,名称,坐标mm,间距mm")
            return
    else:
        spec = spec_from_params(ask)
        if spec is None:
            return

    make_views = forms.SelectFromList.show(
        [u"同时创建楼层平面视图", u"只建标高和轴网"],
        title=u"要为每个新标高建楼层平面吗？",
        multiselect=False) or u"只建标高和轴网"
    want_views = make_views.startswith(u"同时")

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
    skipped_levels = []
    failed = []
    created_views = 0
    plan_type = floor_plan_type_id() if want_views else None

    xs = [c for _, c in spec["x"]]
    ys = [c for _, c in spec["y"]]
    z_base = 0.0

    with revit.Transaction(u"一键布置标高轴网"):
        for name, mm in spec["levels"]:
            key = round(mm)
            if key in existing:
                skipped_levels.append(u"{0}（已有 {1}）".format(
                    format_level_name(mm), existing[key]))
                continue
            try:
                level = Level.Create(doc, mm / FT_TO_MM)
                level.Name = unique_name(name or format_level_name(mm),
                                         used_level_names)
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

        if want_views and plan_type is not None:
            for level in created_levels:
                try:
                    ViewPlan.Create(doc, plan_type, level.Id)
                    created_views += 1
                except Exception as ex:
                    failed.append(u"视图 {0}：{1}".format(level.Name, ex))

    output.print_md(u"## 一键布置标高轴网")
    output.print_md(u"起点 ({0:.0f}, {1:.0f}) mm，旋转 {2:.2f}°".format(
        origin[0], origin[1], angle))
    output.print_md(u"新建标高 **{0}** 个、轴网 **{1}** 条、楼层平面 **{2}** 个。".format(
        len(created_levels), len(created_grids), created_views))
    if skipped_levels:
        output.print_md(u"跳过已存在的标高：" + u"、".join(skipped_levels))
    if created_levels:
        output.print_md(u"标高：" + u"、".join(
            safe_call(lambda l=l: l.Name, u"") for l in created_levels))
    if created_grids:
        output.print_md(u"轴网：" + u"、".join(
            safe_call(lambda g=g: g.Name, u"") for g in created_grids))
    if failed:
        output.print_md(u"失败：\n" + u"\n".join(u"- " + f for f in failed))
    output.print_md(u"\n一次事务提交，改错了可以 Ctrl+Z 整体撤销。")


try:
    main()
except Exception:
    output.print_md(u"脚本出错：\n```\n{0}\n```".format(traceback.format_exc()))
    forms.alert(u"脚本出错，详见输出窗口。", title=u"一键布置标高轴网")
