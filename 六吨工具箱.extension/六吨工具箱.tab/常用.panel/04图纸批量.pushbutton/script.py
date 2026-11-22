# -*- coding: utf-8 -*-
"""图纸批量：按标高批量创建楼层平面，可选视图模板 / 比例 / 复制裁剪，并可自动套图框。"""
import os
import traceback

import clr  # noqa
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")
clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")

from System.Collections.Generic import List as DotNetList  # noqa: E402
from System.Windows import Visibility  # noqa: E402

from Autodesk.Revit.DB import (  # noqa: E402
    BoundingBoxXYZ,
    BuiltInCategory,
    BuiltInParameter,
    ElementId,
    FilteredElementCollector,
    Level,
    Transaction,
    View,
    ViewFamily,
    ViewFamilyType,
    ViewPlan,
    ViewSheet,
    Viewport,
    XYZ,
)
from Autodesk.Revit.UI import IExternalCommand  # noqa: E402

from pyrevit import forms, revit, script  # noqa: E402

doc = revit.doc
uidoc = revit.uidoc
output = script.get_output()

FT_TO_MM = 304.8
LOG_PATH = r"C:\Users\cyl\Documents\deepseek-harness\default-workspace\_图纸批量日志.txt"
SCALES = [50, 100, 150, 200, 300]


def log_line(text):
    try:
        with open(LOG_PATH, "ab") as fh:
            fh.write((u"%s\n" % text).encode("utf-8"))
    except Exception:
        pass


def safe_name(element, fallback=u""):
    if element is None:
        return fallback
    try:
        name = element.Name
        if name:
            return name
    except Exception:
        pass
    try:
        return u"Id%s" % element.Id.IntegerValue
    except Exception:
        return fallback


def all_levels():
    rows = []
    for level in FilteredElementCollector(doc).OfClass(Level):
        try:
            rows.append((level, level.Elevation * FT_TO_MM))
        except Exception:
            continue
    rows.sort(key=lambda item: item[1])
    return rows


def floor_plan_type_id():
    for view_type in FilteredElementCollector(doc).OfClass(ViewFamilyType):
        try:
            if view_type.ViewFamily == ViewFamily.FloorPlan:
                return view_type.Id
        except Exception:
            continue
    return ElementId.InvalidElementId


def view_templates():
    rows = []
    for view in FilteredElementCollector(doc).OfClass(View):
        try:
            if view.IsTemplate and view.ViewType in (ViewType.FloorPlan, ViewType.CeilingPlan,
                                                    ViewType.EngineeringPlan, ViewType.AreaPlan):
                rows.append(view)
        except Exception:
            continue
    rows.sort(key=lambda v: safe_name(v).lower())
    return rows


def title_blocks():
    rows = []
    for symbol in FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_TitleBlocks) \
            .WhereElementIsElementType():
        try:
            rows.append(symbol)
        except Exception:
            continue
    rows.sort(key=lambda s: safe_name(s).lower())
    return rows


def existing_plans():
    """{level_id: view} 已有楼层平面。"""
    mapping = {}
    for view in FilteredElementCollector(doc).OfClass(ViewPlan):
        try:
            if view.IsTemplate:
                continue
            level = view.GenLevel
            if level is not None:
                mapping[level.Id.IntegerValue] = view
        except Exception:
            continue
    return mapping


def copy_crop(source, target):
    try:
        if not source.CropBoxActive:
            return
        box = source.CropBox
        new_box = BoundingBoxXYZ()
        new_box.Min = box.Min
        new_box.Max = box.Max
        new_box.Transform = box.Transform
        target.CropBox = new_box
        target.CropBoxActive = True
        try:
            target.CropBoxVisible = source.CropBoxVisible
        except Exception:
            pass
    except Exception:
        log_line(u"[crop] 复制裁剪失败：%s" % traceback.format_exc().splitlines()[-1])


def _usable_box(sheet, lr_mm, tb_mm):
    """图纸上可用区域（单位：英尺，paper 坐标）。"""
    outline = sheet.Outline
    lr = lr_mm / FT_TO_MM
    tb = tb_mm / FT_TO_MM
    width = (outline.Max.U - outline.Min.U) - lr * 2.0
    height = (outline.Max.V - outline.Min.V) - tb * 2.0
    if width <= 0.1 or height <= 0.1:
        return None
    return outline, width, height


