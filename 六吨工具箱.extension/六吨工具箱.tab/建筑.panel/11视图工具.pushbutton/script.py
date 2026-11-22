# -*- coding: utf-8 -*-
"""视图工具：类型可见性 / 过滤器可见性 / 当前层三维 / 快速剖面 / 平行剖面"""
import os
import traceback

import clr
clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')

from System.Collections.Generic import List
from Autodesk.Revit.DB import (
    BoundingBoxXYZ, BuiltInCategory, CurveElement, ElementId, FilteredElementCollector,
    LocationCurve, ParameterFilterElement, Transform, View, View3D, ViewDuplicateOption,
    ViewFamily, ViewFamilyType, ViewSection, Wall, XYZ,
)
from pyrevit import forms, revit

LOG_PATH = os.path.join(
    os.path.expanduser('~'), 'Documents', 'deepseek-harness', 'default-workspace',
    u'_视图工具日志.txt')

FT_TO_MM = 304.8


def log_line(text):
    try:
        f = open(LOG_PATH, 'ab')
        try:
            f.write((text + u'\n').encode('utf-8'))
        finally:
            f.close()
    except Exception:
        pass


def active_doc():
    try:
        return revit.doc
    except Exception:
        return revit.uidoc.Document


def uidoc_ref():
    return revit.uidoc


def selected_elements(uidoc):
    return [uidoc.Document.GetElement(i) for i in uidoc.Selection.GetElementIds()]


def instances_of_type(doc, type_id):
    return [e for e in FilteredElementCollector(doc).WhereElementIsNotElementType().ToElements()
            if e.GetTypeId() is not None and e.GetTypeId().IntegerValue == type_id.IntegerValue]


def is_hidden(view, element_id):
    try:
        return view.IsHidden(element_id)
    except Exception:
        return False


# ---------------- 类型可见性 ----------------

def toggle_type_visibility(uidoc, hide_mode):
    """hide_mode: 'hide' / 'show' / 'auto'"""
    doc = uidoc.Document
    view = doc.ActiveView
    elements = [e for e in selected_elements(uidoc) if e is not None]
    if not elements:
        forms.alert(u'请先选中构件。')
        return
    type_ids = []
    for element in elements:
        tid = element.GetTypeId()
        if tid is None or tid.IntegerValue < 0:
            continue
        if tid.IntegerValue not in [t.IntegerValue for t in type_ids]:
            type_ids.append(tid)
    if not type_ids:
        forms.alert(u'选中的构件没有可用的类型。')
        return

    targets = []
    for tid in type_ids:
        targets.extend(instances_of_type(doc, tid))
    visible = [t.Id for t in targets if not is_hidden(view, t.Id)]
    hidden = [t.Id for t in targets if is_hidden(view, t.Id)]
    if hide_mode == 'auto':
        hide_mode = 'hide' if visible else 'show'

    tx = Transaction(doc, u'类型可见性')
    tx.Start()
    try:
        if hide_mode == 'hide':
            if visible:
                view.HideElements(List[ElementId](visible))
            count = len(visible)
        else:
            if hidden:
                view.UnhideElements(List[ElementId](hidden))
            count = len(hidden)
        tx.Commit()
    except Exception:
        tx.RollBack()
        log_line(u'[type] ' + traceback.format_exc())
        forms.alert(u'类型可见性失败：\n' + traceback.format_exc().splitlines()[-1])
        return
    log_line(u'[type] %s %d 个实例，类型 %d 个' % (hide_mode, count, len(type_ids)))
    forms.alert(u'已%s %d 个实例（%d 个类型）。' % (
        u'隐藏' if hide_mode == 'hide' else u'显示', count, len(type_ids)))


# ---------------- 过滤器可见性 ----------------

def all_filters(doc):
    return [f for f in FilteredElementCollector(doc).OfClass(ParameterFilterElement).ToElements()]


