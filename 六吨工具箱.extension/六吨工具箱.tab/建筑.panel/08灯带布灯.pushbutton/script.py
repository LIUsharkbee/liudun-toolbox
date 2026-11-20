# -*- coding: utf-8 -*-
"""灯带 / 布灯：沿选中线（模型线、详图线、墙定位线）按间距布置灯具。"""
import traceback

import clr  # noqa
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")
clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")

from Autodesk.Revit.DB import (  # noqa: E402
    BuiltInCategory,
    BuiltInParameter,
    CurveElement,
    ElementId,
    FamilySymbol,
    FilteredElementCollector,
    Level,
    Line,
    LocationCurve,
    Transaction,
    Wall,
    XYZ,
)
from Autodesk.Revit.DB.Structure import StructuralType  # noqa: E402
from Autodesk.Revit.UI import IExternalCommand  # noqa: E402

from pyrevit import forms, revit, script  # noqa: E402

doc = revit.doc
uidoc = revit.uidoc
output = script.get_output()

MM = 1.0 / 304.8
FT_TO_MM = 304.8
LOG_PATH = r"C:\Users\cyl\Documents\deepseek-harness\default-workspace\_灯带日志.txt"


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


def fixture_symbols():
    rows = []
    for symbol in FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_LightingFixtures) \
            .WhereElementIsElementType():
        if isinstance(symbol, FamilySymbol):
            rows.append(symbol)
    rows.sort(key=lambda s: u"%s/%s" % (safe_name(s.Family) if hasattr(s, "Family") else u"",
                                        safe_name(s).lower()))
    return rows


def all_levels():
    rows = []
    for level in FilteredElementCollector(doc).OfClass(Level):
        try:
            rows.append((level, level.Elevation * FT_TO_MM))
        except Exception:
            continue
    rows.sort(key=lambda item: item[1])
    return rows


def picked_curves():
    """选中里的曲线：模型线/详图线取几何线，墙取定位线。"""
    rows = []
    failures = []
    try:
        ids = list(uidoc.Selection.GetElementIds())
    except Exception:
        return rows, failures
    for element_id in ids:
        element = doc.GetElement(element_id)
        if isinstance(element, CurveElement):
            try:
                rows.append(element.GeometryCurve)
            except Exception:
                failures.append(u"%s 取线失败" % element_id)
            continue
        if isinstance(element, Wall):
            try:
                location = element.Location
                if isinstance(location, LocationCurve):
                    rows.append(location.Curve)
            except Exception:
                failures.append(u"%s 取墙线失败" % element_id)
    return rows, failures


def points_on_curve(curve, spacing_ft, include_ends=True):
    points = []
    try:
        length = curve.Length
    except Exception:
        return points
    if length <= 0:
        return points
    count = int(length / spacing_ft)
    if count < 1:
        count = 1
    step = length / float(count + 1) if not include_ends else None
    if include_ends:
        points.append(curve.Evaluate(0.0, True))
        current = spacing_ft
        while current < length - spacing_ft * 0.5:
            points.append(curve.Evaluate(current / length, True))
            current += spacing_ft
        points.append(curve.Evaluate(1.0, True))
    else:
        current = step
        for _index in range(count):
            points.append(curve.Evaluate(current / length, True))
            current += step
    return points


def place_fixtures(curves, symbol, level, spacing_mm, offset_mm, include_ends):
    placed = 0
    failures = []
    spacing_ft = spacing_mm * MM
    offset_ft = offset_mm * MM
    if spacing_ft <= 0:
        return 0, [u"间距必须大于 0"]
    transaction = Transaction(doc, u"灯带布灯")
    transaction.Start()
    try:
        try:
            if not symbol.IsActive:
                symbol.Activate()
        except Exception:
            pass
        for curve in curves:
            points = points_on_curve(curve, spacing_ft, include_ends)
            for point in points:
                location = XYZ(point.X, point.Y, point.Z + offset_ft)
                try:
                    doc.Create.NewFamilyInstance(location, symbol, level, StructuralType.NonStructural)
                    placed += 1
                except Exception:
                    failures.append(u"%s: %s" % (location, traceback.format_exc().splitlines()[-1]))
        transaction.Commit()
    except Exception:
        transaction.RollBack()
        failures.append(traceback.format_exc().splitlines()[-1])
    log_line(u"[place] 布置 %d 个，失败 %d" % (placed, len(failures)))
    return placed, failures


