# -*- coding: utf-8 -*-
"""六吨工具箱 · 吊顶龙骨

按房间边界自动排布轻钢龙骨吊顶骨架：
    主龙骨（承载龙骨）中距 900~1000
    次龙骨（覆面龙骨）中距 500 / 605
    吊杆间距 900~1200

每层房间生成「一个」常规模型实体（DirectShape），骨架位置：
    吊顶完成面 = 输入的标高，石膏板 13mm 在完成面之上，次龙骨 / 主龙骨依次往上。
"""

import math

from pyrevit import forms, revit, script

from Autodesk.Revit.DB import (
    BuiltInCategory,
    BuiltInParameter,
    CurveLoop,
    DirectShape,
    ElementId,
    FilteredElementCollector,
    GeometryCreationUtilities,
    GeometryObject,
    Line,
    Plane,
    SketchPlane,
    SpatialElementBoundaryOptions,
    Transaction,
    XYZ,
)
from Autodesk.Revit.DB.Architecture import Room
from Autodesk.Revit.UI.Selection import ObjectType
from System.Collections.Generic import List as DotNetList
from System.Windows import HorizontalAlignment, TextWrapping, Thickness, FontWeights, VerticalAlignment
from System.Windows.Controls import (
    Button,
    CheckBox,
    ComboBox,
    Orientation,
    ScrollViewer,
    ScrollBarVisibility,
    StackPanel,
    TextBlock,
    TextBox,
)
from System.Windows.Media import Brushes

doc = revit.doc
uidoc = revit.uidoc

FT_TO_MM = 304.8
MM_TO_FT = 1.0 / FT_TO_MM

BOARD_MM = 13.0          # 石膏板厚
SECONDARY_H_MM = 20.0    # 次龙骨高
SECONDARY_W_MM = 50.0    # 次龙骨宽
MAIN_H_MM = 30.0         # 主龙骨高
MAIN_W_MM = 60.0         # 主龙骨宽
EDGE_MM = 20.0           # 边龙骨
ROD_MM = 10.0            # 吊杆直径

SAMPLE_MM = 120.0        # 轮廓采样步长
MIN_MEMBER_MM = 150.0    # 太短的龙骨不生成


# ---------------------------------------------------------------- 工具
def to_float(text, default):
    try:
        return float(u"{0}".format(text).strip())
    except Exception:
        return default


def mm_text(value):
    return u"{0:.0f}".format(value)


def format_level(mm):
    return u"{0:+.3f}".format(mm / 1000.0)


def category_rooms():
    """项目里所有已放置的房间。"""
    rooms = []
    for element in FilteredElementCollector(doc).OfCategory(
            BuiltInCategory.OST_Rooms).WhereElementIsNotElementType():
        try:
            if not isinstance(element, Room):
                continue
            if element.Area <= 0:
                continue
            if element.Location is None:
                continue
            rooms.append(element)
        except Exception:
            continue
    return rooms


def loops_from_room(room):
    """房间边界 → (轮廓点, 边界曲线)。点在英尺，只取 XY。"""
    opts = SpatialElementBoundaryOptions()
    loops = []
    curves = []
    try:
        segments = room.GetBoundarySegments(opts)
    except Exception:
        return loops, curves
    for segment_loop in segments:
        points = []
        loop_curves = []
        for segment in segment_loop:
            try:
                curve = segment.GetCurve()
                loop_curves.append(curve)
                for point in curve.Tessellate():
                    points.append((point.X, point.Y))
            except Exception:
                continue
        if len(points) >= 3:
            loops.append(points)
            curves.append(loop_curves)
    return loops, curves


def point_inside(x, y, loops):
    """奇偶规则判断点是否在房间轮廓内（支持带洞的房间）。"""
    inside = False
    for points in loops:
        count = len(points)
        for index in range(count):
            x1, y1 = points[index]
            x2, y2 = points[(index + 1) % count]
            if (y1 > y) != (y2 > y):
                xi = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
                if xi > x:
                    inside = not inside
    return inside