def filter_state(doc, view, filter_element):
    ids = List[ElementId]()
    ids.Add(filter_element.Id)
    try:
        return view.GetFilterOverrides(filter_element.Id) is not None
    except Exception:
        return False


def toggle_filters(uidoc, filter_ids, enable):
    doc = uidoc.Document
    view = doc.ActiveView
    ok = 0
    failures = []
    tx = Transaction(doc, u'过滤器可见性')
    tx.Start()
    try:
        for fid in filter_ids:
            try:
                view.SetFilterVisibility(ElementId(fid), enable)
                ok += 1
            except Exception as ex:
                failures.append(u'%s：%s' % (fid, ex))
        tx.Commit()
    except Exception:
        tx.RollBack()
        failures.append(traceback.format_exc())
    log_line(u'[filter] %s %d 个，失败 %d' % (enable, ok, len(failures)))
    forms.alert(u'已%s %d 个过滤器%s' % (
        u'启用' if enable else u'停用', ok,
        (u'，失败 %d 个' % len(failures)) if failures else u''))


# ---------------- 当前层三维 ----------------

def level_elevation(level):
    try:
        return level.Elevation
    except Exception:
        return 0.0


def current_level_3d(uidoc, margin_mm):
    doc = uidoc.Document
    view = doc.ActiveView
    level = None
    try:
        level = view.GenLevel
    except Exception:
        level = None
    if level is None:
        forms.alert(u'当前视图不是楼层平面，无法判断所在标高。')
        return

    # 找相邻更高标高，用来算层高
    levels = sorted([l for l in FilteredElementCollector(doc).OfClass(__import__('Autodesk.Revit.DB', fromlist=['Level']).Level).ToElements()],
                    key=lambda l: l.Elevation)
    base_z = level.Elevation
    next_z = None
    for l in levels:
        if l.Elevation > base_z + 1e-6:
            next_z = l.Elevation
            break
    top_z = next_z if next_z is not None else base_z + 3000.0 / FT_TO_MM

    base3d = None
    for v in FilteredElementCollector(doc).OfClass(View3D).ToElements():
        if v.IsTemplate:
            continue
        base3d = v
        break
    if base3d is None:
        forms.alert(u'文档里没有可用的三维视图。')
        return

    margin = margin_mm / FT_TO_MM
    tx = Transaction(doc, u'当前层三维')
    tx.Start()
    try:
        new_view = base3d.Duplicate(ViewDuplicateOption.Duplicate)
        view3d = doc.GetElement(new_view)
        view3d.Name = u'三维 - %s' % level.Name
        box = BoundingBoxXYZ()
        width = 30000.0 / FT_TO_MM
        box.Min = XYZ(-width / 2.0, -width / 2.0, base_z - margin)
        box.Max = XYZ(width / 2.0, width / 2.0, top_z + margin)
        view3d.SetSectionBox(box)
        tx.Commit()
    except Exception:
        tx.RollBack()
        log_line(u'[3d] ' + traceback.format_exc())
        forms.alert(u'当前层三维失败：\n' + traceback.format_exc().splitlines()[-1])
        return
    log_line(u'[3d] %s %.1f~%.1f mm' % (level.Name, base_z * FT_TO_MM, top_z * FT_TO_MM))
    forms.alert(u'已生成「三维 - %s」，范围 %.0f ~ %.0f mm。' % (
        level.Name, base_z * FT_TO_MM, top_z * FT_TO_MM))


# ---------------- 快速剖面 / 平行剖面 ----------------

def section_type_id(doc, family):
    for t in FilteredElementCollector(doc).OfClass(ViewFamilyType).ToElements():
        if t.ViewFamily == family:
            return t.Id
    return None


