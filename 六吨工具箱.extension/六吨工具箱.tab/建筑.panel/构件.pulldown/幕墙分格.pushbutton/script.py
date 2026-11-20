# -*- coding: utf-8 -*-
"""幕墙分格 —— 六吨工具箱

借鉴小鲶鱼 CurtainGridCore 的做法：不重做几何，只对「幕墙类墙」补充 U/V 分格线。

两种用法：
  1) 选一条模型线/详图线/墙的定位线（或选一段墙），按给定宽度/高度新建幕墙并自动分格；
  2) 只选已经存在的幕墙，按给定间距补齐分格线。

刻意避开的坑（以前踩过）：
  * 不在窗口内拾取（非模态窗口里 PickPoint 拿不到上下文）——只读选择集；
  * 模块级 doc 用 _active_doc() 现取，避免 doc is None；
  * 日志写文件，失败不静默；
  * 所有修改集中在单个事务，可 Ctrl+Z。
"""

import os
import sys
import traceback

import clr

clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

from Autodesk.Revit.DB import (  # noqa: E402
    BuiltInCategory,
    BuiltInParameter,
    CurveElement,
    CurtainGridLine,
    Document,
    ElementId,
    FilteredElementCollector,
    IFailuresPreprocessor,
    FailureProcessingResult,
    FailureSeverity,
    Level,
    Line,
    Transaction,
    Wall,
    WallKind,
    WallType,
    XYZ,
)
from Autodesk.Revit.UI import IExternalCommand  # noqa: E402

from pyrevit import forms, revit, script  # noqa: E402

MM_TO_FT = 1.0 / 304.8
FT_TO_MM = 304.8

LOG_PATH = r"C:\Users\cyl\Documents\deepseek-harness\default-workspace\_幕墙日志.txt"


def log_line(text):
    try:
        with open(LOG_PATH, "ab") as fh:
            fh.write((u"%s\n" % text).encode("utf-8"))
    except Exception:
        pass


def _active_doc():
    try:
        doc = revit.doc
    except Exception:
        doc = None
    if doc is None:
        from Autodesk.Revit.UI import UIDocument  # noqa
        try:
            doc = revit.uidoc.Document
        except Exception:
            doc = None
    return doc


class QuietPreprocessor(IFailuresPreprocessor):
    def PreprocessFailures(self, accessor):
        try:
            for msg in accessor.GetFailureMessages():
                if msg.GetSeverity() == FailureSeverity.Warning:
                    accessor.DeleteWarning(msg)
        except Exception:
            pass
        return FailureProcessingResult.Continue


def mm(value):
    return float(value) * MM_TO_FT


def curtain_types():
    result = []
    for wall_type in FilteredElementCollector(_active_doc()).OfClass(WallType):
        try:
            if getattr(wall_type, "Kind", None) == WallKind.Curtain:
                result.append(wall_type)
        except Exception:
            continue
    result.sort(key=safe_name)
    return result


def safe_name(element):
    try:
        return element.Name or u""
    except Exception:
        try:
            return u"%s" % element.Id.IntegerValue
        except Exception:
            return u""


def type_label(wall_type):
    try:
        width = wall_type.GetCompoundStructure().GetWidth() * FT_TO_MM
    except Exception:
        width = None
    name = safe_name(wall_type)
    if width:
        return u"%s (%.0f mm)" % (name, width)
    return name


def basic_types():
    result = []
    for wall_type in FilteredElementCollector(_active_doc()).OfClass(WallType):
        try:
            if getattr(wall_type, "Kind", None) == WallKind.Basic:
                result.append(wall_type)
        except Exception:
            continue
    result.sort(key=safe_name)
    return result


def all_levels():
    levels = [l for l in FilteredElementCollector(_active_doc()).OfClass(Level)]
    levels.sort(key=lambda l: l.Elevation)
    return levels


def level_label(level):
    try:
        return u"%s (%+.3f)" % (level.Name, level.Elevation * FT_TO_MM / 1000.0)
    except Exception:
        return u"?"


def element_line(element):
    """取图元的定位线（墙用 LocationCurve，模型线用 GeometryCurve）。"""
    if element is None:
        return None
    if isinstance(element, Wall):
        location = element.Location
        if location is not None and hasattr(location, "Curve"):
            return location.Curve
        return None
    if isinstance(element, CurveElement):
        try:
            return element.GeometryCurve
        except Exception:
            return None
    return None