XAML = u"""
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="灯带 / 布灯" Height="460" Width="540" MinWidth="500" MinHeight="420"
        WindowStartupLocation="CenterScreen" ResizeMode="CanResize">
  <Grid Margin="16">
    <Grid.RowDefinitions>
      <RowDefinition Height="Auto"/>
      <RowDefinition Height="Auto"/>
      <RowDefinition Height="Auto"/>
      <RowDefinition Height="Auto"/>
      <RowDefinition Height="*"/>
      <RowDefinition Height="Auto"/>
    </Grid.RowDefinitions>

    <StackPanel Grid.Row="0" Orientation="Horizontal" Margin="0,0,0,10">
      <TextBlock Text="灯具类型" VerticalAlignment="Center" Width="70"/>
      <ComboBox x:Name="symbolBox" Width="330" Height="28" VerticalContentAlignment="Center"/>
    </StackPanel>

    <StackPanel Grid.Row="1" Orientation="Horizontal" Margin="0,0,0,10">
      <TextBlock Text="标高" VerticalAlignment="Center" Width="70"/>
      <ComboBox x:Name="levelBox" Width="200" Height="28" VerticalContentAlignment="Center"/>
    </StackPanel>

    <StackPanel Grid.Row="2" Orientation="Horizontal" Margin="0,0,0,10">
      <TextBlock Text="间距(mm)" VerticalAlignment="Center" Width="70"/>
      <TextBox x:Name="spacingBox" Text="600" Width="80" Height="28"/>
      <TextBlock Text="抬高(mm)" VerticalAlignment="Center" Margin="16,0,8,0"/>
      <TextBox x:Name="offsetBox" Text="0" Width="70" Height="28"/>
      <CheckBox x:Name="endsBox" Content="两端也放" IsChecked="True"
                VerticalAlignment="Center" Margin="16,0,0,0"/>
    </StackPanel>

    <TextBlock Grid.Row="3" Text="先选中模型线 / 详图线 / 墙（可多选），再点「沿线布灯」。"
               TextWrapping="Wrap" Foreground="#FF6B7683" Margin="0,0,0,10"/>

    <TextBox Grid.Row="4" x:Name="report" IsReadOnly="True" AcceptsReturn="True"
             VerticalScrollBarVisibility="Auto" TextWrapping="Wrap"/>

    <StackPanel Grid.Row="5" Orientation="Horizontal" HorizontalAlignment="Right" Margin="0,10,0,0">
      <Button x:Name="btnPlace" Content="沿线布灯" Width="120" Height="34" FontWeight="Bold" Margin="0,0,8,0"/>
      <Button x:Name="btnClose" Content="关闭" Width="88" Height="34"/>
    </StackPanel>
  </Grid>
</Window>
"""


class LightWindow(forms.WPFWindow):
    def __init__(self, symbols, levels):
        forms.WPFWindow.__init__(self, XAML, literal_string=True)
        self.symbols = symbols
        self.levels = levels
        self.symbol_box = self.FindName("symbolBox")
        for symbol in symbols:
            try:
                family = safe_name(symbol.Family)
            except Exception:
                family = u""
            self.symbol_box.Items.Add(u"<%s> %s" % (family, safe_name(symbol)))
        if symbols:
            self.symbol_box.SelectedIndex = 0
        self.level_box = self.FindName("levelBox")
        for index, (level, elevation) in enumerate(levels):
            self.level_box.Items.Add(u"%s (%+.3f m)" % (safe_name(level), elevation / 1000.0))
            try:
                if level.Id == uidoc.ActiveView.GenLevel.Id:
                    self.level_box.SelectedIndex = index
            except Exception:
                pass
        if self.level_box.SelectedIndex < 0:
            self.level_box.SelectedIndex = 0
        self.report = self.FindName("report")
        self.FindName("btnPlace").Click += self._guard(self._on_place)
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

    def _on_place(self, sender, args):
        if not self.symbols:
            self.report.Text = u"项目里没有照明设备类型（载入一个灯具族再试）。"
            return
        if not self.levels:
            self.report.Text = u"项目里没有标高。"
            return
        curves, failures = picked_curves()
        if not curves:
            self.report.Text = u"没选中线。请先选中模型线 / 详图线 / 墙。"
            return
        try:
            spacing = float(self.FindName("spacingBox").Text)
        except Exception:
            spacing = 600.0
        try:
            offset = float(self.FindName("offsetBox").Text)
        except Exception:
            offset = 0.0
        include_ends = bool(self.FindName("endsBox").IsChecked)
        symbol = self.symbols[max(0, self.symbol_box.SelectedIndex)]
        level = self.levels[max(0, self.level_box.SelectedIndex)][0]
        placed, more = place_fixtures(curves, symbol, level, spacing, offset, include_ends)
        text = u"布置 %d 个灯具（曲线 %d 条）" % (placed, len(curves))
        problems = failures + more
        if problems:
            text += u"\n失败 %d 条：%s" % (len(problems), problems[0])
        self.report.Text = text

    def _on_close(self, sender, args):
        self.Close()


def main():
    if doc is None:
        forms.alert(u"请先打开一个项目。", exitscript=True)
        return
    window = LightWindow(fixture_symbols(), all_levels())
    window.ShowDialog()


try:
    main()
except Exception:
    output.print_md(u"脚本出错：\n```\n{0}\n```".format(traceback.format_exc()))
    forms.alert(u"脚本出错：\n%s" % traceback.format_exc()[-600:], title=u"灯带 / 布灯")