def plan_lines(loops, angle_deg, spacing_ft, min_len_ft):
    """在轮廓内按方向/中距排线，返回 [(p1, p2), ...]。"""
    radius = math.radians(angle_deg)
    ux, uy = math.cos(radius), math.sin(radius)
    vx, vy = -uy, ux
    points = []
    for loop in loops:
        points.extend(loop)
    if not points:
        return []
    v_values = [x * vx + y * vy for (x, y) in points]
    u_values = [x * ux + y * uy for (x, y) in points]
    vmin, vmax = min(v_values), max(v_values)
    umin, umax = min(u_values), max(u_values)

    step = SAMPLE_MM * MM_TO_FT
    result = []
    index = int(math.floor(vmin / spacing_ft))
    while index * spacing_ft <= vmax:
        v0 = index * spacing_ft
        t = umin + step * 0.5
        run_start = None
        run_end = None
        while t <= umax:
            x = ux * t + vx * v0
            y = uy * t + vy * v0
            if point_inside(x, y, loops):
                if run_start is None:
                    run_start = t
                run_end = t
            elif run_start is not None:
                if run_end - run_start >= min_len_ft:
                    result.append(_segment(ux, uy, vx, vy, v0, run_start, run_end))
                run_start = None
                run_end = None
            t += step
        if run_start is not None and run_end - run_start >= min_len_ft:
            result.append(_segment(ux, uy, vx, vy, v0, run_start, run_end))
        index += 1
    return result


def _segment(ux, uy, vx, vy, v0, t1, t2):
    p1 = (ux * t1 + vx * v0, uy * t1 + vy * v0)
    p2 = (ux * t2 + vx * v0, uy * t2 + vy * v0)
    return (p1, p2)


def member_solid(p1, p2, z0, width_ft, height_ft):
    """沿 p1→p2 生成一根矩形截面杆件（从 z0 向上拉伸）。"""
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    length = math.hypot(dx, dy)
    if length < 1e-4:
        return None
    nx, ny = -dy / length, dx / length
    half = width_ft / 2.0
    a = XYZ(p1[0] + nx * half, p1[1] + ny * half, z0)
    b = XYZ(p2[0] + nx * half, p2[1] + ny * half, z0)
    c = XYZ(p2[0] - nx * half, p2[1] - ny * half, z0)
    d = XYZ(p1[0] - nx * half, p1[1] - ny * half, z0)
    loop = CurveLoop()
    loop.Append(Line.CreateBound(a, b))
    loop.Append(Line.CreateBound(b, c))
    loop.Append(Line.CreateBound(c, d))
    loop.Append(Line.CreateBound(d, a))
    loops = DotNetList[CurveLoop]()
    loops.Add(loop)
    try:
        return GeometryCreationUtilities.CreateExtrusionGeometry(
            loops, XYZ.BasisZ, height_ft)
    except Exception:
        return None


def rod_positions(p1, p2, spacing_ft):
    """沿一根主龙骨按间距放吊杆。"""
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    length = math.hypot(dx, dy)
    if length < 1e-4:
        return []
    count = max(1, int(length / spacing_ft))
    result = []
    for i in range(count + 1):
        t = min(1.0, (i * spacing_ft) / length)
        result.append((p1[0] + dx * t, p1[1] + dy * t))
    return result


def chop_line(p1, p2, pitch_ft, keep_ft, phase_ft=0.0):
    """把一条线段按 pitch 切成长度 keep 的小段（全局对齐，用于横撑）。"""
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    length = math.hypot(dx, dy)
    if length < 1e-4:
        return []
    ux, uy = dx / length, dy / length
    base = p1[0] * ux + p1[1] * uy
    start = math.floor((base - phase_ft) / pitch_ft) * pitch_ft + phase_ft
    out = []
    while start < base + length - 1e-9:
        a = max(start, base)
        b = min(start + keep_ft, base + length)
        if b - a > keep_ft * 0.35:
            out.append((
                (p1[0] + ux * (a - base), p1[1] + uy * (a - base)),
                (p1[0] + ux * (b - base), p1[1] + uy * (b - base))))
        start += pitch_ft
    return out