def selected_elements():
    try:
        ids = revit.uidoc.Selection.GetElementIds()
    except Exception:
        return []
    result = []
    for eid in ids:
        element = _active_doc().GetElement(eid)
        if element is not None:
            result.append(element)
    return result


def existing_curtains(elements):
    return [e for e in elements if isinstance(e, Wall) and getattr(e, "CurtainGrid", None) is not None]


def wall_length_ft(wall):
    try:
        return wall.Location.Curve.Length
    except Exception:
        return 0.0


def wall_height_ft(wall):
    try:
        param = wall.get_Parameter(BuiltInParameter.WALL_USER_HEIGHT_PARAM)
        if param is not None:
            return param.AsDouble()
    except Exception:
        pass
    try:
        box = wall.get_BoundingBox(None)
        if box is not None:
            return box.Max.Z - box.Min.Z
    except Exception:
        pass
    return mm(3000)


def add_grid_lines(wall, u_spacing_mm, v_spacing_mm):
    """U 线 = 沿墙长方向的竖梃（按长度均分），V 线 = 水平横梃（按高度均分）。"""
    created = 0
    skipped = 0
    grid = getattr(wall, "CurtainGrid", None)
    if grid is None:
        return created, skipped

    curve = wall.Location.Curve
    if not isinstance(curve, Line):
        raise Exception(u"只支持直线定位线的幕墙（当前是曲线）")

    start = curve.GetEndPoint(0)
    end = curve.GetEndPoint(1)
    direction = (end - start).Normalize()
    length = curve.Length
    height = wall_height_ft(wall)
    level_z = start.Z

    # U 线（竖梃）：沿长度方向均分
    step_u = mm(u_spacing_mm) if u_spacing_mm > 0 else 0.0
    if step_u > 0.01:
        count_u = int(round(length / step_u))
        count_u = min(max(count_u, 1), 200)
        real_u = length / float(count_u)
        for index in range(1, count_u):
            point = start + direction * (real_u * index)
            try:
                grid.AddGridLine(True, point, False)
                created += 1
            except Exception as exc:
                skipped += 1
                log_line(u"[grid] U 线失败 index=%d: %s" % (index, exc))

    # V 线（横梃）：沿高度方向均分
    step_v = mm(v_spacing_mm) if v_spacing_mm > 0 else 0.0
    if step_v > 0.01:
        count_v = int(round(height / step_v))
        count_v = min(max(count_v, 1), 200)
        real_v = height / float(count_v)
        for index in range(1, count_v):
            point = start + XYZ(0, 0, real_v * index)
            try:
                grid.AddGridLine(False, point, False)
                created += 1
            except Exception as exc:
                skipped += 1
                log_line(u"[grid] V 线失败 index=%d: %s" % (index, exc))
    return created, skipped


def create_curtain(doc, line, wall_type, level, height_mm, base_offset_mm, u_mm, v_mm):
    wall = Wall.Create(
        doc,
        line,
        wall_type.Id,
        level.Id,
        mm(height_mm),
        mm(base_offset_mm),
        False,
        False,
    )
    lines, skipped = add_grid_lines(wall, u_mm, v_mm)
    return wall, lines, skipped


