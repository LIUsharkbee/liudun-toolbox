# -*- coding: utf-8 -*-
"""内外墙刷面

给墙的内侧 / 外侧分别刷不同的颜色或材质（Revit 油漆工具，Document.Paint）。

用法：
  1. 在模型里选中若干墙（可以跨楼层、跨类型）；
  2. 选「内侧材质」「外侧材质」；
  3. 点「刷到选中墙」。

内外侧判断：用墙的 Orientation（墙外法向）与每个竖直面的法向点积，同向为外侧、反向为内侧。
如果某面判断反了（墙被 Flip 过），勾「交换内外侧」再刷一次即可。

「清除油漆」会把选中墙所有竖直面的油漆去掉。
「新建纯色材质」可以直接选一个颜色，生成一个纯色材质再刷。

注意：真实样式下，材质要有「外观」资源才显示颜色 / 纹理；
      油漆只改面层材质，不改墙类型，也不增加几何。
"""

import os

from pyrevit import forms, revit

from Autodesk.Revit.DB import (
    BuiltInCategory,
    Color,
    FilteredElementCollector,
    Material,
    Options,
    Transaction,
    UV,
    ViewDetailLevel,
    Wall,
    XYZ,
)
from System.Windows import HorizontalAlignment, TextWrapping, Thickness, FontWeights
from System.Windows.Controls import (
    Button,
    CheckBox,
    ComboBox,
    Orientation,
    ScrollViewer,
    StackPanel,
    TextBlock,
)
from System.Windows.Media import Brushes
from System.Windows.Forms import ColorDialog, DialogResult

doc = revit.doc
uidoc = revit.uidoc


# ---------------------------------------------------------------- 材质
def all_materials():
    result = []
    for element in FilteredElementCollector(doc).OfClass(Material):
        try:
            result.append(element)
        except Exception:
            continue
    result.sort(key=lambda m: m.Name)
    return result


def new_solid_material(name, color):
    material = Material.Create(doc, name)
    try:
        material.Color = color
    except Exception:
        pass
    try:
        material.SurfaceBackgroundPatternColor = color
    except Exception:
        pass
    return material


def apply_appearance_color(material, color):
    """尽量把颜色写进外观资源（真实样式下才看得见）。"""
    try:
        asset_id = material.AppearanceAssetId
        if asset_id is None or asset_id.IntegerValue < 0:
            return False
        asset_element = doc.GetElement(asset_id)
        if asset_element is None:
            return False
        asset = asset_element.GetRenderingAsset()
        if asset is None:
            return False
        for index in range(asset.Size):
            prop = asset[index]
            if prop.Name == u"generic_diffuse" or prop.Name == u"diffuse":
                value = prop.GetDoubleArrayValue()
                if value is not None:
                    value.SetValueAsColor(color)
                    return True
    except Exception:
        return False
    return False


# ---------------------------------------------------------------- 几何
def vertical_faces(wall):
    """返回 [(face, 法向)]，只取竖直面。"""
    result = []
    options = Options()
    options.ComputeReferences = True
    options.DetailLevel = ViewDetailLevel.Fine
    options.IncludeNonVisibleObjects = False
    try:
        geometry = wall.get_Geometry(options)
    except Exception:
        return result
    if geometry is None:
        return result

    for obj in geometry:
        try:
            faces = obj.Faces
        except Exception:
            continue
        if faces is None:
            continue
        for face in faces:
            try:
                normal = face.ComputeNormal(UV(0.5, 0.5))
                area = face.Area
            except Exception:
                continue
            if abs(normal.Z) > 0.5:
                continue
            if area < 0.01:
                continue
            result.append((face, normal))
    return result


def outward_normal(wall):
    try:
        vector = wall.Orientation
        if vector is not None and (abs(vector.X) + abs(vector.Y)) > 1e-9:
            return XYZ(vector.X, vector.Y, 0).Normalize()
    except Exception:
        pass
    return None


def classify(wall, swap=False):
    """返回 (外侧faces, 内侧faces)。"""
    outer = []
    inner = []
    normal = outward_normal(wall)
    if normal is None:
        return outer, inner
    for face, face_normal in vertical_faces(wall):
        flat = XYZ(face_normal.X, face_normal.Y, 0)
        if abs(flat.X) + abs(flat.Y) < 1e-9:
            continue
        flat = flat.Normalize()
        dot = flat.DotProduct(normal)
        if swap:
            dot = -dot
        if dot > 0.2:
            outer.append(face)
        elif dot < -0.2:
            inner.append(face)
    return outer, inner


def selected_walls():
    walls = []
    try:
        for element_id in uidoc.Selection.GetElementIds():
            element = doc.GetElement(element_id)
            if isinstance(element, Wall):
                walls.append(element)
    except Exception:
        pass
    if walls:
        return walls
    return [e for e in FilteredElementCollector(doc).OfCategory(
        BuiltInCategory.OST_Walls).WhereElementIsNotElementType()
        if isinstance(e, Wall)]