# ---------------------------------------------------------------- 计算
def plan_room(room, params):
    """算一个房间的龙骨，返回 (骨架长度统计, 吊杆数, 房间信息)。"""
    loops, curves = loops_from_room(room)
    if not loops:
        return None

    zc = params["top"] * MM_TO_FT
    z_board = zc
    z_secondary = zc + BOARD_MM * MM_TO_FT
    z_main = z_secondary + SECONDARY_H_MM * MM_TO_FT
    z_rod = z_main + MAIN_H_MM * MM_TO_FT

    main_lines = plan_lines(loops, params["main_angle"],
                            params["main_spacing"] * MM_TO_FT,
                            MIN_MEMBER_MM * MM_TO_FT)
    secondary_lines = plan_lines(loops, params["main_angle"] + 90.0,
                                 params["secondary_spacing"] * MM_TO_FT,
                                 MIN_MEMBER_MM * MM_TO_FT)

    edge_lines = []
    if params["edge"]:
        for loop_curves in curves:
            for curve in loop_curves:
                try:
                    p1 = curve.GetEndPoint(0)
                    p2 = curve.GetEndPoint(1)
                except Exception:
                    continue
                if math.hypot(p2.X - p1.X, p2.Y - p1.Y) < MIN_MEMBER_MM * MM_TO_FT:
                    continue
                edge_lines.append(((p1.X, p1.Y), (p2.X, p2.Y)))

    rods = []
    if params["rod"]:
        for p1, p2 in main_lines:
            rods.extend(rod_positions(p1, p2, params["rod_spacing"] * MM_TO_FT))

    cross_lines = []
    if params.get("cross"):
        raw = plan_lines(loops, params["main_angle"],
                         params["cross_spacing"] * MM_TO_FT,
                         MIN_MEMBER_MM * MM_TO_FT)
        keep = max(60.0, params["secondary_spacing"] - SECONDARY_W_MM)
        for p1, p2 in raw:
            cross_lines.extend(chop_line(
                p1, p2, params["secondary_spacing"] * MM_TO_FT,
                keep * MM_TO_FT, SECONDARY_W_MM * 0.5 * MM_TO_FT))

    seam_lines = []
    if params.get("seams"):
        seam_lines.extend(plan_lines(loops, params["main_angle"],
                                     params["seam_spacing"] * MM_TO_FT,
                                     MIN_MEMBER_MM * MM_TO_FT))
        seam_lines.extend(plan_lines(loops, params["main_angle"] + 90.0,
                                     params["seam_spacing"] * MM_TO_FT,
                                     MIN_MEMBER_MM * MM_TO_FT))

    def total(lines):
        return sum(math.hypot(p2[0] - p1[0], p2[1] - p1[1]) for p1, p2 in lines)

    stats = {
        "main": total(main_lines),
        "secondary": total(secondary_lines),
        "cross": total(cross_lines),
        "edge": total(edge_lines),
        "seams": len(seam_lines),
        "rods": len(rods),
        "z": (z_board, z_secondary, z_main, z_rod),
    }
    return (main_lines, secondary_lines, cross_lines, edge_lines,
            seam_lines, rods, stats)