class CurtainWindow(forms.WPFWindow):
    XAML = u"""
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="幕墙分格" Width="620" Height="600" MinWidth="580" MinHeight="560"
        WindowStartupLocation="CenterScreen" ResizeMode="CanResize">
  <ScrollViewer VerticalScrollBarVisibility="Auto" HorizontalScrollBarVisibility="Disabled">
    <StackPanel Margin="18">

      <TextBlock Text="① 幕墙类型 / 标高" FontWeight="Bold" FontSize="15" Margin="0,0,0,8"/>
      <Grid Margin="0,0,0,14">
        <Grid.ColumnDefinitions>
          <ColumnDefinition Width="92"/>
          <ColumnDefinition Width="*"/>
        </Grid.ColumnDefinitions>
        <Grid.RowDefinitions>
          <RowDefinition Height="34"/>
          <RowDefinition Height="34"/>
        </Grid.RowDefinitions>
        <TextBlock Grid.Row="0" Grid.Column="0" Text="幕墙类型" VerticalAlignment="Center"/>
        <ComboBox Grid.Row="0" Grid.Column="1" x:Name="typeBox" Height="28"
                  HorizontalAlignment="Stretch" VerticalContentAlignment="Center"/>
        <TextBlock Grid.Row="1" Grid.Column="0" Text="标高" VerticalAlignment="Center"/>
        <ComboBox Grid.Row="1" Grid.Column="1" x:Name="levelBox" Height="28"
                  HorizontalAlignment="Stretch" VerticalContentAlignment="Center"/>
      </Grid>

      <TextBlock Text="② 尺寸与分格（mm）" FontWeight="Bold" FontSize="15" Margin="0,0,0,8"/>
      <Grid Margin="0,0,0,14">
        <Grid.ColumnDefinitions>
          <ColumnDefinition Width="92"/>
          <ColumnDefinition Width="*"/>
          <ColumnDefinition Width="92"/>
          <ColumnDefinition Width="*"/>
        </Grid.ColumnDefinitions>
        <Grid.RowDefinitions>
          <RowDefinition Height="34"/>
          <RowDefinition Height="34"/>
        </Grid.RowDefinitions>
        <TextBlock Grid.Row="0" Grid.Column="0" Text="墙高" VerticalAlignment="Center"/>
        <TextBox Grid.Row="0" Grid.Column="1" x:Name="heightBox" Text="3000" Height="28"
                 Margin="0,0,14,0" VerticalContentAlignment="Center"/>
        <TextBlock Grid.Row="0" Grid.Column="2" Text="底偏移" VerticalAlignment="Center"/>
        <TextBox Grid.Row="0" Grid.Column="3" x:Name="offsetBox" Text="0" Height="28"
                 VerticalContentAlignment="Center"/>

        <TextBlock Grid.Row="1" Grid.Column="0" Text="竖梃间距" VerticalAlignment="Center"/>
        <TextBox Grid.Row="1" Grid.Column="1" x:Name="uBox" Text="1200" Height="28"
                 Margin="0,0,14,0" VerticalContentAlignment="Center"/>
        <TextBlock Grid.Row="1" Grid.Column="2" Text="横梃间距" VerticalAlignment="Center"/>
        <TextBox Grid.Row="1" Grid.Column="3" x:Name="vBox" Text="1500" Height="28"
                 VerticalContentAlignment="Center"/>
      </Grid>

      <TextBlock Text="③ 操作" FontWeight="Bold" FontSize="15" Margin="0,0,0,8"/>
      <TextBlock x:Name="hintBox" TextWrapping="Wrap" Margin="0,0,0,10"
                 Text="先选中一条线（模型线 / 详图线）或一段直线墙 → 点「按选中线建幕墙」；或只选已有幕墙 → 点「给已有幕墙补分格」。分格按间距均分，不会留半格。"/>
      <Button x:Name="btnCreate" Content="按选中线建幕墙" Height="34" Margin="0,0,0,8" FontWeight="Bold"/>
      <Button x:Name="btnFill" Content="给已有幕墙补分格" Height="34" Margin="0,0,0,8" FontWeight="Bold"/>
      <Button x:Name="btnMerge" Content="合并选中网格线（删线并格）" Height="34" Margin="0,0,0,8" FontWeight="Bold"/>
      <TextBlock x:Name="statusBox" TextWrapping="Wrap" Margin="0,4,0,10"
                 Text="就绪。选中对象后再点上面的按钮。"/>
      <Button x:Name="btnClose" Content="关闭" Height="30" Width="110" HorizontalAlignment="Right"/>
    </StackPanel>
  </ScrollViewer>
</Window>
"""

    def __init__(self):
        forms.WPFWindow.__init__(self, self.XAML, literal_string=True)
        self.action = None
        self.params = {}
        self._fill()

    def _fill(self):
        # 先取控件，保证后面的任何失败都不会让属性缺失
        self.type_box = self.FindName("typeBox")
        self.level_box = self.FindName("levelBox")
        self.height_box = self.FindName("heightBox")
        self.offset_box = self.FindName("offsetBox")
        self.u_box = self.FindName("uBox")
        self.v_box = self.FindName("vBox")
        self.status_box = self.FindName("statusBox")

        self.type_items = []
        try:
            types = curtain_types()
            self.type_items = types
            self.type_box.Items.Clear()
            for wall_type in types:
                self.type_box.Items.Add(u"%s" % type_label(wall_type))
            if types:
                self.type_box.SelectedIndex = 0
            else:
                self.type_box.Items.Add(u"（项目里没有幕墙类型）")
                self.type_box.SelectedIndex = 0
                self._say(u"项目里没有「幕墙」墙类型：先用 Revit 自带的 建筑 → 墙 → "
                          u"类型选择器选「幕墙」画一段，再回来用本工具。")
        except Exception as exc:
            log_line(u"[init] 幕墙类型失败: %s" % exc)
            self._say(u"读取幕墙类型失败：%s" % exc)

        self.level_items = []
        try:
            levels = all_levels()
            self.level_items = levels
            self.level_box.Items.Clear()
            for level in levels:
                self.level_box.Items.Add(level_label(level))
            if levels:
                self.level_box.SelectedIndex = 0
        except Exception as exc:
            log_line(u"[init] 标高失败: %s" % exc)
            self._say(u"读取标高失败：%s" % exc)

        self.FindName("btnCreate").Click += self._on_create
        self.FindName("btnFill").Click += self._on_fill
        self.FindName("btnMerge").Click += self._on_merge
        self.FindName("btnClose").Click += self._on_close

    def _say(self, text):
        try:
            self.status_box.Text = text
        except Exception:
            pass

    def _num(self, box, default):
        try:
            return float((box.Text or u"").strip())
        except Exception:
            return float(default)

    def _collect(self):
        type_index = 0
        level_index = 0
        try:
            if self.type_box is not None and self.type_box.SelectedIndex > 0:
                type_index = int(self.type_box.SelectedIndex)
        except Exception:
            type_index = 0
        try:
            if self.level_box is not None and self.level_box.SelectedIndex > 0:
                level_index = int(self.level_box.SelectedIndex)
        except Exception:
            level_index = 0
        params = {
            "height_mm": self._num(self.height_box, 3000),
            "offset_mm": self._num(self.offset_box, 0),
            "u_mm": self._num(self.u_box, 1200),
            "v_mm": self._num(self.v_box, 1500),
            "type_index": type_index,
            "level_index": level_index,
        }
        return params

    def _on_create(self, sender, args):
        if not self.type_items:
            forms.alert(u"项目里还没有「幕墙」墙类型。\n\n"
                        u"做法：建筑 → 墙 → 在类型选择器里选「幕墙」→ 随便画一段，\n"
                        u"再回来点本按钮，就能批量建了。", title=u"幕墙分格")
            return
        self.params = self._collect()
        self.action = "create"
        self.Close()

    def _on_merge(self, sender, args):
        deleted, failures = merge_grid_lines()
        if failures:
            text = u"已删除 %d 条网格线，失败 %d 条：%s" % (deleted, len(failures), failures[0])
        else:
            text = u"已删除 %d 条网格线，相邻格已合并" % deleted
        self.status.Text = text
        forms.alert(text, title=u"幕墙分格")

    def _on_fill(self, sender, args):
        self.params = self._collect()
        self.action = "fill"
        self.Close()

    def _on_close(self, sender, args):
        self.action = None
        self.Close()


