# -*- coding: utf-8 -*-
"""房屋散水 —— 沿建筑外轮廓一键生成散水（楔形实体，带坡度）。

做法：
  1. 取楼板顶面的外轮廓（优先选中的楼板，没选就取面积最大的那块）；
  2. 沿每条外边向外偏移「散水宽」，内边顶标高用你填的值，外边按坡度下降；
  3. 每段生成一个楔形实体（梯形截面沿边拉伸），全部装进一个 DirectShape，
     一个事务完成，可 Ctrl+Z 整体撤销。
"""

import math
import traceback
from datetime import datetime

import clr

clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')

from Autodesk.Revit.DB import (
    BuiltInCategory,
    BuiltInParameter,
    CurveLoop,
    DirectShape,
    ElementId,
    FailureHandlingOptions,
    FailureProcessingResult,
    FailureSeverity,
    FilteredElementCollector,
    GeometryCreationUtilities,
    GeometryObject,
    IFailuresPreprocessor,
    Line,
    Options,
    Transaction,
    UV,
    ViewDetailLevel,
    XYZ,
)
from System.Collections.Generic import List as DotNetList
from System.Windows import (
    FontWeights,
    GridLength,
    GridUnitType,
    HorizontalAlignment,
    TextWrapping,
    Thickness,
    VerticalAlignment,
)
from System.Windows.Controls import (
    Button,
    CheckBox,
    ColumnDefinition,
    Grid,
    Orientation,
    StackPanel,
    TextBlock,
    TextBox,
)

from pyrevit import forms, revit, script

doc = revit.doc
uidoc = revit.uidoc

FT_TO_MM = 304.8
MM_TO_FT = 1.0 / 304.8

LOG_PATH = r"C:\Users\cyl\Documents\deepseek-harness\default-workspace\_散水日志.txt"