def describe(rooms, params):
    total_main = 0.0
    total_secondary = 0.0
    total_cross = 0.0
    total_edge = 0.0
    total_seams = 0
    total_rods = 0
    ok_rooms = 0
    for room in rooms:
        planned = plan_room(room, params)
        if planned is None:
            continue
        ok_rooms += 1
        stats = planned[6]
        total_main += stats["main"]
        total_secondary += stats["secondary"]
        total_cross += stats["cross"]
        total_edge += stats["edge"]
        total_seams += stats["seams"]
        total_rods += stats["rods"]

    board_m2 = 0.0
    for room in rooms:
        try:
            board_m2 += room.Area * 0.09290304
        except Exception:
            pass

    lines = [
        u"房间 {0} 间（有边界 {1} 间）".format(len(rooms), ok_rooms),
        u"吊顶完成面标高 {0} m（绝对）".format(format_level(params["top"])),
        u"主龙骨中距 {0:.0f} · 次龙骨中距 {1:.0f} · 吊杆间距 {2:.0f}".format(
            params["main_spacing"], params["secondary_spacing"], params["rod_spacing"]),
    ]
    if ok_rooms:
        lines.append(u"主龙骨 {0:.1f} m · 次龙骨 {1:.1f} m · 横撑 {2:.1f} m".format(
            total_main / 3.2808399, total_secondary / 3.2808399,
            total_cross / 3.2808399))
        lines.append(u"边龙骨 {0:.1f} m · 吊杆 {1} 根 · 板缝线 {2} 条".format(
            total_edge / 3.2808399, total_rods, total_seams))
        lines.append(u"房间面积合计 {0:.1f} m²（石膏板约 {1:.1f} m²）".format(board_m2, board_m2))
    else:
        lines.append(u"选中的房间没有可用边界，请确认房间已放置且有边界线。")
    lines.append(u"生成后每间房一个「常规模型」实体，可用斯维尔按构件提取；Ctrl+Z 可整体撤销。")
    return u"\n".join(lines)


# ---------------------------------------------------------------- 生成
def build_rooms(rooms, params):
    zc = params["top"] * MM_TO_FT
    z_secondary = zc + BOARD_MM * MM_TO_FT
    z_main = z_secondary + SECONDARY_H_MM * MM_TO_FT
    z_rod = z_main + MAIN_H_MM * MM_TO_FT

    transaction = Transaction(doc, u"吊顶龙骨")
    transaction.Start()
    created = []
    failures = []
    try:
        category_id = ElementId(BuiltInCategory.OST_GenericModel)
        sketch_plane = None
        if params.get("seams"):
            try:
                sketch_plane = SketchPlane.Create(
                    doc, Plane.CreateByNormalAndOrigin(XYZ.BasisZ, XYZ(0, 0, zc)))
            except Exception:
                sketch_plane = None
        for room in rooms:
            name = u"房间"
            try:
                name_param = room.get_Parameter(BuiltInParameter.ROOM_NAME)
                if name_param is not None:
                    name = name_param.AsString() or u"房间"
                planned = plan_room(room, params)
                if planned is None:
                    failures.append((name, u"没有房间边界"))
                    continue
                (main_lines, secondary_lines, cross_lines, edge_lines,
                 seam_lines, rods, stats) = planned
                shapes = DotNetList[GeometryObject]()
                for p1, p2 in main_lines:
                    solid = member_solid(p1, p2, z_main, MAIN_W_MM * MM_TO_FT,
                                         MAIN_H_MM * MM_TO_FT)
                    if solid is not None:
                        shapes.Add(solid)
                for p1, p2 in secondary_lines:
                    solid = member_solid(p1, p2, z_secondary,
                                         SECONDARY_W_MM * MM_TO_FT,
                                         SECONDARY_H_MM * MM_TO_FT)
                    if solid is not None:
                        shapes.Add(solid)
                for p1, p2 in cross_lines:
                    solid = member_solid(p1, p2, z_secondary,
                                         SECONDARY_W_MM * MM_TO_FT,
                                         SECONDARY_H_MM * MM_TO_FT)
                    if solid is not None:
                        shapes.Add(solid)
                for p1, p2 in edge_lines:
                    solid = member_solid(p1, p2, z_secondary, EDGE_MM * MM_TO_FT,
                                         SECONDARY_H_MM * MM_TO_FT)
                    if solid is not None:
                        shapes.Add(solid)
                for center in rods:
                    p1 = (center[0], center[1])
                    p2 = (center[0] + ROD_MM * MM_TO_FT, center[1])
                    solid = member_solid(
                        p1, p2, z_rod, ROD_MM * MM_TO_FT,
                        params["rod_length"] * MM_TO_FT)
                    if solid is not None:
                        shapes.Add(solid)
                if shapes.Count == 0:
                    failures.append((name, u"没有生成任何杆件"))
                    continue
                element = DirectShape.CreateElement(doc, category_id)
                try:
                    element.ApplicationId = u"liudun-toolbox"
                    element.ApplicationDataId = u"ceiling-keel"
                except Exception:
                    pass
                element.SetShape(shapes)
                mark = element.get_Parameter(BuiltInParameter.ALL_MODEL_MARK)
                if mark is not None and not mark.IsReadOnly:
                    mark.Set(u"吊顶龙骨-{0}".format(name))
                created.append(element.Id)
                if sketch_plane is not None:
                    for p1, p2 in seam_lines:
                        try:
                            doc.Create.NewModelCurve(
                                Line.CreateBound(XYZ(p1[0], p1[1], zc),
                                                 XYZ(p2[0], p2[1], zc)),
                                sketch_plane)
                        except Exception:
                            pass
            except Exception as ex:
                failures.append((name, u"{0}".format(ex)))
        if failures and not created:
            transaction.RollBack()
            return 0, failures
        transaction.Commit()
    except Exception as ex:
        try:
            transaction.RollBack()
        except Exception:
            pass
        return 0, [(u"整个事务", u"{0}".format(ex))]

    try:
        if created:
            uidoc.Selection.SetElementIds(created)
    except Exception:
        pass
    return len(created), failures