CANDIDATE_SCALES = [20, 25, 30, 40, 50, 60, 75, 100, 125, 150, 200, 250, 300, 400, 500]


def _fit_scale(view, usable_w, usable_h):
    """按裁剪框尺寸挑一个能装进可用区域的整数比例（1:N）。"""
    try:
        box = view.CropBox
        model_w = abs(box.Max.X - box.Min.X)
        model_h = abs(box.Max.Y - box.Min.Y)
    except Exception:
        return None
    if model_w <= 0 or model_h <= 0:
        return None
    need = max(model_w / usable_w, model_h / usable_h)
    for candidate in CANDIDATE_SCALES:
        if candidate >= need:
            return candidate
    return CANDIDATE_SCALES[-1]


def _place_viewport(vp, view, sheet, lr_mm, tb_mm, title_mm, auto_fit):
    """按边距把视图挪进图框，必要时自动挑比例。"""
    box = _usable_box(sheet, lr_mm, tb_mm)
    if box is None:
        return
    outline, usable_w, usable_h = box
    if auto_fit:
        target = _fit_scale(view, usable_w, usable_h)
        if target:
            try:
                view.Scale = target
            except Exception:
                pass
    center = XYZ((outline.Min.U + outline.Max.U) * 0.5,
                 (outline.Min.V + outline.Max.V) * 0.5, 0.0)
    shift_v = (title_mm / FT_TO_MM) - (tb_mm / FT_TO_MM) * 0.0
    try:
        vp.SetBoxCenter(XYZ(center.X, center.Y + shift_v, 0.0))
    except Exception:
        pass


def sheet_center(sheet):
    try:
        outline = sheet.Outline
        return XYZ((outline.Min.U + outline.Max.U) * 0.5,
                   (outline.Min.V + outline.Max.V) * 0.5, 0.0)
    except Exception:
        return XYZ(0, 0, 0)


def generate(level_ids, template_id, scale, copy_from_view, make_sheet,
             title_block_id, number_prefix, margins=None):
    """margins: dict(lr=左右缩减mm, tb=上下缩减mm, title=标题下移mm, fit=bool 自动适配比例)"""
    margins = margins or {}
    lr_mm = float(margins.get("lr", 0.0))
    tb_mm = float(margins.get("tb", 0.0))
    title_mm = float(margins.get("title", 0.0))
    auto_fit = bool(margins.get("fit", False))
    made = 0
    skipped = 0
    sheets = 0
    failures = []
    plan_type_id = floor_plan_type_id()
    if plan_type_id == ElementId.InvalidElementId:
        return 0, 0, 0, [u"项目里没有「楼层平面」视图类型，无法创建平面视图。"]

    transaction = Transaction(doc, u"图纸批量")
    transaction.Start()
    try:
        plans = existing_plans()
        index = 0
        for level_id in level_ids:
            level = doc.GetElement(level_id)
            if level is None:
                continue
            if plans.get(level_id.IntegerValue) is not None:
                skipped += 1
                continue
            try:
                view = ViewPlan.Create(doc, plan_type_id, level_id)
            except Exception:
                failures.append(u"%s 建平面失败：%s" % (safe_name(level), traceback.format_exc().splitlines()[-1]))
                continue
            made += 1
            try:
                view.Name = safe_name(level)
            except Exception:
                pass
            if template_id is not None and template_id != ElementId.InvalidElementId:
                try:
                    view.ViewTemplateId = template_id
                except Exception:
                    log_line(u"[template] 套模板失败：%s" % traceback.format_exc().splitlines()[-1])
            try:
                view.Scale = scale
            except Exception:
                pass
            if copy_from_view is not None:
                copy_crop(copy_from_view, view)
            if make_sheet and title_block_id is not None and title_block_id != ElementId.InvalidElementId:
                try:
                    sheet = ViewSheet.Create(doc, title_block_id)
                    index += 1
                    try:
                        sheet.SheetNumber = u"%s%02d" % (number_prefix or u"A", index)
                    except Exception:
                        pass
                    try:
                        sheet.Name = u"%s 平面" % safe_name(level)
                    except Exception:
                        pass
                    vp = Viewport.Create(doc, sheet.Id, view.Id, sheet_center(sheet))
                    try:
                        _place_viewport(vp, view, sheet, lr_mm, tb_mm, title_mm, auto_fit)
                    except Exception:
                        log_line(u"[margin] %s" % traceback.format_exc().splitlines()[-1])
                    sheets += 1
                except Exception:
                    failures.append(u"%s 出图失败：%s" % (safe_name(level), traceback.format_exc().splitlines()[-1]))
        transaction.Commit()
    except Exception:
        transaction.RollBack()
        failures.append(traceback.format_exc().splitlines()[-1])
    log_line(u"[gen] 新建 %d，跳过 %d，出图 %d，失败 %d" % (made, skipped, sheets, len(failures)))
    return made, skipped, sheets, failures


