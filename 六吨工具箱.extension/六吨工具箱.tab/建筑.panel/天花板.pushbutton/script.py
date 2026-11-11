# -*- coding: utf-8 -*-
"""六吨工具箱 · 天花板

用法（不做任何拾取动作，避免 Revit 抢命令上下文）：
  1. 在平面里先选中房间（点房间文字，可 Ctrl 多选、也可框选房间）；
  2. 点「建筑 → 天花板」，设标高 / 完成面高 / 板厚 / 材质，点「生成」。

没选中任何东西时，按「当前视图里所有已放置的房间」生成。
选中了墙/梁/柱等（非房间）时，按它们的包围盒生成一块。
生成的是「天花板」类别的实体（Revit 2020 没有 API 创建原生天花板）。
"""
import traceback

import clr

clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

from System.Collections.Generic import List as DotNetList

from Autodesk.Revit.DB import (
    BuiltInCategory, ElementId, Level, Material, SolidOptions, XYZ, Line,
    CurveLoop, GeometryObject, GeometryCreationUtilities, DirectShape,
    SpatialElementBoundaryOptions,
    IFailuresPreprocessor, FailureProcessingResult, FailureSeverity, Transaction,
)

from System.Windows import Thickness, VerticalAlignment, TextWrapping, FontWeights
from System.Windows.Controls import (
    StackPanel, TextBlock, TextBox, Button, ComboBox, Orientation,
)

from pyrevit import revit, forms, script

doc = revit.doc
uidoc = revit.uidoc

FT_TO_MM = 304.8
MM_TO_FT = 1.0 / 304.8


def to_float(text, default):
    try:
        return float(unicode(text).strip())
    except Exception:
        return default


def type_name(element):
    if element is None:
        return u"<无>"
    for getter in ("Name", "get_Name"):
        try:
            value = getattr(element, getter)
            if callable(value):
                value = value()
            if value:
                return unicode(value)
        except Exception:
            pass
    return u"Id {0}".format(getattr(element.Id, "IntegerValue", 0))


def all_levels():
    items = []
    try:
        for level in FilteredElementCollector(doc).OfClass(Level):
            items.append(level)
    except Exception:
        pass
    items.sort(key=lambda lv: lv.Elevation)
    return items


def all_materials():
    items = []
    try:
        for material in FilteredElementCollector(doc).OfClass(Material):
            items.append(material)
    except Exception:
        pass
    items.sort(key=lambda m: type_name(m))
    return items


def all_rooms():
    items = []
    try:
        for element in FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_Rooms):
            try:
                if element.Area is None or element.Area <= 0:
                    continue
            except Exception:
                continue
            items.append(element)
    except Exception:
        pass
    return items


class WarningSuppressor(IFailuresPreprocessor):
    def PreprocessFailures(self, accessor):
        try:
            for message in accessor.GetFailureMessages():
                if message.GetSeverity() == FailureSeverity.Warning:
                    accessor.DeleteWarning(message)
        except Exception:
            pass
        return FailureProcessingResult.Continue


def make_ceiling(corners, base_z, thick_mm, material_id, label):
    curve_loop = CurveLoop()
    count = len(corners)
    for index in range(count):
        try:
            curve_loop.Append(Line.CreateBound(corners[index], corners[(index + 1) % count]))
        except Exception:
            return None
    loops = DotNetList[CurveLoop]()
    loops.Add(curve_loop)
    thickness = max(thick_mm, 10.0) * MM_TO_FT
    try:
        if material_id is not None and material_id.IntegerValue > 0:
            options = SolidOptions(material_id, ElementId.InvalidElementId)
            solid = GeometryCreationUtilities.CreateExtrusionGeometry(loops, XYZ.BasisZ, thickness, options)
        else:
            solid = GeometryCreationUtilities.CreateExtrusionGeometry(loops, XYZ.BasisZ, thickness)
    except Exception:
        return None

    shape = DotNetList[GeometryObject]()
    shape.Add(solid)
    try:
        element = DirectShape.CreateElement(doc, ElementId(int(BuiltInCategory.OST_Ceilings)))
    except Exception:
        element = DirectShape.CreateElement(doc, ElementId(int(BuiltInCategory.OST_GenericModel)))
    element.SetShape(shape)
    try:
        element.SetName(u"天花板-{0}".format(label))
    except Exception:
        pass
    return element


