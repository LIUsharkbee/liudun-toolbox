# -*- coding: utf-8 -*-
"""弧形台阶一键生成

按「圆心 + 内弧半径 + 起止角」逐级生成扇环楼板，用来做弧形台阶 / 弧形平台。
斯维尔（THS）可以直接按「板 / 台阶」提取。

两种做法：

  实体（默认）：每级楼板从踏步面一直做到「底标高（地坪）」，
                看起来是一级级实心台阶，方量也按实体算，不会悬空；
  薄板　　　　：只做一块贴面的薄楼板（用你选的楼板类型），仅看面用。

生成规则（默认「向外逐级下降」）：

    平台   [R0, R0+平台宽]                       顶标高 = 顶标高
    水舌   [平台外缘, 平台外缘+水舌宽]            与平台同高
    第 1 级 [水舌外缘, +踏面宽]                   顶标高 - 1×踏步高
    第 2 级 [第1级外缘, +踏面宽]                  顶标高 - 2×踏步高
    ……

  * 平台宽填 0 表示没有平台，水舌紧贴内弧；
  * 水舌宽填 0 表示不做滴水挑带；
  * 级数填 0 表示只做平台 + 水舌。

角度约定：+X 方向为 0°，逆时针为正，终止角 > 起始角。
单位：长度 mm，角度 度。

注意：doc.Create.NewFloor 只接受「建筑楼板」类型，
如果选到基础板（FloorType.IsFoundationSlab = True）会报
"Provided floor type does not match internal type"，所以这里会过滤基础板；
实体模式下按厚度自动创建「六吨-台阶-<厚度>」楼板类型。

窗口是模态的，Revit 不允许在模态窗口里进入拾取状态，
所以「拾取圆心 / 拾取起止角」采用「关窗 → 拾取 → 重新开窗」的方式。
"""

from __future__ import print_function

import math

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
    WindowStartupLocation,
)
from System.Windows.Controls import (
    Border,
    Button,
    ComboBox,
    ComboBoxItem,
    Label,
    Orientation,
    ScrollBarVisibility,
    ScrollViewer,
    StackPanel,
    TextBlock,
    TextBox,
)
from System.Windows.Media import Brushes, FontFamily
from System.Collections.Generic import List as DotNetList

from Autodesk.Revit.DB import (
    Arc,
    BuiltInParameter,
    BuiltInCategory,
    CompoundStructure,
    CurveArray,
    CurveLoop,
    DirectShape,
    ElementId,
    FailureHandlingOptions,
    GeometryCreationUtilities,
    GeometryObject,
    MaterialFunctionAssignment,
    FailureProcessingResult,
    FailureSeverity,
    FilteredElementCollector,
    FloorType,
    IFailuresPreprocessor,
    Level,
    Line,
    Transaction,
    XYZ,
)
from pyrevit import forms, revit, script

doc = revit.doc
uidoc = revit.uidoc

FT_TO_MM = 304.8
MM_TO_FT = 1.0 / FT_TO_MM
DEG_TO_RAD = math.pi / 180.0

FOUNDATION_WORDS = (u"基础", u"Foundation", u"foundation", u"筏板", u"承台")
SOLID_PREFIX = u"六吨-台阶-"
MIN_THICKNESS_MM = 50.0