def selected_grid_lines():
    result = []
    try:
        ids = list(revit.uidoc.Selection.GetElementIds())
    except Exception:
        return result
    for element_id in ids:
        element = doc.GetElement(element_id)
        if isinstance(element, CurtainGridLine):
            result.append(element)
    return result


def merge_grid_lines():
    """删掉选中的幕墙网格线，让相邻单元格合并成一块板。"""
    lines = selected_grid_lines()
    if not lines:
        return 0, [u"请先选中要删掉的幕墙网格线（三维或立面里可多选）"]
    transaction = Transaction(doc, u"合并幕墙分格")
    transaction.Start()
    deleted = 0
    failures = []
    try:
        for line in lines:
            try:
                doc.Delete(line.Id)
                deleted += 1
            except Exception:
                failures.append(u"%s: %s" % (line.Id, traceback.format_exc().splitlines()[-1]))
        transaction.Commit()
    except Exception:
        transaction.RollBack()
        failures.append(traceback.format_exc().splitlines()[-1])
    log_line(u"[merge] 删除 %d 条网格线，失败 %d" % (deleted, len(failures)))
    return deleted, failures


def _guard(func):
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception:
            text = traceback.format_exc()
            log_line(text)
            forms.alert(u"操作出错（已阻止崩溃）：\n%s" % text[-1500:], title=u"幕墙分格")
            return None

    return wrapper


