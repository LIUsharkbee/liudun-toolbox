# -*- coding: utf-8 -*-
"""标注工具：批量换标注样式 / 文字避让清零 / 文字归位。"""
import traceback

import clr  # noqa
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")
clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")

from Autodesk.Revit.DB import (  # noqa: E402
    Dimension,
    DimensionType,
    ElementId,
    FilteredElementCollector,
    Transaction,
    ViewPlan,
    XYZ,
)
from Autodesk.Revit.UI import IExternalCommand  # noqa: E402

from pyrevit import forms, revit, script  # noqa: E402

doc = revit.doc
uidoc = revit.uidoc
output = script.get_output()

MM = 1.0 / 304.8
LOG_PATH = r"C:\Users\cyl\Documents\deepseek-harness\default-workspace\_标注工具日志.txt"


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


def dimension_types():
    rows = []
    for item in FilteredElementCollector(doc).OfClass(DimensionType):
        try:
            rows.append(item)
        except Exception:
            continue
    rows.sort(key=lambda t: safe_name(t).lower())
    return rows


def dimensions_in_view(view):
    rows = []
    for element in FilteredElementCollector(doc, view.Id).WhereElementIsNotElementType():
        if isinstance(element, Dimension):
            rows.append(element)
    return rows


def selected_dimensions():
    rows = []
    try:
        ids = list(uidoc.Selection.GetElementIds())
    except Exception:
        return rows
    for element_id in ids:
        element = doc.GetElement(element_id)
        if isinstance(element, Dimension):
            rows.append(element)
    return rows


def restyle(dimensions, type_id):
    changed = 0
    failures = []
    transaction = Transaction(doc, u"批量换标注样式")
    transaction.Start()
    try:
        for dimension in dimensions:
            try:
                dimension.ChangeTypeId(type_id)
                changed += 1
            except Exception:
                failures.append(u"%s: %s" % (dimension.Id, traceback.format_exc().splitlines()[-1]))
        transaction.Commit()
    except Exception:
        transaction.RollBack()
        failures.append(traceback.format_exc().splitlines()[-1])
    log_line(u"[restyle] 改 %d 条，失败 %d" % (changed, len(failures)))
    return changed, failures


def spread_text(dimensions, step_mm):
    moved = 0
    skipped = 0
    failures = []
    step = step_mm * MM
    transaction = Transaction(doc, u"标注文字避让")
    transaction.Start()
    try:
        buckets = {}
        for dimension in dimensions:
            try:
                point = dimension.TextPosition
            except Exception:
                skipped += 1
                continue
            if point is None:
                skipped += 1
                continue
            key = (round(point.X / step) if step else 0, round(point.Z / step) if step else 0)
            index = buckets.get(key, 0)
            buckets[key] = index + 1
            if index == 0:
                continue
            try:
                dimension.TextPosition = XYZ(point.X, point.Y + step * index, point.Z)
                moved += 1
            except Exception:
                skipped += 1
        transaction.Commit()
    except Exception:
        transaction.RollBack()
        failures.append(traceback.format_exc().splitlines()[-1])
    log_line(u"[spread] 移动 %d 条，跳过 %d" % (moved, skipped))
    return moved, skipped, failures


def reset_text(dimensions):
    done = 0
    failures = []
    transaction = Transaction(doc, u"标注文字归位")
    transaction.Start()
    try:
        for dimension in dimensions:
            try:
                dimension.ResetTextPosition()
                done += 1
            except Exception:
                failures.append(u"%s: %s" % (dimension.Id, traceback.format_exc().splitlines()[-1]))
        transaction.Commit()
    except Exception:
        transaction.RollBack()
        failures.append(traceback.format_exc().splitlines()[-1])
    log_line(u"[reset] 归位 %d 条，失败 %d" % (done, len(failures)))
    return done, failures