# ---------------------------------------------------------------- 界面
class KeelWindow(forms.WPFWindow):
    def __init__(self, defaults):
        self.defaults = defaults
        forms.WPFWindow.__init__(self, """
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="吊顶龙骨" Height="580" Width="640"
        WindowStartupLocation="CenterScreen" ResizeMode="CanResize"
        ShowInTaskbar="True">
  <ScrollViewer VerticalScrollBarVisibility="Auto">
    <StackPanel Margin="14" />
  </ScrollViewer>
</Window>
""", literal_string=True)
        self.result = None
        root = self.Content.Content
        if root is None:
            root = StackPanel()

        self.scope_combo = self._combo([u"选中的房间", u"当前视图全部房间"], 220)
        self.scope_combo.SelectedIndex = 0 if defaults.get("scope", 0) == 0 else 1

        self.top_box = self._text_box(mm_text(defaults["top"]), 90)
        self.main_box = self._text_box(mm_text(defaults["main_spacing"]), 90)
        self.second_box = self._text_box(mm_text(defaults["secondary_spacing"]), 90)
        self.rod_box = self._text_box(mm_text(defaults["rod_spacing"]), 90)
        self.length_box = self._text_box(mm_text(defaults["rod_length"]), 90)
        self.angle_box = self._text_box(u"{0:.0f}".format(defaults["main_angle"]), 90)
        self.cross_box = self._text_box(mm_text(defaults["cross_spacing"]), 90)
        self.seam_box = self._text_box(mm_text(defaults["seam_spacing"]), 90)

        self.edge_check = CheckBox()
        self.edge_check.Content = u"生成边龙骨"
        self.edge_check.IsChecked = True
        self.edge_check.Margin = Thickness(0, 2, 0, 2)

        self.rod_check = CheckBox()
        self.rod_check.Content = u"生成吊杆"
        self.rod_check.IsChecked = True
        self.rod_check.Margin = Thickness(0, 2, 0, 2)

        self.cross_check = CheckBox()
        self.cross_check.Content = u"生成横撑龙骨（次龙骨之间，605）"
        self.cross_check.IsChecked = True
        self.cross_check.Margin = Thickness(0, 2, 0, 2)

        self.seam_check = CheckBox()
        self.seam_check.Content = u"生成 600×600 石膏板分格线（板缝）"
        self.seam_check.IsChecked = True
        self.seam_check.Margin = Thickness(0, 2, 0, 2)

        scope = StackPanel()
        scope.Children.Add(self._row(u"生成范围", [self.scope_combo], 110))
        root.Children.Add(self._section(u"① 范围", scope))

        size = StackPanel()
        size.Children.Add(self._row(u"吊顶完成面 (mm，绝对)", [self.top_box], 110))
        size.Children.Add(self._row(u"主龙骨中距 (mm)", [self.main_box,
                                                         self._label(u"次龙骨中距 (mm)", 150),
                                                         self.second_box], 110))
        size.Children.Add(self._row(u"主龙骨方向 (°)", [self.angle_box,
                                                        self._label(u"0° = 沿 X 轴", 150)], 110))
        size.Children.Add(self._row(u"吊杆间距 (mm)", [self.rod_box,
                                                      self._label(u"吊杆长度 (mm)", 150),
                                                      self.length_box], 110))
        size.Children.Add(self._row(u"横撑中距 (mm)", [self.cross_box,
                                                      self._label(u"板缝间距 (mm)", 150),
                                                      self.seam_box], 110))
        root.Children.Add(self._section(u"② 间距与标高", size))

        extra = StackPanel()
        extra.Children.Add(self.edge_check)
        extra.Children.Add(self.rod_check)
        extra.Children.Add(self.cross_check)
        extra.Children.Add(self.seam_check)
        hint = TextBlock()
        hint.Text = (u"常用做法：主龙骨中距 900~1000，次龙骨（覆面）中距 500 或 605，"
                     u"横撑中距 605，吊杆间距 900~1200；石膏板 600×600 时次龙骨用 605。"
                     u"板缝线画在吊顶完成面标高上，是模型线，可单独删除。")
        hint.TextWrapping = TextWrapping.Wrap
        hint.Foreground = Brushes.Gray
        hint.Margin = Thickness(0, 6, 0, 2)
        extra.Children.Add(hint)
        root.Children.Add(self._section(u"③ 构件", extra))

        self.preview = TextBlock()
        self.preview.TextWrapping = TextWrapping.Wrap
        self.preview.Margin = Thickness(0, 4, 0, 8)
        root.Children.Add(self._section(u"预览", self.preview))

        buttons = StackPanel()
        buttons.Orientation = Orientation.Horizontal
        buttons.HorizontalAlignment = HorizontalAlignment.Right
        btn_preview = self._button(u"预览", 80)
        btn_preview.Click += self._on_preview
        btn_ok = self._button(u"生成", 80)
        btn_ok.Click += self._on_ok
        btn_cancel = self._button(u"取消", 80)
        btn_cancel.Click += self._on_close
        buttons.Children.Add(btn_preview)
        buttons.Children.Add(btn_ok)
        buttons.Children.Add(btn_cancel)
        root.Children.Add(buttons)

        self._on_preview(None, None)

    # ---------- 控件工厂 ----------
    def _label(self, text, width=0):
        label = TextBlock()
        label.Text = text
        label.VerticalAlignment = VerticalAlignment.Center
        label.Margin = Thickness(0, 0, 6, 0)
        if width:
            label.Width = width
        return label

    def _text_box(self, text, width=90):
        box = TextBox()
        box.Text = text
        box.Width = width
        box.Margin = Thickness(0, 2, 6, 2)
        return box

    def _combo(self, items, width=200):
        combo = ComboBox()
        for item in items:
            combo.Items.Add(item)
        combo.Width = width
        combo.SelectedIndex = 0
        combo.Margin = Thickness(0, 2, 6, 2)
        return combo

    def _button(self, text, width=80):
        button = Button()
        button.Content = text
        button.Width = width
        button.Margin = Thickness(0, 0, 8, 0)
        button.Padding = Thickness(6, 4, 6, 4)
        return button

    def _row(self, label_text, controls, label_width=110):
        row = StackPanel()
        row.Orientation = Orientation.Horizontal
        row.Margin = Thickness(0, 3, 0, 3)
        row.Children.Add(self._label(label_text, label_width))
        for control in controls:
            row.Children.Add(control)
        return row

    def _section(self, title, content):
        panel = StackPanel()
        heading = TextBlock()
        heading.Text = title
        heading.FontWeight = FontWeights.Bold
        heading.Margin = Thickness(0, 12, 0, 6)
        panel.Children.Add(heading)
        panel.Children.Add(content)
        return panel

    # ---------- 参数 ----------
    def _collect(self):
        return {
            "scope": self.scope_combo.SelectedIndex,
            "top": to_float(self.top_box.Text, 3000.0),
            "main_spacing": max(100.0, to_float(self.main_box.Text, 900.0)),
            "secondary_spacing": max(100.0, to_float(self.second_box.Text, 500.0)),
            "main_angle": to_float(self.angle_box.Text, 0.0),
            "rod_spacing": max(200.0, to_float(self.rod_box.Text, 900.0)),
            "rod_length": max(100.0, to_float(self.length_box.Text, 800.0)),
            "cross_spacing": max(100.0, to_float(self.cross_box.Text, 605.0)),
            "seam_spacing": max(100.0, to_float(self.seam_box.Text, 600.0)),
            "edge": bool(self.edge_check.IsChecked),
            "rod": bool(self.rod_check.IsChecked),
            "cross": bool(self.cross_check.IsChecked),
            "seams": bool(self.seam_check.IsChecked),
        }

    def _rooms(self, params):
        rooms = []
        if params["scope"] == 0:
            try:
                selection = uidoc.Selection.GetElementIds()
            except Exception:
                selection = []
            for element_id in selection:
                try:
                    element = doc.GetElement(element_id)
                    if isinstance(element, Room) and element.Area > 0:
                        rooms.append(element)
                except Exception:
                    continue
        if not rooms:
            rooms = category_rooms()
        return rooms

    def _on_preview(self, sender, args):
        params = self._collect()
        rooms = self._rooms(params)
        self.preview.Text = describe(rooms, params)

    def _on_ok(self, sender, args):
        params = self._collect()
        rooms = self._rooms(params)
        if not rooms:
            forms.alert(u"没有找到房间。请先选中房间，或确认房间已放置。", title=u"吊顶龙骨")
            return
        self.result = {"params": params, "rooms": rooms}
        self.Close()

    def _on_close(self, sender, args):
        self.result = None
        self.Close()


