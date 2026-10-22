# -*- coding: utf-8 -*-
"""一键布置标高轴网（单窗口界面）

全部选项集中在一个窗口里：

  * 输入方式：参数生成 / 从 CSV 导入（切到 CSV 时自动禁用参数区）
  * 标高序列、X/Y 向轴距、X/Y 轴号前缀
  * 起点坐标、旋转角度
  * 是否同时创建楼层平面视图
  * 实时预览：点「预览」先看要建什么，再点「确定」

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

import clr

clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")

from System.Windows import (
    CornerRadius,
    FontWeights,
    HorizontalAlignment,
    MessageBox,
    ResizeMode,
    TextWrapping,
    Thickness,
    VerticalAlignment,
    Window,
    WindowStartupLocation,
)
from System.Windows.Controls import (
    Border,
    Button,
    CheckBox,
    ComboBox,
    ComboBoxItem,
    Label,
    Orientation,
    ScrollBarVisibility,
    StackPanel,
    TextBox,
)
from System.Windows.Media import Brushes, FontFamily

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
SHELL_XAML = u"""
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="一键布置标高轴网" Width="580" Height="680"
        WindowStartupLocation="CenterScreen" ResizeMode="NoResize"
        ShowInTaskbar="False"/>
"""
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


def spec_from_params(levels_text, x_gaps_text, y_gaps_text, x_prefix, y_prefix):
    elevations = parse_numbers(levels_text)
    x_gaps = parse_numbers(x_gaps_text)
    y_gaps = parse_numbers(y_gaps_text)

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


def describe_spec(spec, origin, angle, want_views):
    lines = []
    levels = spec["levels"]
    xs = spec["x"]
    ys = spec["y"]
    if levels:
        names = [format_level_name(mm) for _, mm in levels]
        lines.append(u"标高 {0} 个：{1}".format(len(levels), u"、".join(names)))
    else:
        lines.append(u"标高 0 个")
    if xs:
        lines.append(u"X 轴 {0} 条：{1}".format(
            len(xs), u"、".join(n for n, _ in xs)))
    else:
        lines.append(u"X 轴 0 条")
    if ys:
        lines.append(u"Y 轴 {0} 条：{1}".format(
            len(ys), u"、".join(n for n, _ in ys)))
    else:
        lines.append(u"Y 轴 0 条")
    lines.append(u"起点 ({0:.0f}, {1:.0f}) mm，旋转 {2:.2f}°".format(
        origin[0], origin[1], angle))
    lines.append(u"创建楼层平面：{0}".format(u"是" if want_views else u"否"))
    return u"\n".join(lines)


class SyncWindow(forms.WPFWindow):
    """单窗口配置界面。"""

    def __init__(self):
        forms.WPFWindow.__init__(self, SHELL_XAML, literal_string=True)
        self.result = None
        self._build()

    # ---------- 界面 ----------
    def _text_box(self, default, width=300):
        box = TextBox()
        box.Width = width
        box.Height = 26
        box.Text = default
        box.VerticalContentAlignment = VerticalAlignment.Center
        box.Padding = Thickness(4, 2, 4, 2)
        return box

    def _row(self, label_text, control, label_width=128):
        panel = StackPanel()
        panel.Orientation = Orientation.Horizontal
        panel.Margin = Thickness(0, 0, 0, 8)
        label = Label()
        label.Content = label_text
        label.Width = label_width
        label.VerticalContentAlignment = VerticalAlignment.Center
        panel.Children.Add(label)
        panel.Children.Add(control)
        return panel

    def _section(self, title, content):
        border = Border()
        border.BorderBrush = Brushes.LightGray
        border.BorderThickness = Thickness(1)
        border.CornerRadius = CornerRadius(6)
        border.Padding = Thickness(10, 8, 10, 4)
        border.Margin = Thickness(0, 0, 0, 10)
        stack = StackPanel()
        head = Label()
        head.Content = title
        head.FontWeight = FontWeights.SemiBold
        head.Padding = Thickness(0, 0, 0, 6)
        stack.Children.Add(head)
        stack.Children.Add(content)
        border.Child = stack
        return border

    def _build(self):
        self.Title = u"一键布置标高轴网"
        self.Width = 580
        self.Height = 680
        self.WindowStartupLocation = WindowStartupLocation.CenterScreen
        self.ResizeMode = ResizeMode.NoResize
        try:
            self.FontFamily = FontFamily(u"Microsoft YaHei")
            self.FontSize = 13
        except Exception:
            pass

        root = StackPanel()
        root.Margin = Thickness(14)

        # 输入方式
        self.mode_combo = ComboBox()
        self.mode_combo.Width = 300
        self.mode_combo.Height = 26
        self.mode_combo.Items.Add(self._combo_item(u"参数生成（标高序列 + 轴距）"))
        self.mode_combo.Items.Add(self._combo_item(u"从 CSV 导入"))
        self.mode_combo.SelectedIndex = 0
        self.mode_combo.SelectionChanged += self._on_mode_changed

        # CSV
        self.csv_box = self._text_box(u"", 236)
        self.csv_box.IsEnabled = False
        self.browse_btn = Button()
        self.browse_btn.Content = u"浏览…"
        self.browse_btn.Width = 60
        self.browse_btn.Height = 26
        self.browse_btn.Margin = Thickness(4, 0, 0, 0)
        self.browse_btn.IsEnabled = False
        self.browse_btn.Click += self._on_browse
        csv_panel = StackPanel()
        csv_panel.Orientation = Orientation.Horizontal
        csv_panel.Children.Add(self.csv_box)
        csv_panel.Children.Add(self.browse_btn)

        # 参数
        self.levels_box = self._text_box(u"0, 3600, 7200, 10800")
        self.x_gaps_box = self._text_box(u"6000, 6000, 6000")
        self.y_gaps_box = self._text_box(u"8000, 8000")
        self.x_prefix_box = self._text_box(u"", 80)
        self.y_prefix_box = self._text_box(u"", 80)

        # 定位
        self.origin_box = self._text_box(u"0, 0")
        self.angle_box = self._text_box(u"0")

        # 是否建视图
        self.view_check = CheckBox()
        self.view_check.Content = u"同时创建楼层平面视图"
        self.view_check.IsChecked = False
        self.view_check.Margin = Thickness(0, 2, 0, 8)

        # 预览
        self.preview_box = TextBox()
        self.preview_box.Height = 110
        self.preview_box.IsReadOnly = True
        self.preview_box.TextWrapping = TextWrapping.Wrap
        self.preview_box.VerticalScrollBarVisibility = ScrollBarVisibility.Auto
        self.preview_box.Background = Brushes.WhiteSmoke
        self.preview_box.Padding = Thickness(6)
        self.preview_box.FontFamily = FontFamily(u"Consolas")

        params = StackPanel()
        params.Children.Add(self._row(u"标高序列 (mm)", self.levels_box))
        params.Children.Add(self._row(u"X 向轴距 (mm)", self.x_gaps_box))
        params.Children.Add(self._row(u"Y 向轴距 (mm)", self.y_gaps_box))
        row_prefix = StackPanel()
        row_prefix.Orientation = Orientation.Horizontal
        lbl_x = Label()
        lbl_x.Content = u"X 轴号前缀"
        lbl_x.Width = 128
        row_prefix.Children.Add(lbl_x)
        row_prefix.Children.Add(self.x_prefix_box)
        lbl_y = Label()
        lbl_y.Content = u"Y 轴号前缀"
        lbl_y.Width = 96
        lbl_y.Margin = Thickness(12, 0, 0, 0)
        row_prefix.Children.Add(lbl_y)
        row_prefix.Children.Add(self.y_prefix_box)
        params.Children.Add(row_prefix)
        self.params_panel = params

        locate = StackPanel()
        locate.Children.Add(self._row(u"起点 X, Y (mm)", self.origin_box))
        locate.Children.Add(self._row(u"旋转角度 (°)", self.angle_box))

        root.Children.Add(self._section(u"输入方式", self._row(u"来源", self.mode_combo)))
        root.Children.Add(self._section(u"CSV 文件", csv_panel))
        root.Children.Add(self._section(u"参数", params))
        root.Children.Add(self._section(u"定位", locate))
        root.Children.Add(self.view_check)

        preview_section = self._section(u"预览", self.preview_box)
        root.Children.Add(preview_section)

        # 按钮
        buttons = StackPanel()
        buttons.Orientation = Orientation.Horizontal
        buttons.HorizontalAlignment = HorizontalAlignment.Right
        preview_btn = Button()
        preview_btn.Content = u"预览"
        preview_btn.Width = 80
        preview_btn.Height = 28
        preview_btn.Margin = Thickness(0, 0, 8, 0)
        preview_btn.Click += self._on_preview
        ok_btn = Button()
        ok_btn.Content = u"确定生成"
        ok_btn.Width = 96
        ok_btn.Height = 28
        ok_btn.Margin = Thickness(0, 0, 8, 0)
        ok_btn.Click += self._on_ok
        cancel_btn = Button()
        cancel_btn.Content = u"取消"
        cancel_btn.Width = 72
        cancel_btn.Height = 28
        cancel_btn.Click += self._on_cancel
        buttons.Children.Add(preview_btn)
        buttons.Children.Add(ok_btn)
        buttons.Children.Add(cancel_btn)
        root.Children.Add(buttons)

        self.Content = root
        safe_call(self._update_preview)

    def _combo_item(self, text):
        item = ComboBoxItem()
        item.Content = text
        return item

    # ---------- 事件 ----------
    def _on_mode_changed(self, sender, args):
        is_csv = self.mode_combo.SelectedIndex == 1
        self.csv_box.IsEnabled = is_csv
        self.browse_btn.IsEnabled = is_csv
        self.params_panel.IsEnabled = not is_csv
        self._update_preview()

    def _on_browse(self, sender, args):
        picker = safe_call(lambda: forms.pick_file, None)
        if picker is None:
            MessageBox.Show(u"当前 pyRevit 不支持选文件。")
            return
        path = picker(file_ext="csv")
        if path:
            self.csv_box.Text = path
            self._update_preview()

    def _collect(self):
        origin_nums = parse_numbers(self.origin_box.Text) or [0.0, 0.0]
        angle_nums = parse_numbers(self.angle_box.Text)
        return {
            "is_csv": self.mode_combo.SelectedIndex == 1,
            "csv_path": self.csv_box.Text.strip(),
            "levels": self.levels_box.Text,
            "x_gaps": self.x_gaps_box.Text,
            "y_gaps": self.y_gaps_box.Text,
            "x_prefix": (self.x_prefix_box.Text or u"").strip(),
            "y_prefix": (self.y_prefix_box.Text or u"").strip(),
            "origin": (origin_nums[0],
                       origin_nums[1] if len(origin_nums) > 1 else 0.0),
            "angle": angle_nums[0] if angle_nums else 0.0,
            "want_views": bool(self.view_check.IsChecked),
        }

    def _make_spec(self, values):
        if values["is_csv"]:
            if not values["csv_path"]:
                return None, u"请先选择 CSV 文件。"
            spec = spec_from_csv(values["csv_path"])
            if not spec["levels"] and not spec["x"] and not spec["y"]:
                return None, u"CSV 里没有解析到有效数据。\n需要四列：类型,名称,坐标mm,间距mm"
            return spec, None
        spec = spec_from_params(values["levels"], values["x_gaps"],
                                values["y_gaps"], values["x_prefix"],
                                values["y_prefix"])
        if not spec["levels"]:
            return None, u"没有解析到标高值，请检查「标高序列」。"
        return spec, None

    def _update_preview(self):
        values = self._collect()
        spec, error = self._make_spec(values)
        if spec is None:
            self.preview_box.Text = error
            return
        self.preview_box.Text = describe_spec(
            spec, values["origin"], values["angle"], values["want_views"])

    def _on_preview(self, sender, args):
        self._update_preview()

    def _on_ok(self, sender, args):
        values = self._collect()
        spec, error = self._make_spec(values)
        if spec is None:
            MessageBox.Show(error, u"一键布置标高轴网")
            return
        values["spec"] = spec
        self.result = values
        self.Close()

    def _on_cancel(self, sender, args):
        self.result = None
        self.Close()


def run_window():
    window = SyncWindow()
    window.ShowDialog()
    return window.result


def main():
    if doc is None:
        forms.alert(u"请先打开一个项目。", exitscript=True)

    values = run_window()
    if not values:
        return

    spec = values["spec"]
    origin = values["origin"]
    angle = values["angle"]
    want_views = values["want_views"]

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