# ---------------------------------------------------------------- 执行
def painted_material(wall, face):
    """读回油漆结果；没刷上返回 None。"""
    try:
        material_id = doc.GetPaintedMaterial(wall.Id, face)
        if material_id is None:
            return None
        if material_id.IntegerValue < 0:
            return None
        return doc.GetElement(material_id)
    except Exception:
        return None


def paint_faces(walls, outer_material, inner_material, swap):
    done = {"outer": 0, "inner": 0, "walls": 0}
    failures = []
    transaction = Transaction(doc, u"内外墙刷面")
    transaction.Start()
    try:
        for wall in walls:
            outer, inner = classify(wall, swap)
            if not outer and not inner:
                failures.append((wall.Id.IntegerValue, u"没有找到竖直面"))
                continue
            touched = False
            for face in outer:
                if outer_material is None:
                    continue
                try:
                    doc.Paint(wall.Id, face, outer_material.Id)
                    check = painted_material(wall, face)
                    if check is None:
                        failures.append((wall.Id.IntegerValue, u"外侧刷了但读不回来（该面可能被墙的洞口/幕墙占用）"))
                        continue
                    done["outer"] += 1
                    touched = True
                except Exception as ex:
                    failures.append((wall.Id.IntegerValue, u"外侧：{0}".format(ex)))
            for face in inner:
                if inner_material is None:
                    continue
                try:
                    doc.Paint(wall.Id, face, inner_material.Id)
                    check = painted_material(wall, face)
                    if check is None:
                        failures.append((wall.Id.IntegerValue, u"内侧刷了但读不回来（该面可能被墙的洞口/幕墙占用）"))
                        continue
                    done["inner"] += 1
                    touched = True
                except Exception as ex:
                    failures.append((wall.Id.IntegerValue, u"内侧：{0}".format(ex)))
            if touched:
                done["walls"] += 1
    except Exception as ex:
        if transaction.HasStarted():
            transaction.RollBack()
        forms.alert(u"刷面失败，已整体回滚：\n{0}".format(ex),
                    title=u"内外墙刷面", warn_icon=True)
        return None

    transaction.Commit()
    return done, failures


def view_report(walls):
    """解释「为什么刷了看不见」：显示样式 / 过滤器 / 类别替换 / 油漆读回。"""
    lines = []
    view = doc.ActiveView
    try:
        lines.append(u"视图：{0}".format(view.Name))
    except Exception:
        lines.append(u"视图：<读取失败>")
    try:
        lines.append(u"显示样式：{0}".format(view.DisplayStyle))
    except Exception:
        pass
    try:
        lines.append(u"视觉样式：{0}".format(view.DisplayStyle))
    except Exception:
        pass

    filter_count = 0
    try:
        filters = list(view.GetFilters())
        filter_count = len(filters)
        lines.append(u"视图过滤器：{0} 个".format(filter_count))
        for index, filter_id in enumerate(filters[:10]):
            element = doc.GetElement(filter_id)
            name = element.Name if element is not None else u"?"
            lines.append(u"  · {0}".format(name))
    except Exception:
        lines.append(u"视图过滤器：读取失败")

    try:
        categories = doc.Settings.Categories
        wall_category = categories.get_Item(BuiltInCategory.OST_Walls)
        settings = view.GetCategoryOverrides(wall_category.Id)
        colors = []
        if settings.SurfaceForegroundPatternColor.IsValid:
            color = settings.SurfaceForegroundPatternColor
            colors.append(u"表面前景色 RGB({0},{1},{2})".format(color.Red, color.Green, color.Blue))
        if settings.SurfaceBackgroundPatternColor.IsValid:
            color = settings.SurfaceBackgroundPatternColor
            colors.append(u"表面背景色 RGB({0},{1},{2})".format(color.Red, color.Green, color.Blue))
        if settings.Transparency > 0:
            colors.append(u"透明度 {0}%".format(settings.Transparency))
        if colors:
            lines.append(u"墙类别在视图里被替换：" + u"；".join(colors))
            lines.append(u"  → 类别替换会盖住油漆颜色，这就是「刷了看不见」的常见原因")
        else:
            lines.append(u"墙类别：视图里没有图形替换")
    except Exception:
        pass

    lines.append(u"")
    lines.append(u"选中墙的油漆读回：")
    for wall in walls[:3]:
        outer, inner = classify(wall, False)
        outer_names = []
        inner_names = []
        for face in outer:
            material = painted_material(wall, face)
            outer_names.append(material.Name if material is not None else u"未刷")
        for face in inner:
            material = painted_material(wall, face)
            inner_names.append(material.Name if material is not None else u"未刷")
        lines.append(u"  墙 {0}：外侧 {1}；内侧 {2}".format(
            wall.Id.IntegerValue,
            u"、".join(outer_names) if outer_names else u"无可用面",
            u"、".join(inner_names) if inner_names else u"无可用面"))
    if len(walls) > 3:
        lines.append(u"  ……其余 {0} 面墙省略".format(len(walls) - 3))
    return u"\n".join(lines)