def section_box_for_curve(curve, height_ft, depth_ft, base_z):
    p0 = curve.GetEndPoint(0)
    p1 = curve.GetEndPoint(1)
    direction = (p1 - p0).Normalize()
    normal = XYZ(-direction.Y, direction.X, 0.0)
    if normal.GetLength() < 1e-9:
        normal = XYZ(1, 0, 0)
    normal = normal.Normalize()
    mid = (p0 + p1) * 0.5
    length = p0.DistanceTo(p1)
    origin = XYZ(mid.X, mid.Y, base_z) - normal * depth_ft

    transform = Transform.Identity
    transform.Origin = origin
    transform.BasisX = direction
    transform.BasisY = XYZ(0, 0, 1)
    transform.BasisZ = normal

    box = BoundingBoxXYZ()
    box.Transform = transform
    box.Min = XYZ(-length / 2.0, -1.0, 0.0)
    box.Max = XYZ(length / 2.0, height_ft, depth_ft * 2.0)
    return box


def make_sections(uidoc, height_mm, depth_mm, parallel):
    doc = uidoc.Document
    view = doc.ActiveView
    base_z = 0.0
    try:
        if view.GenLevel is not None:
            base_z = view.GenLevel.Elevation
    except Exception:
        base_z = 0.0

    curves = []
    for element in selected_elements(uidoc):
        if isinstance(element, Wall):
            loc = element.Location
            if isinstance(loc, LocationCurve):
                curves.append(loc.Curve)
        elif isinstance(element, CurveElement):
            curves.append(element.GeometryCurve)
    if not curves:
        forms.alert(u'请先选中墙或模型线（剖面方向由它们决定）。')
        return
    if not parallel:
        curves = curves[:1]

    vt_id = section_type_id(doc, ViewFamily.Section)
    if vt_id is None:
        forms.alert(u'文档里没有剖面视图类型。')
        return

    height_ft = height_mm / FT_TO_MM
    depth_ft = depth_mm / FT_TO_MM
    made = 0
    failures = []
    tx = Transaction(doc, u'快速剖面')
    tx.Start()
    try:
        for curve in curves:
            try:
                box = section_box_for_curve(curve, height_ft, depth_ft, base_z)
                section = ViewSection.CreateSection(doc, vt_id, box)
                if section is not None:
                    made += 1
            except Exception as ex:
                failures.append(str(ex))
        tx.Commit()
    except Exception:
        tx.RollBack()
        failures.append(traceback.format_exc())
    log_line(u'[section] 生成 %d 个，失败 %d' % (made, len(failures)))
    forms.alert(u'已生成 %d 个剖面视图%s' % (
        made, (u'\n失败 %d 个：%s' % (len(failures), failures[0])) if failures else u''))


# ---------------- 过滤器页 ----------------

class FilterWindow(forms.WPFWindow):
    def __init__(self, filters, view, template=None):
        forms.WPFWindow.__init__(self, XAML_FILTER, literal_string=True)
        self.filters = filters
        self.view = view
        self.listBox.Items.Clear()
        for f in filters:
            self.listBox.Items.Add(u'%s' % f.Name)
        self.titleText.Text = u'当前视图：%s' % view.Name

    def _picked_ids(self):
        ids = []
        for item in self.listBox.SelectedItems:
            index = self.listBox.Items.IndexOf(item)
            ids.append(self.filters[index].Id.IntegerValue)
        return ids

    def on_enable(self, sender, args):
        ids = self._picked_ids()
        if not ids:
            forms.alert(u'请先在列表里选中过滤器。')
            return
        self.Close()
        self.pending = (ids, True)

    def on_disable(self, sender, args):
        ids = self._picked_ids()
        if not ids:
            forms.alert(u'请先在列表里选中过滤器。')
            return
        self.Close()
        self.pending = (ids, False)

    def on_close(self, sender, args):
        self.pending = None
        self.Close()