SHELL_XAML = u"""
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="图纸批量" Height="660" Width="640" MinWidth="600" MinHeight="540"
        WindowStartupLocation="CenterScreen" ResizeMode="CanResize">
  <Grid Margin="16">
    <Grid.RowDefinitions>
      <RowDefinition Height="Auto"/>
      <RowDefinition Height="Auto"/>
      <RowDefinition Height="*"/>
      <RowDefinition Height="Auto"/>
      <RowDefinition Height="Auto"/>
    </Grid.RowDefinitions>

    <StackPanel Grid.Row="0" Orientation="Horizontal" Margin="0,0,0,8">
      <TextBlock Text="视图模板" VerticalAlignment="Center" Width="86"/>
      <ComboBox x:Name="templateBox" Width="240" Height="28" VerticalContentAlignment="Center"/>
      <TextBlock Text="比例 1:" VerticalAlignment="Center" Margin="14,0,6,0"/>
      <ComboBox x:Name="scaleBox" Width="86" Height="28" VerticalContentAlignment="Center"/>
    </StackPanel>

    <StackPanel Grid.Row="1" Orientation="Horizontal" Margin="0,0,0,8">
      <CheckBox x:Name="copyCropBox" Content="复制当前视图的裁剪框" IsChecked="True"
                VerticalAlignment="Center" Margin="0,0,16,0"/>
      <CheckBox x:Name="sheetBox" Content="同时套图框出图" VerticalAlignment="Center"/>
      <TextBlock Text="图号前缀" VerticalAlignment="Center" Margin="14,0,6,0"/>
      <TextBox x:Name="prefixBox" Text="A" Width="56" Height="28"/>
    </StackPanel>

    <Border Grid.Row="2" BorderBrush="#FFBFC8D6" BorderThickness="1" CornerRadius="6">
      <ListBox x:Name="levelList" SelectionMode="Extended" Margin="2"/>
    </Border>

    <WrapPanel Grid.Row="3" Margin="0,8,0,0">
      <TextBlock Text="图框" VerticalAlignment="Center" Width="44"/>
      <ComboBox x:Name="titleBlockBox" Width="240" Height="28" VerticalContentAlignment="Center"/>
      <Button x:Name="btnAll" Content="全选" Width="64" Height="28" Margin="12,0,12,0"/>
      <TextBlock Text="左右缩减" VerticalAlignment="Center" Margin="0,0,4,0"/>
      <TextBox x:Name="lrBox" Text="0" Width="52" Height="28" VerticalContentAlignment="Center"/>
      <TextBlock Text="上下" VerticalAlignment="Center" Margin="8,0,4,0"/>
      <TextBox x:Name="tbBox" Text="0" Width="52" Height="28" VerticalContentAlignment="Center"/>
      <TextBlock Text="标题下移" VerticalAlignment="Center" Margin="8,0,4,0"/>
      <TextBox x:Name="titleBox" Text="0" Width="52" Height="28" VerticalContentAlignment="Center"/>
      <CheckBox x:Name="fitBox" Content="自动适配比例" VerticalAlignment="Center" Margin="10,0,0,0"/>
    </WrapPanel>

    <StackPanel Grid.Row="4" Orientation="Horizontal" HorizontalAlignment="Right" Margin="0,12,0,0">
      <Button x:Name="btnGo" Content="生成" Width="104" Height="34" FontWeight="Bold" Margin="0,0,8,0"/>
      <Button x:Name="btnClose" Content="关闭" Width="88" Height="34"/>
    </StackPanel>
  </Grid>
</Window>
"""


def _to_float(text, default):
    try:
        return float(text)
    except Exception:
        return default