def clear_faces(walls):
    count = 0
    failures = []
    transaction = Transaction(doc, u"清除油漆")
    transaction.Start()
    try:
        for wall in walls:
            for face, face_normal in vertical_faces(wall):
                try:
                    doc.RemovePaint(wall.Id, face)
                    count += 1
                except Exception as ex:
                    failures.append((wall.Id.IntegerValue, u"{0}".format(ex)))
    except Exception as ex:
        if transaction.HasStarted():
            transaction.RollBack()
        forms.alert(u"清除失败，已整体回滚：\n{0}".format(ex),
                    title=u"内外墙刷面", warn_icon=True)
        return None
    transaction.Commit()
    return count, failures


# ---------------------------------------------------------------- 界面
def label(text, width=0):
    block = TextBlock()
    block.Text = text
    block.Margin = Thickness(0, 0, 6, 0)
    if width:
        block.Width = width
    return block


def section(title, content):
    panel = StackPanel()
    heading = TextBlock()
    heading.Text = title
    heading.FontWeight = FontWeights.Bold
    heading.Margin = Thickness(0, 10, 0, 4)
    panel.Children.Add(heading)
    panel.Children.Add(content)
    return panel


def button(text, width=100):
    control = Button()
    control.Content = text
    control.Width = width
    control.Margin = Thickness(0, 0, 8, 0)
    control.Padding = Thickness(6, 4, 6, 4)
    return control