XAML_FILTER = u"""
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="过滤器可见性" Height="480" Width="520"
        WindowStartupLocation="CenterScreen" Background="#FFF7FBF9">
  <DockPanel Margin="16">
    <TextBlock x:Name="titleText" DockPanel.Dock="Top" Foreground="#FF263043" Margin="0,0,0,10" TextWrapping="Wrap"/>
    <StackPanel DockPanel.Dock="Bottom" Orientation="Horizontal" HorizontalAlignment="Right" Margin="0,12,0,0">
      <Button x:Name="btnEnable" Content="启用到当前视图" Height="32" Width="140" Margin="0,0,8,0" FontWeight="Bold"/>
      <Button x:Name="btnDisable" Content="从当前视图停用" Height="32" Width="140" Margin="0,0,8,0"/>
      <Button x:Name="btnClose" Content="关闭" Height="32" Width="90"/>
    </StackPanel>
    <ListBox x:Name="listBox" SelectionMode="Extended"/>
  </DockPanel>
</Window>
"""


XAML_MAIN = u"""
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="视图工具" Height="620" Width="640"
        WindowStartupLocation="CenterScreen" ResizeMode="CanResize"
        Background="#FFF7FBF9">
  <ScrollViewer VerticalScrollBarVisibility="Auto" HorizontalScrollBarVisibility="Disabled">
    <StackPanel Margin="18">
      <TextBlock Text="① 类型可见性" FontSize="15" FontWeight="Bold" Foreground="#FF263043" Margin="0,0,0,6"/>
      <TextBlock Text="选中任意一个构件 → 按「类型」批量隐藏/显示它的同类实例（当前视图）。"
                 TextWrapping="Wrap" Foreground="#FF5A6B7B" Margin="0,0,0,8"/>
      <StackPanel Orientation="Horizontal" Margin="0,0,0,16">
        <Button x:Name="btnHideType" Content="隐藏同类" Height="32" Width="120" Margin="0,0,8,0" FontWeight="Bold"/>
        <Button x:Name="btnShowType" Content="显示同类" Height="32" Width="120" Margin="0,0,8,0"/>
        <Button x:Name="btnAutoType" Content="一键切换" Height="32" Width="120"/>
      </StackPanel>

      <TextBlock Text="② 过滤器可见性" FontSize="15" FontWeight="Bold" Foreground="#FF263043" Margin="0,0,0,6"/>
      <TextBlock Text="列出文档里的参数过滤器，批量启用到当前视图或停用。"
                 TextWrapping="Wrap" Foreground="#FF5A6B7B" Margin="0,0,0,8"/>
      <Button x:Name="btnFilters" Content="打开过滤器列表…" Height="34" Margin="0,0,0,16" FontWeight="Bold"/>

      <TextBlock Text="③ 当前层三维" FontSize="15" FontWeight="Bold" Foreground="#FF263043" Margin="0,0,0,6"/>
      <Grid Margin="0,0,0,8">
        <Grid.ColumnDefinitions><ColumnDefinition Width="96"/><ColumnDefinition Width="*"/></Grid.ColumnDefinitions>
        <TextBlock Text="上下余量(mm)" VerticalAlignment="Center" Foreground="#FF263043"/>
        <TextBox x:Name="marginBox" Grid.Column="1" Height="28" Text="300" VerticalContentAlignment="Center"/>
      </Grid>
      <Button x:Name="btn3d" Content="按当前标高生成三维视图" Height="34" Margin="0,0,0,16" FontWeight="Bold"/>

      <TextBlock Text="④ 快速剖面 / 平行剖面" FontSize="15" FontWeight="Bold" Foreground="#FF263043" Margin="0,0,0,6"/>
      <Grid Margin="0,0,0,8">
        <Grid.ColumnDefinitions><ColumnDefinition Width="96"/><ColumnDefinition Width="*"/><ColumnDefinition Width="72"/><ColumnDefinition Width="*"/></Grid.ColumnDefinitions>
        <TextBlock Text="剖切高度(mm)" VerticalAlignment="Center" Foreground="#FF263043"/>
        <TextBox x:Name="heightBox" Grid.Column="1" Height="28" Text="3000" VerticalContentAlignment="Center"/>
        <TextBlock Text="进深(mm)" Grid.Column="2" VerticalAlignment="Center" HorizontalAlignment="Right" Foreground="#FF263043" Margin="0,0,6,0"/>
        <TextBox x:Name="depthBox" Grid.Column="3" Height="28" Text="3000" VerticalContentAlignment="Center"/>
      </Grid>
      <StackPanel Orientation="Horizontal" Margin="0,0,0,16">
        <Button x:Name="btnSection" Content="单个剖面（第一段）" Height="34" Width="180" Margin="0,0,8,0" FontWeight="Bold"/>
        <Button x:Name="btnParallel" Content="平行剖面（全部选中）" Height="34" Width="200" FontWeight="Bold"/>
      </StackPanel>

      <TextBlock x:Name="statusBox" TextWrapping="Wrap" Foreground="#FF5A6B7B" Margin="0,4,0,10"/>
      <Button x:Name="btnClose" Content="关闭" Height="32" Width="110" HorizontalAlignment="Right"/>
    </StackPanel>
  </ScrollViewer>
</Window>
"""