XAML = u"""
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="标注工具" Height="420" Width="520" MinWidth="480" MinHeight="380"
        WindowStartupLocation="CenterScreen" ResizeMode="CanResize">
  <Grid Margin="16">
    <Grid.RowDefinitions>
      <RowDefinition Height="Auto"/>
      <RowDefinition Height="Auto"/>
      <RowDefinition Height="Auto"/>
      <RowDefinition Height="*"/>
      <RowDefinition Height="Auto"/>
    </Grid.RowDefinitions>

    <StackPanel Grid.Row="0" Orientation="Horizontal" Margin="0,0,0,10">
      <TextBlock Text="标注样式" VerticalAlignment="Center" Width="70"/>
      <ComboBox x:Name="typeBox" Width="260" Height="28" VerticalContentAlignment="Center"/>
    </StackPanel>

    <StackPanel Grid.Row="1" Orientation="Horizontal" Margin="0,0,0,10">
      <TextBlock Text="避让间距(mm)" VerticalAlignment="Center" Width="100"/>
      <TextBox x:Name="stepBox" Text="220" Width="80" Height="28"/>
      <TextBlock Text="范围" VerticalAlignment="Center" Margin="20,0,8,0"/>
      <ComboBox x:Name="scopeBox" Width="120" Height="28" VerticalContentAlignment="Center"/>
    </StackPanel>

    <WrapPanel Grid.Row="2" Margin="0,0,0,10">
      <Button x:Name="btnRestyle" Content="换样式" Width="110" Height="32" Margin="0,0,8,8"/>
      <Button x:Name="btnSpread" Content="文字避让" Width="110" Height="32" Margin="0,0,8,8"/>
      <Button x:Name="btnReset" Content="文字归位" Width="110" Height="32" Margin="0,0,8,8"/>
      <Button x:Name="btnCount" Content="统计" Width="80" Height="32" Margin="0,0,8,8"/>
    </WrapPanel>

    <TextBox Grid.Row="3" x:Name="report" IsReadOnly="True" AcceptsReturn="True"
             VerticalScrollBarVisibility="Auto" TextWrapping="Wrap"/>

    <StackPanel Grid.Row="4" Orientation="Horizontal" HorizontalAlignment="Right" Margin="0,10,0,0">
      <Button x:Name="btnClose" Content="关闭" Width="88" Height="32"/>
    </StackPanel>
  </Grid>
</Window>
"""


class DimWindow(forms.WPFWindow):
    def __init__(self, types):
        forms.WPFWindow.__init__(self, XAML, literal_string=True)
        self.types = types
        self.type_box = self.FindName("typeBox")
        for item in types:
            self.type_box.Items.Add(safe_name(item))
        if types:
            self.type_box.SelectedIndex = 0
        self.scope = self.FindName("scopeBox")
        self.scope.Items.Add(u"当前视图")
        self.scope.Items.Add(u"选中的标注")
        self.scope.SelectedIndex = 0
        self.report = self.FindName("report")
        self.FindName("btnRestyle").Click += self._guard(self._on_restyle)
        self.FindName("btnSpread").Click += self._guard(self._on_spread)
        self.FindName("btnReset").Click += self._guard(self._on_reset)
        self.FindName("btnCount").Click += self._guard(self._on_count)
        self.FindName("btnClose").Click += self._on_close

    def _guard(self, func):
        def wrapped(sender, args):
            try:
                func(sender, args)
            except Exception:
                text = traceback.format_exc()[-800:]
                self.report.Text = text
                log_line(text)
        return wrapped

    def _targets(self):
        if self.scope.SelectedIndex == 1:
            return selected_dimensions()
        try:
            return dimensions_in_view(uidoc.ActiveView)
        except Exception:
            return []

    def _type_index(self):
        return max(0, self.type_box.SelectedIndex)

    def _on_count(self, sender, args):
        rows = self._targets()
        self.report.Text = u"当前范围标注数量：%d" % len(rows)

    def _on_restyle(self, sender, args):
        if not self.types:
            self.report.Text = u"项目里没有标注样式。"
            return
        rows = self._targets()
        if not rows:
            self.report.Text = u"没有可处理的标注。"
            return
        changed, failures = restyle(rows, self.types[self._type_index()].Id)
        text = u"换样式：%d 条" % changed
        if failures:
            text += u"\n失败 %d 条：%s" % (len(failures), failures[0])
        self.report.Text = text

    def _on_spread(self, sender, args):
        rows = self._targets()
        if not rows:
            self.report.Text = u"没有可处理的标注。"
            return
        try:
            step = float(self.FindName("stepBox").Text)
        except Exception:
            step = 220.0
        moved, skipped, failures = spread_text(rows, step)
        text = u"文字避让：移动 %d 条，跳过 %d 条" % (moved, skipped)
        if failures:
            text += u"\n" + failures[0]
        self.report.Text = text

    def _on_reset(self, sender, args):
        rows = self._targets()
        if not rows:
            self.report.Text = u"没有可处理的标注。"
            return
        done, failures = reset_text(rows)
        text = u"文字归位：%d 条" % done
        if failures:
            text += u"\n失败 %d 条：%s" % (len(failures), failures[0])
        self.report.Text = text

    def _on_close(self, sender, args):
        self.Close()


def main():
    if doc is None:
        forms.alert(u"请先打开一个项目。", exitscript=True)
        return
    window = DimWindow(dimension_types())
    window.ShowDialog()


try:
    main()
except Exception:
    output.print_md(u"脚本出错：\n```\n{0}\n```".format(traceback.format_exc()))
    forms.alert(u"脚本出错：\n%s" % traceback.format_exc()[-600:], title=u"标注工具")