class FaceWindow(forms.WPFWindow):
    def __init__(self, materials):
        forms.WPFWindow.__init__(self, """
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        Title="内外墙刷面" Height="560" Width="640"
        WindowStartupLocation="CenterScreen" ResizeMode="CanResize">
  <ScrollViewer VerticalScrollBarVisibility="Auto">
    <StackPanel Margin="14" />
  </ScrollViewer>
</Window>
""", literal_string=True)
        self.result = None
        self.materials = materials
        root = self.Content.Content

        self.inner_box = self._material_box()
        self.outer_box = self._material_box()
        if len(materials) > 0:
            self.inner_box.SelectedIndex = 0
            self.outer_box.SelectedIndex = min(1, len(materials) - 1)

        inner_row = StackPanel()
        inner_row.Orientation = Orientation.Horizontal
        inner_row.Children.Add(label(u"内侧材质", 90))
        inner_row.Children.Add(self.inner_box)

        outer_row = StackPanel()
        outer_row.Orientation = Orientation.Horizontal
        outer_row.Children.Add(label(u"外侧材质", 90))
        outer_row.Children.Add(self.outer_box)

        self.swap = CheckBox()
        self.swap.Content = u"交换内外侧（墙被 Flip 过时勾选）"
        self.swap.IsChecked = False

        self.summary = TextBlock()
        self.summary.TextWrapping = TextWrapping.Wrap
        self.summary.Foreground = Brushes.Gray
        self.summary.Margin = Thickness(0, 8, 0, 4)

        picker = StackPanel()
        picker.Orientation = Orientation.Horizontal
        new_button = button(u"新建纯色材质…", 130)
        new_button.Click += self._on_new_material
        refresh = button(u"刷新材质列表", 110)
        refresh.Click += self._on_refresh
        picker.Children.Add(new_button)
        picker.Children.Add(refresh)

        root.Children.Add(section(u"① 材质", self._wrap(inner_row, outer_row, self.swap, picker)))
        root.Children.Add(section(u"② 说明", self.summary))

        hint = TextBlock()
        hint.Text = (u"· 先在模型里选中墙，再点「刷到选中墙」；没有选中时对全部墙生效。\n"
                     u"· 油漆只改面层材质，不改墙类型、不增加几何；真实样式下材质需要有外观资源。\n"
                     u"· 内外侧按墙的 Orientation 判断；判断反了勾「交换内外侧」。")
        hint.TextWrapping = TextWrapping.Wrap
        hint.Foreground = Brushes.Gray
        hint.Margin = Thickness(0, 4, 0, 4)
        root.Children.Add(hint)

        actions = StackPanel()
        actions.Orientation = Orientation.Horizontal
        actions.HorizontalAlignment = HorizontalAlignment.Right
        diagnose = button(u"诊断视图", 90)
        diagnose.Click += self._on_diagnose
        apply = button(u"刷到选中墙", 110)
        apply.Click += self._on_apply
        clear = button(u"清除油漆", 100)
        clear.Click += self._on_clear
        cancel = button(u"取消", 80)
        cancel.Click += self._on_cancel
        actions.Children.Add(diagnose)
        actions.Children.Add(apply)
        actions.Children.Add(clear)
        actions.Children.Add(cancel)
        root.Children.Add(actions)

        self._update_summary()

    def _wrap(self, *items):
        panel = StackPanel()
        for item in items:
            panel.Children.Add(item)
        return panel

    def _material_box(self):
        box = ComboBox()
        box.Width = 320
        box.Margin = Thickness(0, 3, 0, 3)
        for material in self.materials:
            box.Items.Add(material.Name)
        return box

    def _update_summary(self):
        walls = selected_walls()
        try:
            in_selection = len(uidoc.Selection.GetElementIds()) > 0
        except Exception:
            in_selection = False
        scope = u"选中的 {0} 面墙".format(len(walls)) if in_selection else u"项目里全部 {0} 面墙".format(len(walls))
        self.summary.Text = u"当前范围：{0}；材质库 {1} 个。".format(scope, len(self.materials))

    def _current(self, box):
        index = box.SelectedIndex
        if index < 0 or index >= len(self.materials):
            return None
        return self.materials[index]

    def _on_refresh(self, sender, args):
        self.materials = all_materials()
        for box in (self.inner_box, self.outer_box):
            box.Items.Clear()
            for material in self.materials:
                box.Items.Add(material.Name)
            if self.materials:
                box.SelectedIndex = 0
        self._update_summary()

    def _on_new_material(self, sender, args):
        dialog = ColorDialog()
        dialog.FullOpen = True
        if dialog.ShowDialog() != DialogResult.OK:
            return
        color = dialog.Color
        rgb = Color(color.R, color.G, color.B)
        name = u"纯色-{0:02X}{1:02X}{2:02X}".format(color.R, color.G, color.B)
        transaction = Transaction(doc, u"新建纯色材质")
        transaction.Start()
        try:
            material = new_solid_material(name, rgb)
            apply_appearance_color(material, rgb)
            transaction.Commit()
        except Exception as ex:
            if transaction.HasStarted():
                transaction.RollBack()
            forms.alert(u"新建材质失败：\n{0}".format(ex), title=u"内外墙刷面", warn_icon=True)
            return
        self.materials = all_materials()
        target_id = material.Id.IntegerValue
        for box in (self.inner_box, self.outer_box):
            box.Items.Clear()
            for item in self.materials:
                box.Items.Add(item.Name)
            index = 0
            for position, item in enumerate(self.materials):
                if item.Id.IntegerValue == target_id:
                    index = position
                    break
            box.SelectedIndex = index

    def _on_apply(self, sender, args):
        inner = self._current(self.inner_box)
        outer = self._current(self.outer_box)
        if inner is None and outer is None:
            forms.alert(u"请先选择至少一个材质。", title=u"内外墙刷面", warn_icon=True)
            return
        self.result = ("apply", inner, outer, bool(self.swap.IsChecked))
        self.Close()

    def _on_clear(self, sender, args):
        self.result = ("clear", None, None, False)
        self.Close()

    def _on_diagnose(self, sender, args):
        walls = selected_walls()
        forms.alert(view_report(walls), title=u"内外墙刷面 · 诊断")

    def _on_cancel(self, sender, args):
        self.result = None
        self.Close()


# ---------------------------------------------------------------- 入口
def main():
    walls = selected_walls()
    if not walls:
        forms.alert(u"项目里没有找到墙。", title=u"内外墙刷面", warn_icon=True)
        return

    window = FaceWindow(all_materials())
    window.ShowDialog()
    if not window.result:
        return

    action, inner, outer, swap = window.result
    if action == "clear":
        outcome = clear_faces(walls)
        if outcome is None:
            return
        count, failures = outcome
        message = u"已清除 {0} 个面的油漆。".format(count)
        if failures:
            message += u"\n失败 {0} 个（前 5 条）：".format(len(failures))
            for element_id, text in failures[:5]:
                message += u"\n  [{0}] {1}".format(element_id, text)
        forms.alert(message, title=u"内外墙刷面")
        return

    outcome = paint_faces(walls, outer, inner, swap)
    if outcome is None:
        return
    done, failures = outcome
    message = (u"处理墙 {0} 面：外侧刷了 {1} 个面，内侧刷了 {2} 个面。".format(
        done["walls"], done["outer"], done["inner"]))
    if failures:
        message += u"\n失败 {0} 条（前 5 条）：".format(len(failures))
        for element_id, text in failures[:5]:
            message += u"\n  [{0}] {1}".format(element_id, text)
    message += u"\n\n改错了可以 Ctrl+Z 整体撤销。"
    forms.alert(message, title=u"内外墙刷面")


main()