def _float(text, default):
    try:
        return float(text)
    except Exception:
        return default


def main():
    doc = active_doc()
    if doc is None:
        forms.alert(u'请先打开一个项目。')
        return
    uidoc = uidoc_ref()
    view = doc.ActiveView

    window = forms.WPFWindow(XAML_MAIN, literal_string=True)
    window.Title = u'视图工具'

    def guard(name, func):
        def handler(sender, args):
            try:
                func()
            except Exception:
                log_line(u'[%s] %s' % (name, traceback.format_exc()))
                forms.alert(u'%s 出错：\n%s' % (name, traceback.format_exc().splitlines()[-1]))
        return handler

    def hide_type():
        toggle_type_visibility(uidoc, 'hide')
        window.statusBox.Text = u'类型可见性：已隐藏同类。'

    def show_type():
        toggle_type_visibility(uidoc, 'show')
        window.statusBox.Text = u'类型可见性：已显示同类。'

    def auto_type():
        toggle_type_visibility(uidoc, 'auto')
        window.statusBox.Text = u'类型可见性：已切换。'

    def open_filters():
        filters = all_filters(doc)
        if not filters:
            forms.alert(u'文档里还没有参数过滤器。可以先用「视图 → 过滤器」建一个。')
            return
        sub = FilterWindow(filters, view)
        sub.ShowDialog()
        pending = getattr(sub, 'pending', None)
        if pending:
            toggle_filters(uidoc, pending[0], pending[1])
            window.statusBox.Text = u'过滤器可见性：已应用。'

    def make_3d():
        current_level_3d(uidoc, _float(window.marginBox.Text, 300.0))
        window.statusBox.Text = u'当前层三维：已生成。'

    def section():
        make_sections(uidoc, _float(window.heightBox.Text, 3000.0),
                      _float(window.depthBox.Text, 3000.0), False)
        window.statusBox.Text = u'快速剖面：已生成。'

    def parallel():
        make_sections(uidoc, _float(window.heightBox.Text, 3000.0),
                      _float(window.depthBox.Text, 3000.0), True)
        window.statusBox.Text = u'平行剖面：已生成。'

    window.btnHideType.Click += guard('hideType', hide_type)
    window.btnShowType.Click += guard('showType', show_type)
    window.btnAutoType.Click += guard('autoType', auto_type)
    window.btnFilters.Click += guard('filters', open_filters)
    window.btn3d.Click += guard('3d', make_3d)
    window.btnSection.Click += guard('section', section)
    window.btnParallel.Click += guard('parallel', parallel)
    window.btnClose.Click += lambda s, a: window.Close()

    window.statusBox.Text = u'提示：所有改动都在事务里，可 Ctrl+Z 撤销。'
    window.ShowDialog()


try:
    main()
except Exception:
    log_line(traceback.format_exc())
    forms.alert(u'视图工具启动失败：\n' + traceback.format_exc().splitlines()[-1])