def rect_corners(min_x, min_y, max_x, max_y, base_z):
    return [XYZ(min_x, min_y, base_z), XYZ(max_x, min_y, base_z),
            XYZ(max_x, max_y, base_z), XYZ(min_x, max_y, base_z)]


def resolve_level(levels, index, auto_current):
    if auto_current:
        try:
            level = doc.ActiveView.GenLevel
            if level is not None:
                return level
        except Exception:
            pass
    if index >= 0 and index < len(levels):
        return levels[index]
    return levels[0] if levels else None


def room_corners(room, base_z):
    options = SpatialElementBoundaryOptions()
    try:
        segments = room.GetBoundarySegments(options)
    except Exception:
        segments = None
    if not segments:
        return None
    best = max(segments, key=len)
    corners = []
    for segment in best:
        try:
            point = segment.GetCurve().GetEndPoint(0)
        except Exception:
            continue
        corners.append(XYZ(point.X, point.Y, base_z))
    if len(corners) < 3:
        return None
    return corners


def generate(level, height_mm, thick_mm, material_id):
    base_z = level.Elevation + height_mm * MM_TO_FT

    try:
        ids = list(uidoc.Selection.GetElementIds())
    except Exception:
        ids = []

    picked_rooms = []
    others = []
    for element_id in ids:
        element = doc.GetElement(element_id)
        if element is None:
            continue
        is_room = False
        try:
            is_room = element.Category is not None and \
                element.Category.Id.IntegerValue == int(BuiltInCategory.OST_Rooms)
        except Exception:
            is_room = False
        if is_room:
            picked_rooms.append(element)
        else:
            others.append(element)

    rooms = picked_rooms if picked_rooms else ([] if others else all_rooms())
    used_all = not picked_rooms and not others

    created = 0
    skipped = 0
    transaction = Transaction(doc, u"生成天花板")
    transaction.Start()
    try:
        options = transaction.GetFailureHandlingOptions()
        options.SetFailuresPreprocessor(WarningSuppressor())
        options.SetClearAfterRollback(True)
        transaction.SetFailureHandlingOptions(options)
    except Exception:
        pass

    for room in rooms:
        corners = room_corners(room, base_z)
        if corners is None:
            skipped += 1
            continue
        try:
            element = make_ceiling(corners, base_z, thick_mm, material_id, u"房间")
        except Exception:
            element = None
        if element is None:
            skipped += 1
        else:
            created += 1

    if others and not picked_rooms:
        min_x = min_y = max_x = max_y = None
        for element in others:
            try:
                box = element.get_BoundingBox(None)
            except Exception:
                box = None
            if box is None:
                continue
            lo, hi = box.Min, box.Max
            min_x = lo.X if min_x is None else min(min_x, lo.X)
            min_y = lo.Y if min_y is None else min(min_y, lo.Y)
            max_x = hi.X if max_x is None else max(max_x, hi.X)
            max_y = hi.Y if max_y is None else max(max_y, hi.Y)
        if min_x is not None:
            inset = 50.0 * MM_TO_FT
            if max_x - min_x > 2 * inset and max_y - min_y > 2 * inset:
                min_x += inset
                min_y += inset
                max_x -= inset
                max_y -= inset
            try:
                element = make_ceiling(rect_corners(min_x, min_y, max_x, max_y, base_z),
                                       base_z, thick_mm, material_id, u"框选")
            except Exception:
                element = None
            if element is None:
                skipped += 1
            else:
                created += 1

    if not rooms and not others:
        transaction.RollBack()
        forms.alert(u"当前视图没有可用房间，也没有选中任何构件。\n"
                    u"先在平面里选中房间（点房间名字），再点这个按钮。", title=u"天花板")
        return

    try:
        if created:
            transaction.Commit()
        else:
            transaction.RollBack()
    except Exception:
        try:
            transaction.RollBack()
        except Exception:
            pass

    scope = u"项目全部房间" if used_all else u"选中的房间/构件"
    forms.alert(u"生成完成：成功 {0} 块，跳过 {1} 块。\n范围：{2}\n标高：{3}  完成面高 {4:.0f} mm  板厚 {5:.0f} mm".format(
        created, skipped, scope, type_name(level), height_mm, thick_mm),
        title=u"天花板")