class BatchWindow(forms.WPFWindow):
    def __init__(self, levels, templates, blocks):
        forms.WPFWindow.__init__(self, SHELL_XAML, literal_string=True)
        self.levels = levels
        self.action = None
        self.result = None

        self.template_box = self.FindName("templateBox")
        self.scale_box = self.FindName("scaleBox")
        self.level_list = self.FindName("levelList")
        self.title_block_box = self.FindName("titleBlockBox")

        self.template_box.Items.Add(u"<不用模板>")
        for view in templates:
            self.template_box.Items.Add(safe_name(view))
        self.template_box.SelectedIndex = 0

        for value in SCALES:
            self.scale_box.Items.Add(u"%d" % value)
        self.scale_box.SelectedIndex = 1

        self.title_block_box.Items.Add(u"<不建图纸>")
        for symbol in blocks:
            self.title_block_box.Items.Add(safe_name(symbol))
        self.title_block_box.SelectedIndex = 0

        for level, elevation in levels:
            self.level_list.Items.Add(u"%s   (%+.3f m)" % (safe_name(level), elevation / 1000.0))

        self.FindName("btnAll").Click += self._on_all
        self.FindName("btnGo").Click += self._on_go
        self.FindName("btnClose").Click += self._on_close

    def _on_all(self, sender, args):
        self.level_list.SelectAll()

    def _on_close(self, sender, args):
        self.action = None
        self.Close()

    def _on_go(self, sender, args):
        picked = list(self.level_list.SelectedItems)
        if not picked:
            forms.alert(u"请先在列表里选中要生成平面的标高（可多选）。", title=u"图纸批量")
            return
        level_ids = []
        for index, item in enumerate(picked):
            position = self.level_list.Items.IndexOf(item)
            if position >= 0 and position < len(self.levels):
                level_ids.append(self.levels[position][0].Id)
        template_id = ElementId.InvalidElementId
        if self.template_box.SelectedIndex > 0:
            templates = view_templates()
            picked_template = templates[self.template_box.SelectedIndex - 1]
            template_id = picked_template.Id
        scale = SCALES[max(0, self.scale_box.SelectedIndex)]
        make_sheet = bool(self.FindName("sheetBox").IsChecked)
        block_id = ElementId.InvalidElementId
        if make_sheet and self.title_block_box.SelectedIndex > 0:
            blocks = title_blocks()
            block_id = blocks[self.title_block_box.SelectedIndex - 1].Id
        copy_from = None
        if bool(self.FindName("copyCropBox").IsChecked):
            try:
                if isinstance(uidoc.ActiveView, ViewPlan):
                    copy_from = uidoc.ActiveView
            except Exception:
                copy_from = None
        prefix_text = self.FindName("prefixBox").Text or u"A"
        margins = {
            "lr": _to_float(self.FindName("lrBox").Text, 0.0),
            "tb": _to_float(self.FindName("tbBox").Text, 0.0),
            "title": _to_float(self.FindName("titleBox").Text, 0.0),
            "fit": bool(self.FindName("fitBox").IsChecked),
        }
        self.result = generate(level_ids, template_id, scale, copy_from, make_sheet,
                               block_id, prefix_text, margins)
        self.action = "done"
        self.Close()


def main():
    if doc is None:
        forms.alert(u"请先打开一个项目。", exitscript=True)
        return
    levels = all_levels()
    if not levels:
        forms.alert(u"项目里没有标高。", title=u"图纸批量")
        return
    templates = view_templates()
    blocks = title_blocks()
    window = BatchWindow(levels, templates, blocks)
    window.ShowDialog()
    if window.action != "done" or window.result is None:
        return
    made, skipped, sheets, failures = window.result
    text = u"新建平面 %d 个，跳过（已存在）%d 个，出图 %d 张。" % (made, skipped, sheets)
    if failures:
        text += u"\n失败 %d 条：\n%s" % (len(failures), u"\n".join(failures[:5]))
    output.print_md(u"### 图纸批量\n" + text)
    forms.alert(text, title=u"图纸批量")


try:
    main()
except Exception:
    output.print_md(u"脚本出错：\n```\n{0}\n```".format(traceback.format_exc()))
    forms.alert(u"脚本出错：\n%s" % traceback.format_exc()[-600:], title=u"图纸批量")