@_guard
def do_create(window):
    doc = _active_doc()
    elements = selected_elements()
    lines = []
    for element in elements:
        line = element_line(element)
        if isinstance(line, Line):
            lines.append(line)
    if not lines:
        return (0, [u"没有选中直线（模型线 / 详图线 / 直线墙）"])

    types = curtain_types()
    levels = all_levels()
    if not types:
        return (0, [u"项目里没有「幕墙」类型的墙类型，请先载入幕墙族或新建幕墙类型"])
    if not levels:
        return (0, [u"项目里没有标高"])

    type_index = min(max(window.params.get("type_index", 0), 0), len(types) - 1)
    level_index = min(max(window.params.get("level_index", 0), 0), len(levels) - 1)
    wall_type = types[type_index]
    level = levels[level_index]

    created = 0
    failures = []
    transaction = Transaction(doc, u"幕墙分格")
    transaction.Start()
    try:
        options = transaction.GetFailureHandlingOptions()
        options.SetFailuresPreprocessor(QuietPreprocessor())
        transaction.SetFailureHandlingOptions(options)
    except Exception:
        pass

    try:
        for line in lines:
            try:
                wall, grid_lines, skipped = create_curtain(
                    doc,
                    line,
                    wall_type,
                    level,
                    window.params.get("height_mm", 3000),
                    window.params.get("offset_mm", 0),
                    window.params.get("u_mm", 1200),
                    window.params.get("v_mm", 1500),
                )
                created += 1
                log_line(u"[create] 幕墙 %s 长度=%.0fmm 分格线=%d 跳过=%d"
                         % (wall.Id, line.Length * FT_TO_MM, grid_lines, skipped))
            except Exception as exc:
                failures.append(u"建幕墙失败：%s" % exc)
                log_line(u"[create] 失败: %s" % traceback.format_exc())
        transaction.Commit()
    except Exception:
        transaction.RollBack()
        raise

    if created == 0:
        return (0, failures or [u"没有生成任何幕墙"])

    try:
        ids = []
        for _ in range(0):
            pass
        revit.uidoc.Selection.SetElementIds(ids)
    except Exception:
        pass
    return (created, failures)


@_guard
def do_fill(window):
    doc = _active_doc()
    curtains = existing_curtains(selected_elements())
    if not curtains:
        return (0, [u"没有选中幕墙（选中墙的类别必须是「幕墙」）"])

    total_lines = 0
    failures = []
    transaction = Transaction(doc, u"幕墙补分格")
    transaction.Start()
    try:
        options = transaction.GetFailureHandlingOptions()
        options.SetFailuresPreprocessor(QuietPreprocessor())
        transaction.SetFailureHandlingOptions(options)
    except Exception:
        pass

    try:
        for wall in curtains:
            try:
                lines, skipped = add_grid_lines(
                    wall,
                    window.params.get("u_mm", 1200),
                    window.params.get("v_mm", 1500),
                )
                total_lines += lines
                log_line(u"[fill] 幕墙 %s 补线=%d 跳过=%d" % (wall.Id, lines, skipped))
            except Exception as exc:
                failures.append(u"%s：%s" % (wall.Id, exc))
                log_line(u"[fill] 失败: %s" % traceback.format_exc())
        transaction.Commit()
    except Exception:
        transaction.RollBack()
        raise

    return (total_lines, failures)


def main():
    log_line(u"== 幕墙分格 %s ==" % __file__)
    while True:
        window = CurtainWindow()
        window.ShowDialog()
        action = getattr(window, "action", None)
        if action == "create":
            created, failures = do_create(window)
            message = u"已生成幕墙 %d 段。" % created
            if failures:
                message += u"\n\n失败 %d 条：\n%s" % (len(failures), u"\n".join(failures[:8]))
            forms.alert(message, title=u"幕墙分格")
            continue
        if action == "fill":
            count, failures = do_fill(window)
            message = u"已补充分格线 %d 条。" % count
            if failures:
                message += u"\n\n失败 %d 条：\n%s" % (len(failures), u"\n".join(failures[:8]))
            forms.alert(message, title=u"幕墙分格")
            continue
        break


try:
    main()
except Exception:
    text = traceback.format_exc()
    log_line(text)
    forms.alert(u"脚本出错：\n%s" % text[-1500:], title=u"幕墙分格")