# ---------------------------------------------------------------- 入口
def main():
    selection_rooms = []
    try:
        for element_id in uidoc.Selection.GetElementIds():
            element = doc.GetElement(element_id)
            if isinstance(element, Room) and element.Area > 0:
                selection_rooms.append(element)
    except Exception:
        pass

    defaults = {
        "scope": 0,
        "top": 3000.0,
        "main_spacing": 900.0,
        "secondary_spacing": 500.0,
        "main_angle": 0.0,
        "rod_spacing": 900.0,
        "rod_length": 800.0,
        "cross_spacing": 605.0,
        "seam_spacing": 600.0,
    }
    if selection_rooms:
        try:
            level = selection_rooms[0].Level
            defaults["top"] = round(level.Elevation * FT_TO_MM + 3000.0)
        except Exception:
            pass

    window = KeelWindow(defaults)
    window.ShowDialog()
    result = window.result
    if not result:
        return

    params = result["params"]
    rooms = result["rooms"]
    summary = describe(rooms, params)

    ok_count, failures = build_rooms(rooms, params)
    if failures and ok_count == 0:
        detail = u"\n".join([u"{0}：{1}".format(name, message)
                            for name, message in failures[:8]])
        forms.alert(u"生成失败：0 间成功。\n\n{0}\n\n{1}".format(detail, summary),
                    title=u"吊顶龙骨", warn_icon=True)
        return

    text = u"生成完成：{0} 间房已生成吊顶骨架，并在模型中选中。\n\n{1}".format(
        ok_count, summary)
    if failures:
        detail = u"\n".join([u"{0}：{1}".format(name, message)
                            for name, message in failures[:6]])
        text += u"\n\n失败 {0} 间：\n{1}".format(len(failures), detail)
    forms.alert(text, title=u"吊顶龙骨")


main()