# ---------------------------------------------------------------- 基础工具
def write_log(lines):
    try:
        with open(LOG_PATH, "a") as fh:
            fh.write(u"\n===== {0} =====\n".format(datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            for line in lines:
                fh.write(u"{0}\n".format(line))
    except Exception:
        pass


def to_float(text, default):
    try:
        return float(u"{0}".format(text).strip())
    except Exception:
        return default


def type_name(element):
    for getter in ("Name", "get_Name"):
        try:
            value = getattr(element, getter)
            if callable(value):
                value = value()
            if value:
                return u"{0}".format(value)
        except Exception:
            pass
    try:
        return u"<{0}>".format(element.Id.IntegerValue)
    except Exception:
        return u"<未知>"


def is_foundation(floor_type):
    try:
        if floor_type.IsFoundationSlab:
            return True
    except Exception:
        pass
    name = type_name(floor_type)
    for word in (u"基础", u"筏板", u"承台", u"Foundation"):
        if word in name:
            return True
    return False


def all_floors():
    collector = FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_Floors)
    floors = []
    for element in collector:
        try:
            if is_foundation(element.FloorType):
                continue
        except Exception:
            pass
        floors.append(element)
    return floors


def floor_area(floor):
    try:
        param = floor.get_Parameter(BuiltInParameter.HOST_AREA_COMPUTED)
        if param is not None:
            return param.AsDouble()
    except Exception:
        pass
    try:
        return floor.Area
    except Exception:
        return 0.0


def pick_floors():
    """优先用选中的楼板；没选就用面积最大的那块。"""
    floors = all_floors()
    if not floors:
        return [], u"项目里没有楼板"
    selected = []
    try:
        for element_id in uidoc.Selection.GetElementIds():
            picked = doc.GetElement(element_id)
            if picked is None or picked.Category is None:
                continue
            if picked.Category.Id.IntegerValue == int(BuiltInCategory.OST_Floors):
                selected.append(picked)
    except Exception:
        pass
    if selected:
        return selected, u"选中的 {0} 块楼板".format(len(selected))
    floors.sort(key=floor_area, reverse=True)
    return [floors[0]], u"未选楼板，使用面积最大的楼板"


# ---------------------------------------------------------------- 轮廓
def top_face(floor):
    opts = Options()
    opts.ComputeReferences = True
    opts.DetailLevel = ViewDetailLevel.Fine
    try:
        geometry = floor.get_Geometry(opts)
    except Exception:
        return None
    if geometry is None:
        return None
    best_face = None
    best_area = 0.0
    for obj in geometry:
        try:
            faces = obj.Faces
        except Exception:
            continue
        for face in faces:
            try:
                normal = face.ComputeNormal(UV(0.5, 0.5))
            except Exception:
                continue
            try:
                if normal.Z < 0.9:
                    continue
                area = face.Area
            except Exception:
                continue
            if area > best_area:
                best_area = area
                best_face = face
    return best_face


def loop_points(curve_loop):
    points = []
    try:
        curves = list(curve_loop)
    except Exception:
        return points
    for curve in curves:
        try:
            sampled = curve.Tessellate()
        except Exception:
            continue
        for point in sampled:
            if points:
                last = points[-1]
                if abs(last.X - point.X) < 1e-6 and abs(last.Y - point.Y) < 1e-6:
                    continue
            points.append(XYZ(point.X, point.Y, 0.0))
    if len(points) > 1:
        first, last = points[0], points[-1]
        if abs(first.X - last.X) < 1e-6 and abs(first.Y - last.Y) < 1e-6:
            points.pop()
    return points


def signed_area(points):
    total = 0.0
    count = len(points)
    for index in range(count):
        p1 = points[index]
        p2 = points[(index + 1) % count]
        total += p1.X * p2.Y - p2.X * p1.Y
    return total * 0.5


def best_loop(face):
    best = []
    best_len = 0.0
    try:
        loops = face.GetEdgesAsCurveLoops()
    except Exception:
        return best
    for curve_loop in loops:
        points = loop_points(curve_loop)
        if len(points) < 3:
            continue
        length = 0.0
        count = len(points)
        for index in range(count):
            p1 = points[index]
            p2 = points[(index + 1) % count]
            length += math.hypot(p2.X - p1.X, p2.Y - p1.Y)
        if length > best_len:
            best_len = length
            best = points
    return best


def outline_of(floor, base_z):
    face = top_face(floor)
    if face is None:
        return []
    points = best_loop(face)
    if len(points) < 3:
        return []
    return [XYZ(point.X, point.Y, base_z) for point in points]


# ---------------------------------------------------------------- 几何
def edge_solid(start, direction, normal, length, width, drop, thickness, base_z):
    """沿 direction 拉伸梯形截面：内边顶 base_z，外边顶 base_z - drop。"""
    inner_top = base_z
    outer_top = base_z - drop
    inner_bottom = base_z - thickness
    outer_bottom = outer_top - thickness

    def at(offset, z):
        return XYZ(start.X + normal.X * offset, start.Y + normal.Y * offset, z)

    a = at(0.0, inner_top)
    b = at(width, outer_top)
    c = at(width, outer_bottom)
    d = at(0.0, inner_bottom)

    loop = CurveLoop()
    try:
        loop.Append(Line.CreateBound(a, b))
        loop.Append(Line.CreateBound(b, c))
        loop.Append(Line.CreateBound(c, d))
        loop.Append(Line.CreateBound(d, a))
    except Exception:
        return None

    loops = DotNetList[CurveLoop]()
    loops.Add(loop)
    try:
        return GeometryCreationUtilities.CreateExtrusionGeometry(loops, direction, length)
    except Exception:
        return None


def build_apron(floors, width_mm, slope_pct, thick_mm, inset_mm, top_mm, lip):
    width = width_mm * MM_TO_FT
    inset = inset_mm * MM_TO_FT
    thickness = max(thick_mm, 20.0) * MM_TO_FT
    drop = width * (slope_pct / 100.0)
    base_z = top_mm * MM_TO_FT
    lip_h = 30.0 * MM_TO_FT if lip else 0.0

    solids = []
    total_len = 0.0
    for floor in floors:
        points = outline_of(floor, base_z)
        if len(points) < 3:
            continue
        if signed_area(points) < 0:
            points = list(reversed(points))
        count = len(points)
        for index in range(count):
            p1 = points[index]
            p2 = points[(index + 1) % count]
            dx = p2.X - p1.X
            dy = p2.Y - p1.Y
            seg_len = math.hypot(dx, dy)
            if seg_len < 0.01:
                continue
            direction = XYZ(dx / seg_len, dy / seg_len, 0.0)
            # 逆时针环的外法向 = (dy, -dx) 归一化
            normal = XYZ(dy / seg_len, -dx / seg_len, 0.0)
            ext = width * 0.5
            start = XYZ(
                p1.X - direction.X * ext + normal.X * inset,
                p1.Y - direction.Y * ext + normal.Y * inset,
                base_z,
            )
            solid = edge_solid(start, direction, normal, seg_len + 2 * ext,
                               width, drop, thickness, base_z)
            if solid is not None:
                solids.append(solid)
                total_len += seg_len
            if lip_h > 0:
                lip_start = XYZ(
                    p1.X - direction.X * ext + normal.X * (inset + width),
                    p1.Y - direction.Y * ext + normal.Y * (inset + width),
                    base_z - drop,
                )
                lip_solid = edge_solid(lip_start, direction, normal, seg_len + 2 * ext,
                                       0.06, 0.0, lip_h, base_z - drop)
                if lip_solid is not None:
                    solids.append(lip_solid)

    if not solids:
        return None, 0, 0.0

    shape = DirectShape.CreateElement(doc, ElementId(int(BuiltInCategory.OST_GenericModel)))
    shapes = DotNetList[GeometryObject]()
    for solid in solids:
        shapes.Add(solid)
    shape.SetShape(shapes)
    try:
        shape.get_Parameter(BuiltInParameter.ALL_MODEL_MARK).Set(u"散水")
    except Exception:
        pass
    return shape, len(solids), total_len


# ---------------------------------------------------------------- 事务
class WarningSuppressor(IFailuresPreprocessor):
    def PreprocessFailures(self, accessor):
        try:
            for message in accessor.GetFailureMessages():
                if message.GetSeverity() == FailureSeverity.Warning:
                    accessor.DeleteWarning(message)
        except Exception:
            pass
        return FailureProcessingResult.Continue


def generate(floors, width_mm, slope_pct, thick_mm, inset_mm, top_mm, lip):
    transaction = Transaction(doc, u"生成散水")
    transaction.Start()
    options = transaction.GetFailureHandlingOptions()
    options.SetFailuresPreprocessor(WarningSuppressor())
    options.SetClearAfterRollback(True)
    transaction.SetFailureHandlingOptions(options)
    try:
        shape, count, total_len = build_apron(
            floors, width_mm, slope_pct, thick_mm, inset_mm, top_mm, lip
        )
        if shape is None:
            transaction.RollBack()
            return None, 0, 0.0
        transaction.Commit()
        return shape, count, total_len
    except Exception:
        try:
            transaction.RollBack()
        except Exception:
            pass
        raise


# ---------------------------------------------------------------- 界面
def field_row(label_text, control, label_width=170):
    grid = Grid()
    grid.Margin = Thickness(0, 3, 0, 3)
    col1 = ColumnDefinition()
    col1.Width = GridLength(label_width)
    col2 = ColumnDefinition()
    col2.Width = GridLength(1, GridUnitType.Star)
    grid.ColumnDefinitions.Add(col1)
    grid.ColumnDefinitions.Add(col2)

    label = TextBlock()
    label.Text = label_text
    label.VerticalAlignment = VerticalAlignment.Center
    Grid.SetColumn(label, 0)
    grid.Children.Add(label)

    control.VerticalAlignment = VerticalAlignment.Center
    control.HorizontalAlignment = HorizontalAlignment.Left
    control.MinWidth = 140
    Grid.SetColumn(control, 1)
    grid.Children.Add(control)
    return grid


XAML = u"""<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="房屋散水" Height="560" Width="520"
        WindowStartupLocation="CenterScreen" ResizeMode="CanResize">
    <ScrollViewer VerticalScrollBarVisibility="Auto">
        <StackPanel Margin="16" Name="root"/>
    </ScrollViewer>
</Window>"""


class ApronWindow(forms.WPFWindow):
    def __init__(self, floors, scope_text):
        forms.WPFWindow.__init__(self, XAML, literal_string=True)
        self._floors = floors
        root = self.FindName("root")

        title = TextBlock()
        title.Text = u"房屋散水"
        title.FontSize = 18
        title.FontWeight = FontWeights.Bold
        root.Children.Add(title)

        tip = TextBlock()
        tip.Text = u"{0}。沿楼板顶面外轮廓向外偏移生成，可 Ctrl+Z 整体撤销。".format(scope_text)
        tip.TextWrapping = TextWrapping.Wrap
        tip.Margin = Thickness(0, 6, 0, 10)
        tip.Opacity = 0.75
        root.Children.Add(tip)

        self.width_box = TextBox()
        self.width_box.Text = u"900"
        root.Children.Add(field_row(u"散水宽 (mm)", self.width_box))

        self.slope_box = TextBox()
        self.slope_box.Text = u"5"
        root.Children.Add(field_row(u"坡度 (%)", self.slope_box))

        self.thick_box = TextBox()
        self.thick_box.Text = u"60"
        root.Children.Add(field_row(u"厚度 (mm)", self.thick_box))

        self.inset_box = TextBox()
        self.inset_box.Text = u"0"
        root.Children.Add(field_row(u"内沿偏移 (mm)", self.inset_box))

        self.top_box = TextBox()
        self.top_box.Text = u"0"
        root.Children.Add(field_row(u"内边顶标高 (mm，绝对)", self.top_box))

        self.lip_chk = CheckBox()
        self.lip_chk.Content = u"外沿加 60×30 挡水"
        self.lip_chk.IsChecked = False
        self.lip_chk.Margin = Thickness(0, 8, 0, 4)
        root.Children.Add(self.lip_chk)

        self.preview = TextBlock()
        self.preview.TextWrapping = TextWrapping.Wrap
        self.preview.Margin = Thickness(0, 10, 0, 10)
        self.preview.MinHeight = 96
        root.Children.Add(self.preview)

        buttons = StackPanel()
        buttons.Orientation = Orientation.Horizontal
        buttons.HorizontalAlignment = HorizontalAlignment.Right
        buttons.Margin = Thickness(0, 6, 0, 0)

        self.btn_preview = Button()
        self.btn_preview.Content = u"预览"
        self.btn_preview.Width = 90
        self.btn_preview.Height = 30
        self.btn_preview.Margin = Thickness(0, 0, 8, 0)
        self.btn_preview.Click += self._on_preview
        buttons.Children.Add(self.btn_preview)

        self.btn_generate = Button()
        self.btn_generate.Content = u"生成"
        self.btn_generate.Width = 90
        self.btn_generate.Height = 30
        self.btn_generate.Margin = Thickness(0, 0, 8, 0)
        self.btn_generate.Click += self._on_generate
        buttons.Children.Add(self.btn_generate)

        self.btn_cancel = Button()
        self.btn_cancel.Content = u"取消"
        self.btn_cancel.Width = 90
        self.btn_cancel.Height = 30
        self.btn_cancel.Click += self._on_cancel
        buttons.Children.Add(self.btn_cancel)

        root.Children.Add(buttons)
        self._on_preview(None, None)

    # ------------------------------------------------------------ 参数
    def params(self):
        return {
            "width": to_float(self.width_box.Text, 900.0),
            "slope": to_float(self.slope_box.Text, 5.0),
            "thick": to_float(self.thick_box.Text, 60.0),
            "inset": to_float(self.inset_box.Text, 0.0),
            "top": to_float(self.top_box.Text, 0.0),
            "lip": bool(self.lip_chk.IsChecked),
        }

    def _measure(self, data):
        total_len = 0.0
        edges = 0
        for floor in self._floors:
            points = outline_of(floor, data["top"] * MM_TO_FT)
            if len(points) < 3:
                continue
            count = len(points)
            for index in range(count):
                p1 = points[index]
                p2 = points[(index + 1) % count]
                seg_len = math.hypot(p2.X - p1.X, p2.Y - p1.Y)
                if seg_len < 0.01:
                    continue
                total_len += seg_len
                edges += 1
        return total_len, edges

    # ------------------------------------------------------------ 事件
    def _on_preview(self, sender, args):
        data = self.params()
        try:
            total_len, edges = self._measure(data)
            length_m = total_len * FT_TO_MM / 1000.0
            area = length_m * data["width"] / 1000.0
            volume = area * data["thick"] / 1000.0
            drop = data["width"] * data["slope"] / 100.0
            self.preview.Text = (
                u"外边 {0} 段，总长 {1:.2f} m\n"
                u"散水宽 {2:.0f} mm，坡度 {3:.1f}%（外沿低 {4:.0f} mm），厚 {5:.0f} mm\n"
                u"投影面积 {6:.2f} m²，混凝土约 {7:.2f} m³\n"
                u"内边顶 {8:.3f} m，外沿顶 {9:.3f} m"
            ).format(
                edges, length_m, data["width"], data["slope"], drop,
                data["thick"], area, volume,
                data["top"] / 1000.0, (data["top"] - drop) / 1000.0,
            )
        except Exception:
            self.preview.Text = u"预览失败：\n{0}".format(traceback.format_exc())

    def _on_generate(self, sender, args):
        data = self.params()
        try:
            shape, count, total_len = generate(
                self._floors, data["width"], data["slope"], data["thick"],
                data["inset"], data["top"], data["lip"],
            )
        except Exception:
            write_log([traceback.format_exc()])
            forms.alert(u"生成失败：\n{0}".format(traceback.format_exc()), title=u"房屋散水")
            return
        if shape is None:
            forms.alert(u"没有生成任何散水，检查楼板是否有闭合的顶面轮廓。", title=u"房屋散水")
            return
        try:
            uidoc.Selection.SetElementIds([shape.Id])
        except Exception:
            pass
        length_m = total_len * FT_TO_MM / 1000.0
        self.Close()
        forms.alert(
            u"散水已生成：{0} 段，总长 {1:.2f} m。\n\n"
            u"生成为「常规模型」实体，标记「散水」，Tab 可选中、可直接删除。\n"
            u"改错了可以 Ctrl+Z 整体撤销。".format(count, length_m),
            title=u"房屋散水",
        )

    def _on_cancel(self, sender, args):
        self.Close()


def main():
    if doc is None:
        forms.alert(u"请先打开一个项目。", exitscript=True)
    floors, scope = pick_floors()
    if not floors:
        forms.alert(u"项目里没有可用的楼板（建筑楼板）。", title=u"房屋散水")
        return
    ApronWindow(floors, scope).ShowDialog()


try:
    main()
except Exception:
    write_log([traceback.format_exc()])
    script.get_output().print_md(u"散水脚本出错：\n```\n{0}\n```".format(traceback.format_exc()))
    forms.alert(u"脚本出错，详见输出窗口。", title=u"房屋散水")