XAML = u"""
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="天花板" Height="380" Width="540"
        WindowStartupLocation="CenterScreen">
  <StackPanel Margin="16" />
</Window>
"""


def run():
    levels = all_levels()
    if not levels:
        forms.alert(u"当前项目没有标高。", exitscript=True)
    materials = all_materials()

    window = forms.WPFWindow(XAML, literal_string=True)
    root = window.Content

    title = TextBlock()
    title.Text = u"天花板"
    title.FontSize = 20
    title.FontWeight = FontWeights.Bold
    title.Margin = Thickness(0, 0, 0, 4)
    root.Children.Add(title)

    hint = TextBlock()
    hint.Text = u"先在平面里选中房间（点房间名字，可多选），再点「生成」。\n没选中房间时，按当前视图里所有房间生成。"
    hint.TextWrapping = TextWrapping.Wrap
    hint.Margin = Thickness(0, 0, 0, 12)
    root.Children.Add(hint)

    def label(text):
        block = TextBlock()
        block.Text = text
        block.VerticalAlignment = VerticalAlignment.Center
        block.Margin = Thickness(0, 0, 6, 0)
        return block

    def row(items):
        panel = StackPanel()
        panel.Orientation = Orientation.Horizontal
        panel.Margin = Thickness(0, 3, 0, 3)
        for item in items:
            if not isinstance(item, TextBlock):
                item.Margin = Thickness(0, 0, 10, 0)
            panel.Children.Add(item)
        return panel

    level_box = ComboBox()
    level_box.Width = 230
    for level in levels:
        level_box.Items.Add(u"{0}  ({1:+.3f})".format(type_name(level),
                                                      level.Elevation * FT_TO_MM / 1000.0))
    level_box.SelectedIndex = 0
    try:
        current = doc.ActiveView.GenLevel
        for index, level in enumerate(levels):
            if level.Id.IntegerValue == current.Id.IntegerValue:
                level_box.SelectedIndex = index
                break
    except Exception:
        pass

    height_box = TextBox()
    height_box.Text = u"3000"
    height_box.Width = 60

    thick_box = TextBox()
    thick_box.Text = u"120"
    thick_box.Width = 60

    material_box = ComboBox()
    material_box.Width = 230
    material_box.Items.Add(u"<无材质>")
    for material in materials:
        material_box.Items.Add(type_name(material))
    material_box.SelectedIndex = 0

    summary = TextBlock()
    summary.TextWrapping = TextWrapping.Wrap
    summary.Margin = Thickness(0, 12, 0, 0)
    try:
        count = len(all_rooms())
        selected = len(list(uidoc.Selection.GetElementIds()))
        summary.Text = u"项目房间 {0} 间；当前选中 {1} 个图元。".format(count, selected)
    except Exception:
        summary.Text = u""

    buttons = StackPanel()
    buttons.Orientation = Orientation.Horizontal
    buttons.Margin = Thickness(0, 14, 0, 0)

    def on_generate(sender, args):
        index = level_box.SelectedIndex
        level = levels[index] if 0 <= index < len(levels) else levels[0]
        material_id = None
        material_index = material_box.SelectedIndex
        if material_index > 0 and material_index - 1 < len(materials):
            material_id = materials[material_index - 1].Id.IntegerValue
        try:
            window.Close()
        except Exception:
            pass
        generate(level, to_float(height_box.Text, 3000.0), to_float(thick_box.Text, 120.0), material_id)

    def on_close(sender, args):
        try:
            window.Close()
        except Exception:
            pass

    for text, handler, width in ((u"生成", on_generate, 120), (u"关闭", on_close, 90)):
        button = Button()
        button.Content = text
        button.Width = width
        button.Height = 34
        button.Margin = Thickness(0, 0, 10, 0)
        button.Click += handler
        buttons.Children.Add(button)

    root.Children.Add(row([label(u"标高"), level_box]))
    root.Children.Add(row([label(u"完成面高"), height_box, label(u"mm   板厚"), thick_box, label(u"mm")]))
    root.Children.Add(row([label(u"材质"), material_box]))
    root.Children.Add(buttons)
    root.Children.Add(summary)

    window.ShowDialog()


try:
    run()
except Exception:
    forms.alert(u"脚本出错：\n{0}".format(traceback.format_exc()[-700:]), title=u"天花板")