# ---------------------------------------------------------------- 基础工具
def safe_call(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def to_float(text, default=None):
    try:
        return float((text or u"").strip().replace(u"，", u"").replace(u",", u""))
    except Exception:
        return default


def norm_angle(deg):
    value = deg % 360.0
    if abs(value - 360.0) < 1e-9:
        value = 0.0
    return value


def mm_text(value):
    return u"{0:.0f}".format(value)


def format_level(mm):
    if abs(mm) < 0.5:
        return u"±0.000"
    sign = u"+" if mm > 0 else u"-"
    return u"{0}{1:.3f}".format(sign, abs(mm) / 1000.0)


def type_name(floor_type):
    name = safe_call(lambda: floor_type.get_Parameter(
        BuiltInParameter.SYMBOL_NAME_PARAM).AsString(), None)
    if not name:
        name = safe_call(lambda: floor_type.Name, u"?")
    return name


def is_foundation(floor_type):
    if safe_call(lambda: floor_type.IsFoundationSlab, False):
        return True
    name = type_name(floor_type)
    for word in FOUNDATION_WORDS:
        if word in name:
            return True
    return False


# ---------------------------------------------------------------- 参数计算
def build_rings(params):
    """根据参数算出每一圈扇环：(r_in, r_out, 标高mm, 名称)。"""
    r0 = params["radius"]
    plat = params["plat"]
    lip = params["lip"]
    tread = params["tread"]
    rise = params["rise"]
    count = params["count"]
    top = params["top"]

    rings = []
    cursor = r0

    if plat > 0:
        rings.append((cursor, cursor + plat, top, u"平台"))
        cursor += plat
    if lip > 0:
        rings.append((cursor, cursor + lip, top, u"水舌"))
        cursor += lip
    for index in range(count):
        z = top - (index + 1) * rise
        rings.append((cursor, cursor + tread, z,
                      u"第 {0} 级".format(index + 1)))
        cursor += tread
    return rings


def ring_thickness(params, z_mm):
    """这条扇环的厚度：实体 = 顶面到地坪，薄板 = 板厚。"""
    if not params.get("solid"):
        return max(params.get("thick", 150.0), MIN_THICKNESS_MM)
    return max(z_mm - params["base"], MIN_THICKNESS_MM)


def describe(params, rings):
    start = params["start"]
    end = params["end"]
    sweep = end - start
    if sweep < 0:
        sweep += 360.0
    lines = []
    lines.append(u"圆心 ({0}, {1}) mm · 角度 {2:.1f}° → {3:.1f}°（扫掠 {4:.1f}°）".format(
        params["cx"], params["cy"], start, end, sweep))
    lines.append(u"内弧 R{0:.0f} · 共 {1} 块扇环 · 做法：{2}".format(
        params["radius"], len(rings),
        u"实体（厚到地坪 {0}）".format(format_level(params["base"]))
        if params.get("solid") else u"薄板（板厚 {0:.0f} mm）".format(
            params.get("thick", 150.0))))
    total_area = 0.0
    total_volume = 0.0
    for r_in, r_out, z, name in rings:
        area = 0.5 * sweep * DEG_TO_RAD * (r_out * r_out - r_in * r_in) / 1e6
        total_area += area
        thick = ring_thickness(params, z)
        if thick:
            volume = area * thick / 1000.0
            total_volume += volume
            lines.append(u"  {0}: R{1:.0f} → R{2:.0f}，顶 {3}，厚 {4:.0f}，面积 {5:.2f} m²，体积 {6:.2f} m³".format(
                name, r_in, r_out, format_level(z), thick, area, volume))
        else:
            lines.append(u"  {0}: R{1:.0f} → R{2:.0f}，顶 {3}，面积 {4:.2f} m²".format(
                name, r_in, r_out, format_level(z), area))
    tail = u"合计面积 {0:.2f} m²".format(total_area)
    if total_volume > 0:
        tail += u"，合计体积 {0:.2f} m³".format(total_volume)
    lines.append(tail)
    lines.append(u"最低标高 {0}".format(
        format_level(min(r[2] for r in rings)) if rings else u"—"))
    return u"\n".join(lines)


# ---------------------------------------------------------------- Revit 侧
def all_levels():
    levels = list(FilteredElementCollector(doc).OfClass(Level))
    levels = [lv for lv in levels if safe_call(lambda lv=lv: lv.Elevation, None) is not None]
    levels.sort(key=lambda lv: lv.Elevation)
    return levels


def pick_level(top_mm):
    """选一个不高于顶标高的最高标高，返回 (Level, 偏移mm)。"""
    levels = all_levels()
    if not levels:
        return None, 0.0
    best = levels[0]
    for level in levels:
        if level.Elevation * FT_TO_MM <= top_mm + 0.001:
            best = level
    offset_mm = top_mm - best.Elevation * FT_TO_MM
    return best, offset_mm


def all_floor_types():
    """返回可用的建筑楼板类型 [(名称, 元素)]，基础板会被排除。"""
    types = list(FilteredElementCollector(doc).OfClass(FloorType))
    good = []
    skipped = 0
    for item in types:
        name = type_name(item)
        if not name:
            continue
        if name.startswith(SOLID_PREFIX):
            skipped += 1
            continue
        if is_foundation(item):
            skipped += 1
            continue
        good.append((name, item))
    good.sort(key=lambda pair: pair[0])
    return good, skipped


def find_type_by_name(name):
    for item in FilteredElementCollector(doc).OfClass(FloorType):
        if type_name(item) == name:
            return item
    return None


def solid_type_for(thickness_mm, cache):
    """按厚度取（必要时新建）一个楼板类型，类型名 六吨-台阶-<厚度>。

    Revit 2020 没有 FloorType.CreateDefaultFloorType，
    所以做法是：复制一个已有的建筑楼板类型，再把构造改成单层、厚度 = 台阶厚度。
    """
    key = u"{0:.0f}".format(thickness_mm)
    if key in cache:
        return cache[key]
    name = SOLID_PREFIX + key
    existing = find_type_by_name(name)
    if existing is not None:
        cache[key] = existing
        return existing

    base = None
    for item in FilteredElementCollector(doc).OfClass(FloorType):
        tname = type_name(item)
        if not tname or tname.startswith(SOLID_PREFIX) or is_foundation(item):
            continue
        base = item
        break
    if base is None:
        return None

    thick_ft = thickness_mm * MM_TO_FT
    structure = None
    try:
        structure = CompoundStructure.CreateSingleLayerCompoundStructure(
            MaterialFunctionAssignment.Structure, thick_ft, ElementId.InvalidElementId)
    except Exception:
        structure = None

    created = None
    try:
        created = base.Duplicate(name)
    except Exception:
        created = None
    if created is None:
        cache[key] = base
        return base
    if structure is not None:
        try:
            created.SetCompoundStructure(structure)
        except Exception:
            pass
    cache[key] = created
    return created


def floor_type_thickness_mm(floor_type):
    try:
        structure = floor_type.GetCompoundStructure()
        if structure is None:
            return None
        return structure.GetWidth() * FT_TO_MM
    except Exception:
        return None


def ensure_floor_type(base_type, thick_mm):
    """按目标板厚返回楼板类型：厚度不同就复制一份并改成单层板。"""
    current = floor_type_thickness_mm(base_type)
    if current is not None and abs(current - thick_mm) < 1.0:
        return base_type

    name = u"六吨-台阶-{0:.0f}".format(thick_mm)
    existing = find_type_by_name(name)
    if existing is not None:
        return existing

    material_id = ElementId.InvalidElementId
    try:
        old_structure = base_type.GetCompoundStructure()
        if old_structure is not None:
            for index in range(old_structure.LayerCount):
                if old_structure.GetLayerFunction(index) == MaterialFunctionAssignment.Structure:
                    material_id = old_structure.GetMaterialId(index)
                    break
    except Exception:
        material_id = ElementId.InvalidElementId

    try:
        new_type = base_type.Duplicate(name)
    except Exception:
        return base_type

    structure = CompoundStructure.CreateSingleLayerCompoundStructure(
        MaterialFunctionAssignment.Structure, thick_mm * MM_TO_FT, material_id)
    new_type.SetCompoundStructure(structure)
    got = floor_type_thickness_mm(new_type)
    if got is None or abs(got - thick_mm) > 1.5:
        raise Exception(u"板厚设置没生效（读回 {0}）".format(got))
    return new_type


def arc_curve(center, radius, start_deg, end_deg, z_ft):
    start_rad = start_deg * DEG_TO_RAD
    end_rad = end_deg * DEG_TO_RAD
    center_pt = XYZ(center[0], center[1], z_ft)
    return Arc.Create(center_pt, radius, start_rad, end_rad,
                      XYZ.BasisX, XYZ.BasisY)


def ring_profile(center, r_in, r_out, start_deg, end_deg, z_ft):
    """构造闭合的扇环轮廓：外弧 → 径向线 → 内弧（反向）→ 径向线。"""
    a1 = start_deg * DEG_TO_RAD
    a2 = end_deg * DEG_TO_RAD
    cos1, sin1 = math.cos(a1), math.sin(a1)
    cos2, sin2 = math.cos(a2), math.sin(a2)

    p_start_out = XYZ(center[0] + r_out * cos1, center[1] + r_out * sin1, z_ft)
    p_end_out = XYZ(center[0] + r_out * cos2, center[1] + r_out * sin2, z_ft)
    p_end_in = XYZ(center[0] + r_in * cos2, center[1] + r_in * sin2, z_ft)
    p_start_in = XYZ(center[0] + r_in * cos1, center[1] + r_in * sin1, z_ft)

    outer = arc_curve(center, r_out, start_deg, end_deg, z_ft)
    inner = arc_curve(center, r_in, start_deg, end_deg, z_ft)

    profile = CurveArray()
    profile.Append(outer)
    profile.Append(Line.CreateBound(p_end_out, p_end_in))
    profile.Append(inner.CreateReversed())
    profile.Append(Line.CreateBound(p_start_in, p_start_out))
    return profile


def pick_center():
    try:
        point = uidoc.Selection.PickPoint(u"点取弧形台阶的圆心")
        return (point.X, point.Y)
    except Exception:
        return None


def pick_angles(center):
    try:
        first = uidoc.Selection.PickPoint(u"点取台阶弧的起点方向")
        second = uidoc.Selection.PickPoint(u"点取台阶弧的终点方向")
    except Exception:
        return None
    out = []
    for point in (first, second):
        deg = math.degrees(math.atan2(point.Y - center[1], point.X - center[0]))
        out.append(norm_angle(deg))
    return (out[0], out[1])


# ---------------------------------------------------------------- 界面
SHELL_XAML = u"""
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="弧形台阶一键生成" Width="640" Height="800" MinWidth="580" MinHeight="560"
        WindowStartupLocation="CenterScreen" ResizeMode="CanResize"
        ShowInTaskbar="False"/>
"""


class StepWindow(forms.WPFWindow):
    def __init__(self, defaults):
        forms.WPFWindow.__init__(self, SHELL_XAML, literal_string=True)
        self.defaults = defaults
        self.result = None
        self.floor_types, self.skipped = all_floor_types()
        self._build()

    def _text_box(self, value, width=110):
        box = TextBox()
        box.Width = width
        box.Height = 26
        box.Text = value
        box.VerticalContentAlignment = VerticalAlignment.Center
        box.Padding = Thickness(4, 2, 4, 2)
        return box

    def _label(self, text, width=110):
        label = Label()
        label.Content = text
        label.Width = width
        label.VerticalContentAlignment = VerticalAlignment.Center
        label.Padding = Thickness(0)
        return label

    def _row(self, label_text, controls, label_width=110, margin=(0, 0, 0, 8)):
        panel = StackPanel()
        panel.Orientation = Orientation.Horizontal
        panel.Margin = Thickness(margin[0], margin[1], margin[2], margin[3])
        panel.Children.Add(self._label(label_text, label_width))
        for item in controls:
            panel.Children.Add(item)
        return panel

    def _button(self, text, width=88):
        button = Button()
        button.Content = text
        button.Width = width
        button.Height = 26
        button.Margin = Thickness(4, 0, 0, 0)
        return button

    def _combo(self, items, width=240):
        combo = ComboBox()
        combo.Width = width
        combo.Height = 26
        for text in items:
            item = ComboBoxItem()
            item.Content = text
            combo.Items.Add(item)
        return combo

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
        self.Title = u"弧形台阶一键生成"
        self.WindowStartupLocation = WindowStartupLocation.CenterScreen
        self.ResizeMode = ResizeMode.CanResize
        try:
            self.FontFamily = FontFamily(u"Microsoft YaHei")
            self.FontSize = 13
        except Exception:
            pass

        d = self.defaults
        root = StackPanel()
        root.Margin = Thickness(14)

        # ① 圆心与角度
        self.cx_box = self._text_box(mm_text(d["cx"]), 90)
        self.cy_box = self._text_box(mm_text(d["cy"]), 90)
        self.btn_pick_center = self._button(u"拾取圆心", 92)
        self.btn_pick_center.Click += self._on_pick_center
        center_row = self._row(u"圆心 X / Y (mm)", [
            self.cx_box, self._label(u"Y", 20), self.cy_box,
            self.btn_pick_center], label_width=118)

        self.start_box = self._text_box(u"{0:.1f}".format(d["start"]), 90)
        self.end_box = self._text_box(u"{0:.1f}".format(d["end"]), 90)
        self.btn_pick_angle = self._button(u"拾取起止角", 100)
        self.btn_pick_angle.Click += self._on_pick_angle
        angle_row = self._row(u"起始角 / 终止角 (°)", [
            self.start_box, self._label(u"→", 20), self.end_box,
            self.btn_pick_angle], label_width=118)

        for box in (self.cx_box, self.cy_box, self.start_box, self.end_box):
            box.TextChanged += self._on_input

        geometry = StackPanel()
        geometry.Children.Add(center_row)
        geometry.Children.Add(angle_row)
        root.Children.Add(self._section(u"① 圆心与角度（+X 为 0°，逆时针为正）", geometry))

        # ② 尺寸
        self.radius_box = self._text_box(mm_text(d["radius"]), 90)
        self.count_box = self._text_box(u"{0}".format(d["count"]), 90)
        self.tread_box = self._text_box(mm_text(d["tread"]), 90)
        self.rise_box = self._text_box(mm_text(d["rise"]), 90)
        self.plat_box = self._text_box(mm_text(d["plat"]), 90)
        self.lip_box = self._text_box(mm_text(d["lip"]), 90)
        for box in (self.radius_box, self.count_box, self.tread_box,
                    self.rise_box, self.plat_box, self.lip_box):
            box.TextChanged += self._on_input

        size = StackPanel()
        size.Children.Add(self._row(u"内弧半径 (mm)", [self.radius_box], label_width=118))
        size.Children.Add(self._row(u"级数", [self.count_box,
                                              self._label(u"踏面宽 (mm)", 96),
                                              self.tread_box], label_width=118))
        size.Children.Add(self._row(u"踏步高 (mm)", [self.rise_box,
                                                     self._label(u"平台宽 (mm)", 96),
                                                     self.plat_box], label_width=118))
        size.Children.Add(self._row(u"水舌宽 (mm)", [self.lip_box,
                                                     self._label(u"0 = 不做滴水带", 150)],
                                    label_width=118))
        root.Children.Add(self._section(u"② 台阶尺寸（台阶从内弧向外逐级下降）", size))

        # ③ 标高、做法、类型
        self.top_box = self._text_box(mm_text(d["top"]), 90)
        self.base_box = self._text_box(mm_text(d["base"]), 90)
        self.thick_box = self._text_box(mm_text(d.get("thick", 150.0)), 90)
        self.mode_combo = self._combo([u"薄板（楼板，推荐）", u"实体（厚到地坪）"], 190)
        self.mode_combo.SelectedIndex = 1 if d.get("solid") else 0
        self.mode_combo.SelectionChanged += self._on_mode_changed
        self.top_box.TextChanged += self._on_input
        self.base_box.TextChanged += self._on_input
        self.thick_box.TextChanged += self._on_input

        self.type_combo = self._combo([name for name, _t in self.floor_types], 240)
        if self.floor_types:
            index = 0
            wanted = d.get("type_name")
            for i, (name, _t) in enumerate(self.floor_types):
                if wanted and name == wanted:
                    index = i
                    break
            else:
                for i, (name, _t) in enumerate(self.floor_types):
                    if u"常规" in name or u"Generic" in name:
                        index = i
                        break
            self.type_combo.SelectedIndex = index

        top = StackPanel()
        top.Children.Add(self._row(u"顶标高 (mm，绝对)", [self.top_box,
                                                          self._label(u"板厚 (mm)", 96),
                                                          self.thick_box], label_width=118))
        top.Children.Add(self._row(u"做法", [self.mode_combo], label_width=118))
        top.Children.Add(self._row(u"底标高 (mm)", [self.base_box,
                                                    self._label(u"仅「实体」做法使用", 160)],
                                   label_width=118))
        self.type_row = self._row(u"楼板类型", [self.type_combo], label_width=118)
        top.Children.Add(self.type_row)
        if self.skipped:
            hint = TextBlock()
            hint.Text = u"（已排除 {0} 个基础板 / 台阶类型）".format(self.skipped)
            hint.Foreground = Brushes.Gray
            hint.Margin = Thickness(118, 2, 0, 4)
            top.Children.Add(hint)
        root.Children.Add(self._section(u"③ 标高与做法", top))

        # 预览
        self.preview = TextBlock()
        self.preview.TextWrapping = TextWrapping.Wrap
        self.preview.Margin = Thickness(0, 4, 0, 6)
        preview_border = Border()
        preview_border.BorderBrush = Brushes.LightGray
        preview_border.BorderThickness = Thickness(1)
        preview_border.CornerRadius = CornerRadius(6)
        preview_border.Padding = Thickness(10, 8, 10, 8)
        preview_border.Margin = Thickness(0, 0, 0, 10)
        preview_border.Child = self.preview
        root.Children.Add(preview_border)

        buttons = StackPanel()
        buttons.Orientation = Orientation.Horizontal
        buttons.HorizontalAlignment = HorizontalAlignment.Right
        btn_preview = self._button(u"预览", 80)
        btn_preview.Click += self._on_preview
        btn_ok = self._button(u"生成", 80)
        btn_ok.Click += self._on_ok
        btn_cancel = self._button(u"取消", 80)
        btn_cancel.Click += self._on_cancel
        buttons.Children.Add(btn_preview)
        buttons.Children.Add(btn_ok)
        buttons.Children.Add(btn_cancel)
        root.Children.Add(buttons)

        scroller = ScrollViewer()
        scroller.VerticalScrollBarVisibility = ScrollBarVisibility.Auto
        scroller.Content = root
        self.Content = scroller

        self._sync_mode()
        self._on_preview(None, None)

    # ---------- 交互 ----------
    def _on_mode_changed(self, sender, args):
        self._sync_mode()
        self._on_input(None, None)

    def _sync_mode(self):
        solid = self.mode_combo.SelectedIndex == 1
        try:
            self.type_combo.IsEnabled = not solid
            self.thick_box.IsEnabled = not solid
            self.base_box.IsEnabled = solid
        except Exception:
            pass

    def _on_input(self, sender, args):
        if getattr(self, "preview", None) is not None:
            self.preview.Text = u"参数已修改，点「预览」刷新。"

    def _collect(self):
        params = {
            "cx": to_float(self.cx_box.Text, 0.0),
            "cy": to_float(self.cy_box.Text, 0.0),
            "start": norm_angle(to_float(self.start_box.Text, 0.0)),
            "end": norm_angle(to_float(self.end_box.Text, 90.0)),
            "radius": to_float(self.radius_box.Text, 4600.0),
            "count": int(to_float(self.count_box.Text, 3) or 0),
            "tread": to_float(self.tread_box.Text, 300.0),
            "rise": to_float(self.rise_box.Text, 150.0),
            "plat": to_float(self.plat_box.Text, 0.0),
            "lip": to_float(self.lip_box.Text, 150.0),
            "top": to_float(self.top_box.Text, 4200.0),
            "base": to_float(self.base_box.Text, 0.0),
            "thick": to_float(self.thick_box.Text, 150.0),
            "solid": self.mode_combo.SelectedIndex == 1,
        }
        if self.floor_types:
            index = self.type_combo.SelectedIndex
            if 0 <= index < len(self.floor_types):
                params["floor_type"] = self.floor_types[index][1]
                params["floor_type_name"] = self.floor_types[index][0]
        return params

    def _validate(self, params):
        problems = []
        if params["radius"] <= 0:
            problems.append(u"内弧半径必须大于 0。")
        if params["count"] < 0:
            problems.append(u"级数不能是负数。")
        if params["count"] > 0 and params["tread"] <= 0:
            problems.append(u"级数大于 0 时，踏面宽必须大于 0。")
        if params["count"] > 0 and params["rise"] == 0:
            problems.append(u"级数大于 0 时，踏步高不能为 0。")
        if params["plat"] < 0 or params["lip"] < 0:
            problems.append(u"平台宽 / 水舌宽不能是负数。")
        if abs(params["end"] - params["start"]) < 0.01:
            problems.append(u"起始角与终止角不能相同。")
        sweep = params["end"] - params["start"]
        if sweep < 0:
            sweep += 360.0
        if sweep > 360.0 - 0.01:
            problems.append(u"扫掠角接近整圈，请把起点/终点错开一点。")
        if params.get("solid"):
            if params["base"] >= params["top"]:
                problems.append(u"底标高必须低于顶标高。")
        else:
            if params["thick"] < 1.0:
                problems.append(u"板厚必须大于 0。")
            if not self.floor_types:
                problems.append(u"当前项目没有可用的建筑楼板类型，请先新建一个。")
        return problems

    def _rings_or_error(self):
        params = self._collect()
        problems = self._validate(params)
        if problems:
            return None, None, problems
        rings = build_rings(params)
        if not rings:
            return None, None, [u"没有要生成的扇环：级数、平台宽、水舌宽都是 0。"]
        return params, rings, []

    def _on_preview(self, sender, args):
        params, rings, problems = self._rings_or_error()
        if problems:
            self.preview.Text = u"参数有问题：\n  " + u"\n  ".join(problems)
            return
        level, offset = pick_level(params["top"])
        text = describe(params, rings)
        if level is not None:
            text += u"\n标高：{0}（{1}）+ 偏移 {2:.0f} mm".format(
                safe_call(lambda: level.Name, u"?"),
                format_level(level.Elevation * FT_TO_MM), offset)
        if not params.get("solid"):
            text += u"\n楼板类型：{0} · 目标板厚 {1:.0f} mm".format(
                params.get("floor_type_name", u"—"), params.get("thick", 150.0))
        text += u"\n一次事务生成，Ctrl+Z 可整体撤销。"
        self.preview.Text = text

    def _close_with(self, action):
        self.result = {"action": action, "params": self._collect()}
        self.Close()

    def _on_pick_center(self, sender, args):
        self._close_with("pick_center")

    def _on_pick_angle(self, sender, args):
        self._close_with("pick_angle")

    def _on_ok(self, sender, args):
        params, rings, problems = self._rings_or_error()
        if problems:
            MessageBox.Show(u"\n".join(problems), u"弧形台阶")
            return
        self.result = {"action": "ok", "params": params, "rings": rings}
        self.Close()

    def _on_cancel(self, sender, args):
        self.result = None
        self.Close()


# ---------------------------------------------------------------- 生成
class WarningSuppressor(IFailuresPreprocessor):
    def PreprocessFailures(self, accessor):
        try:
            for message in accessor.GetFailureMessages():
                if message.GetSeverity() == FailureSeverity.Warning:
                    accessor.DeleteWarning(message)
        except Exception:
            pass
        return FailureProcessingResult.Continue


def friendly_error(ex):
    text = u"{0}".format(ex)
    if "does not match internal type" in text:
        return u"楼板类型不支持（基础板不能用于楼板），请改用「实体」做法或换一个建筑楼板类型。"
    if "not valid" in text.lower() and "curve" in text.lower():
        return u"轮廓线无效，请检查起止角 / 半径。"
    return text


LOG_PATH = r"C:\Users\cyl\Documents\deepseek-harness\default-workspace\_弧形台阶日志.txt"


def write_log(lines):
    try:
        handle = open(LOG_PATH, "wb")
        try:
            for line in lines:
                handle.write(u"{0}\n".format(line).encode("utf-8"))
        finally:
            handle.close()
    except Exception:
        pass


def ring_curveloop(center, r_in, r_out, start_deg, end_deg, z_ft):
    """扇环的闭合 CurveLoop（外弧 → 径向线 → 内弧反向 → 径向线）。"""
    a1 = start_deg * DEG_TO_RAD
    a2 = end_deg * DEG_TO_RAD
    cos1, sin1 = math.cos(a1), math.sin(a1)
    cos2, sin2 = math.cos(a2), math.sin(a2)
    p_start_out = XYZ(center[0] + r_out * cos1, center[1] + r_out * sin1, z_ft)
    p_end_out = XYZ(center[0] + r_out * cos2, center[1] + r_out * sin2, z_ft)
    p_end_in = XYZ(center[0] + r_in * cos2, center[1] + r_in * sin2, z_ft)
    p_start_in = XYZ(center[0] + r_in * cos1, center[1] + r_in * sin1, z_ft)
    loop = CurveLoop()
    loop.Append(arc_curve(center, r_out, start_deg, end_deg, z_ft))
    loop.Append(Line.CreateBound(p_end_out, p_end_in))
    loop.Append(arc_curve(center, r_in, start_deg, end_deg, z_ft).CreateReversed())
    loop.Append(Line.CreateBound(p_start_in, p_start_out))
    return loop


def build_solid_step(params, r_in, r_out, z_mm, name, category_id):
    """从底标高拉伸到踏步面，生成一块实心台阶（DirectShape 实体）。"""
    base_ft = params["base"] * MM_TO_FT
    top_ft = z_mm * MM_TO_FT
    height = top_ft - base_ft
    if height <= 1e-6:
        raise Exception(u"厚度为 0，底标高高于踏步面")

    loop = ring_curveloop((params["cx"] * MM_TO_FT, params["cy"] * MM_TO_FT),
                          r_in * MM_TO_FT, r_out * MM_TO_FT,
                          params["start"], params["end"], base_ft)
    loops = DotNetList[CurveLoop]()
    loops.Add(loop)
    solid = GeometryCreationUtilities.CreateExtrusionGeometry(loops, XYZ.BasisZ, height)
    shape = DotNetList[GeometryObject]()
    shape.Add(solid)

    element = DirectShape.CreateElement(doc, category_id)
    try:
        element.ApplicationId = u"liudun-toolbox"
        element.ApplicationDataId = u"arc-step"
    except Exception:
        pass
    try:
        element.SetShape(shape)
    except Exception:
        try:
            doc.Delete(element.Id)
        except Exception:
            pass
        raise
    mark = element.get_Parameter(BuiltInParameter.ALL_MODEL_MARK)
    if mark is not None and not mark.IsReadOnly:
        mark.Set(u"弧形台阶-{0}".format(name))
    return element


def create_rings(params, rings):
    """生成台阶：实体模式用拉伸实体，薄板模式用楼板。

    返回 (成功数, 失败列表, 新建Id列表, 日志行)。
    """
    level, _offset = pick_level(params["top"])
    if level is None:
        return 0, [(u"—", u"项目里没有标高，无法创建楼板。")], [], []

    solid = params.get("solid", True)
    user_type = params.get("floor_type")
    if not solid and user_type is None:
        return 0, [(u"—", u"没有可用的建筑楼板类型。")], [], []
    if not solid and is_foundation(user_type):
        return 0, [(u"—", u"所选类型是基础板，请改选建筑楼板类型。")], [], []

    level_z = level.Elevation
    ok_count = 0
    failures = []
    created = []
    type_cache = {}
    log = [u"做法：{0}".format(u"实体（厚到地坪）" if solid else u"薄板"),
           u"底标高 {0} mm · 顶标高 {1} mm · 共 {2} 块".format(
               params["base"], params["top"], len(rings))]

    transaction = Transaction(doc, u"弧形台阶")
    options = transaction.GetFailureHandlingOptions()
    options.SetFailuresPreprocessor(WarningSuppressor())
    options.SetClearAfterRollback(True)
    transaction.SetFailureHandlingOptions(options)
    transaction.Start()

    try:
        if solid:
            cat_ids = [ElementId(BuiltInCategory.OST_Floors),
                       ElementId(BuiltInCategory.OST_GenericModel)]
            cat_index = 0
            for r_in, r_out, z_mm, name in rings:
                made = None
                last_error = None
                while cat_index < len(cat_ids):
                    try:
                        made = build_solid_step(params, r_in, r_out, z_mm, name,
                                                cat_ids[cat_index])
                        break
                    except Exception as ex:
                        last_error = ex
                        if cat_index == 0:
                            log.append(u"{0}: 楼板类别不可用（{1}），改用常规模型".format(name, ex))
                            cat_index = 1
                            continue
                        break
                if made is None:
                    failures.append((name, friendly_error(last_error)))
                    log.append(u"{0}: 失败 {1}".format(name, last_error))
                else:
                    ok_count += 1
                    created.append(made.Id)
                    log.append(u"{0}: OK id={1} R{2:.0f}-R{3:.0f} 顶{4:.0f} 底{5:.0f}".format(
                        name, made.Id.IntegerValue, r_in, r_out, z_mm, params["base"]))
        else:
            target_type = user_type
            try:
                target_type = ensure_floor_type(user_type, params["thick"])
                log.append(u"楼板类型：{0}，板厚 {1} mm".format(
                    type_name(target_type), floor_type_thickness_mm(target_type) or 0))
            except Exception as ex:
                log.append(u"按板厚建类型失败：{0}（改用原类型）".format(ex))

            for r_in, r_out, z_mm, name in rings:
                floor = None
                try:
                    floor_type = target_type
                    if floor_type is None:
                        failures.append((name, u"没找到可用的楼板类型。"))
                        continue
                    z_ft = z_mm * MM_TO_FT
                    offset_ft = z_ft - level_z
                    profile = ring_profile((params["cx"] * MM_TO_FT,
                                            params["cy"] * MM_TO_FT),
                                           r_in * MM_TO_FT, r_out * MM_TO_FT,
                                           params["start"], params["end"], level_z)
                    floor = doc.Create.NewFloor(profile, floor_type, level, False)
                    if floor is None:
                        failures.append((name, u"Revit 没有返回楼板。"))
                        continue
                    height_param = floor.get_Parameter(
                        BuiltInParameter.FLOOR_HEIGHTABOVELEVEL_PARAM)
                    if height_param is not None and not height_param.IsReadOnly:
                        height_param.Set(offset_ft)
                    mark = floor.get_Parameter(BuiltInParameter.ALL_MODEL_MARK)
                    if mark is not None and not mark.IsReadOnly:
                        mark.Set(u"弧形台阶-{0}".format(name))
                    ok_count += 1
                    created.append(floor.Id)
                    log.append(u"{0}: OK 楼板 id={1}".format(name, floor.Id.IntegerValue))
                except Exception as ex:
                    failures.append((name, friendly_error(ex)))
                    log.append(u"{0}: 失败 {1}".format(name, ex))
                    try:
                        if floor is not None and floor.IsValidObject:
                            doc.Delete(floor.Id)
                    except Exception:
                        pass
        if failures:
            transaction.RollBack()
            write_log(log)
            return 0, failures, [], log
        transaction.Commit()
    except Exception as ex:
        try:
            transaction.RollBack()
        except Exception:
            pass
        write_log(log + [u"事务异常：{0}".format(ex)])
        return 0, [(u"整个事务", friendly_error(ex))], [], log
    write_log(log)
    return ok_count, [], created, log


# ---------------------------------------------------------------- 入口
def run_window():
    defaults = {
        "cx": 0.0, "cy": 0.0,
        "start": 0.0, "end": 90.0,
        "radius": 4600.0,
        "count": 3, "tread": 300.0, "rise": 150.0,
        "plat": 0.0, "lip": 150.0,
        "top": 4200.0,
        "base": 0.0,
        "thick": 150.0,
        "solid": False,
    }
    while True:
        window = StepWindow(defaults)
        window.ShowDialog()
        result = window.result
        if not result:
            return None
        action = result["action"]
        params = result.get("params", {})
        for key in list(defaults.keys()):
            if key in params:
                defaults[key] = params[key]
        if params.get("floor_type_name"):
            defaults["type_name"] = params["floor_type_name"]

        if action == "pick_center":
            point = pick_center()
            if point:
                defaults["cx"] = point[0] * FT_TO_MM
                defaults["cy"] = point[1] * FT_TO_MM
            continue

        if action == "pick_angle":
            center = (defaults["cx"] * MM_TO_FT, defaults["cy"] * MM_TO_FT)
            angles = pick_angles(center)
            if angles:
                defaults["start"], defaults["end"] = angles
            continue

        return result
    return None


def main():
    result = run_window()
    if not result:
        return
    params = result["params"]
    rings = result["rings"]

    preview = describe(params, rings)
    ok_count, failures, created, log_lines = create_rings(params, rings)
    solid = params.get("solid", True)
    what = u"实心台阶" if solid else u"扇形楼板"

    if failures:
        detail = []
        for name, message in failures[:8]:
            detail.append(u"{0}：{1}".format(name, message))
        more = u"" if len(failures) <= 8 else u"\n… 还有 {0} 条".format(len(failures) - 8)
        extra = u""
        try:
            if log_lines:
                extra = u"\n\n日志：{0}".format(LOG_PATH)
        except Exception:
            extra = u""
        forms.alert(
            u"生成失败：成功 {0} 块，失败 {1} 块（已整体回滚）。\n\n{2}{3}\n\n{4}{5}".format(
                ok_count, len(failures), u"\n".join(detail), more, preview, extra),
            title=u"弧形台阶", warn_icon=True)
        return

    try:
        if created:
            uidoc.Selection.SetElementIds(created)
    except Exception:
        pass

    forms.alert(u"生成完成：共 {0} 块{1}，已落地到 {2:.0f} mm 并选中。\n\n{3}".format(
        ok_count, what, params["base"], preview), title=u"弧形台阶")


main()
