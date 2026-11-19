# -*- coding: utf-8 -*-
"""标注对齐（六吨工具箱）

把选中的多个尺寸标注整理整齐：
  ① 尺寸线对齐：以第一个选中的标注为基准，其余标注沿其法向平移，
     使所有标注线落在同一条直线上（轴网尺寸、总尺寸最常用）。
  ② 文字归位：把标注文字从手工拖动的位置复位到标注线上（水平/垂直居中）。
  ③ 两者都做：先对齐线，再归位文字。

说明：Revit 2020 的 API 不允许修改轴网线的端点长度（Grid 没有 SetCurve），
所以「轴号圆圈对齐」这类需要改轴线长度的操作无法自动完成；
本工具解决的是「尺寸标注线不齐 / 文字歪」的图面问题。

单位：界面 mm，内部英尺。
"""
import os
import traceback

import clr
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")
clr.AddReference("System")
clr.AddReference("WindowsBase")
clr.AddReference("PresentationCore")
clr.AddReference("PresentationFramework")

from System.Collections.Generic import List as DotNetList

from Autodesk.Revit.DB import (
    Dimension,
    Grid, ElementId, ElementTransformUtils, Line, Transaction, XYZ,
)
from Autodesk.Revit.UI.Selection import ObjectType

from pyrevit import revit, forms, script

doc = revit.doc
uidoc = revit.uidoc
output = script.get_output()

FT_TO_MM = 304.8
LOG_PATH = r"C:\Users\cyl\Documents\deepseek-harness\default-workspace\_标注对齐日志.txt"


def log_line(text):
    try:
        import io
        with io.open(LOG_PATH, "a", encoding="utf-8") as handle:
            handle.write(u"%s\n" % text)
    except Exception:
        pass


def inspect_selection():
    """数一数选中里各有多少：尺寸标注 / 轴线 / 其它。"""
    dimensions = 0
    grids = 0
    others = 0
    for element_id in revit.uidoc.Selection.GetElementIds():
        element = doc.GetElement(element_id)
        if element is None:
            continue
        if isinstance(element, Dimension):
            dimensions += 1
        elif isinstance(element, Grid):
            grids += 1
        else:
            others += 1
    return dimensions, grids, others


def selected_dimensions():
    """当前选中的尺寸标注（按选择顺序）。"""
    result = []
    try:
        ids = uidoc.Selection.GetElementIds()
    except Exception:
        return result
    for element_id in ids:
        element = doc.GetElement(element_id)
        if isinstance(element, Dimension):
            result.append(element)
    return result


def dim_line(dimension):
    """取标注所在的直线（曲线方向 + 起点 + 法向）。"""
    curve = dimension.Curve
    if curve is None:
        return None
    try:
        if not isinstance(curve, Line):
            return None
        direction = curve.Direction.Normalize()
        normal = XYZ(-direction.Y, direction.X, 0.0)
        if normal.GetLength() < 1e-6:
            return None
        return curve, direction, normal.Normalize()
    except Exception:
        return None


def plan_line_alignment(dimensions):
    """计算每个标注需要沿法向平移的距离（相对第一个标注）。"""
    base_info = dim_line(dimensions[0])
    if base_info is None:
        return [], u"第一个标注不是直线标注，无法作为基准。"
    base_curve, base_dir, base_normal = base_info
    base_origin = base_curve.GetEndPoint(0)

    moves = []
    for dimension in dimensions[1:]:
        info = dim_line(dimension)
        if info is None:
            continue
        curve, direction, normal = info
        if abs(abs(direction.DotProduct(base_dir)) - 1.0) > 0.001:
            continue  # 与基准不平行，跳过
        origin = curve.GetEndPoint(0)
        delta = (base_origin - origin).DotProduct(base_normal)
        if abs(delta) < 1e-6:
            continue
        moves.append((dimension, base_normal.Multiply(delta)))
    return moves, u""


def plan_text_reset(dimensions):
    """需要复位文字位置的标注。"""
    targets = []
    for dimension in dimensions:
        try:
            if dimension.IsTextPositionAdjustable():
                targets.append(dimension)
        except Exception:
            continue
    return targets


def run(params):
    dimensions = params["dimensions"]
    mode = params["mode"]
    ok = 0
    failures = []
    moved = 0
    reset = 0

    transaction = Transaction(doc, u"标注对齐")
    transaction.Start()
    try:
        if mode in ("line", "both"):
            moves, message = plan_line_alignment(dimensions)
            if message:
                failures.append(message)
            for dimension, offset in moves:
                try:
                    ElementTransformUtils.MoveElement(doc, dimension.Id, offset)
                    moved += 1
                    ok += 1
                except Exception as error:
                    failures.append(u"平移失败：%s" % error)

        if mode in ("text", "both"):
            for dimension in plan_text_reset(dimensions):
                try:
                    dimension.ResetTextPosition()
                    reset += 1
                    ok += 1
                except Exception as error:
                    failures.append(u"文字归位失败：%s" % error)

        transaction.Commit()
    except Exception:
        transaction.RollBack()
        failures.append(traceback.format_exc()[-600:])

    log_line(u"mode=%s dims=%d moved=%d reset=%d fail=%d"
             % (mode, len(dimensions), moved, reset, len(failures)))
    return ok, moved, reset, failures


SHELL_XAML = u"""
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="标注对齐" Height="470" Width="620"
        WindowStartupLocation="CenterScreen">
  <ScrollViewer VerticalScrollBarVisibility="Auto">
    <StackPanel Margin="18">
      <TextBlock Text="标注对齐" FontSize="20" FontWeight="Bold" Margin="0,0,0,10"/>
      <TextBlock x:Name="hintText" TextWrapping="Wrap" Foreground="#FF5A6B85" Margin="0,0,0,14"/>

      <TextBlock Text="① 对齐方式" FontWeight="Bold" Margin="0,0,0,6"/>
      <ComboBox x:Name="modeBox" Height="30" Margin="0,0,0,14">
        <ComboBoxItem Content="尺寸线对齐（以第一个为基准，其余平移到同一直线）"/>
        <ComboBoxItem Content="文字归位（把拖歪的标注文字放回标注线）"/>
        <ComboBoxItem Content="两者都做"/>
      </ComboBox>

      <TextBlock Text="② 选择范围" FontWeight="Bold" Margin="0,0,0,6"/>
      <TextBlock x:Name="scopeText" TextWrapping="Wrap" Foreground="#FF5A6B85" Margin="0,0,0,14"/>

      <TextBlock Text="③ 执行" FontWeight="Bold" Margin="0,0,0,6"/>
      <TextBlock x:Name="previewText" TextWrapping="Wrap" Margin="0,0,0,10"/>

      <StackPanel Orientation="Horizontal" Margin="0,6,0,0">
        <Button x:Name="btnRefresh" Content="刷新选择" Width="110" Height="36" Margin="0,0,8,0"/>
        <Button x:Name="btnRun" Content="对齐" Width="110" Height="36" Margin="0,0,8,0"/>
        <Button x:Name="btnClose" Content="关闭" Width="90" Height="36"/>
      </StackPanel>
    </StackPanel>
  </ScrollViewer>
</Window>
"""


class AlignWindow(forms.WPFWindow):
    def __init__(self):
        forms.WPFWindow.__init__(self, SHELL_XAML, literal_string=True)
        self.mode_box = self.FindName("modeBox")
        self.hint_text = self.FindName("hintText")
        self.scope_text = self.FindName("scopeText")
        self.preview_text = self.FindName("previewText")
        self.mode_box.SelectedIndex = 0
        self.hint_text.Text = (u"先在平面视图里框选/点选多个尺寸标注（至少 2 个），"
                               u"第一个选中的作为基准，再点「对齐」。")
        self.FindName("btnRefresh").Click += self._guard(self._on_refresh)
        self.FindName("btnRun").Click += self._guard(self._on_run)
        self.FindName("btnClose").Click += self._guard(self._on_close)
        self.dimensions = []
        self.result = None
        self._on_refresh(None, None)

    # ---------- 交互 ----------
    def _guard(self, handler):
        def wrapped(sender, args):
            try:
                handler(sender, args)
            except Exception:
                text = traceback.format_exc()
                log_line(text)
                try:
                    forms.alert(u"操作出错（已阻止崩溃）：\n%s" % text[-600:], title=u"标注对齐")
                except Exception:
                    pass
        return wrapped

    def _on_refresh(self, sender, args):
        self.dimensions = selected_dimensions()
        self.scope_text.Text = u"当前选中 %d 个尺寸标注。" % len(self.dimensions)
        if self.dimensions:
            first = self.dimensions[0]
            try:
                value = first.ValueString or u""
            except Exception:
                value = u""
            self.preview_text.Text = u"基准：%s（Id %s）" % (
                value or u"<无文字>", first.Id.IntegerValue)
        else:
            self.preview_text.Text = u"还没选标注：先在视图里选几个尺寸标注。"

    def _on_run(self, sender, args):
        self._on_refresh(None, None)
        if len(self.dimensions) < 2:
            dims, grids, others = inspect_selection()
            if grids and not dims:
                forms.alert(u"你选中的是 %d 根轴线（%d 个其它图元），不是尺寸标注。\n\n"
                            u"· 这个按钮只对「尺寸标注」生效；\n"
                            u"· 轴号位置受轴线控制，Revit 2020 的 API 不能单独移动轴号气泡；\n"
                            u"· 如果轴线本身位置不对，请用「一键生成轴网」重建，或直接拖动轴线。"
                            % (grids, others), title=u"标注对齐")
            elif others and not dims:
                forms.alert(u"选中的 %d 个图元里没有尺寸标注（轴线 %d 根）。\n"
                            u"请在图纸里点选「尺寸标注」的文字或尺寸线，再点对齐。"
                            % (others + grids, grids), title=u"标注对齐")
            else:
                forms.alert(u"请先选中至少 2 个尺寸标注（现在选中 %d 个）。" % dims,
                            title=u"标注对齐")
            return
        mode = ["line", "text", "both"][max(0, min(2, self.mode_box.SelectedIndex))]
        ok, moved, reset, failures = run({
            "dimensions": self.dimensions,
            "mode": mode,
        })
        lines = [u"对齐完成：成功处理 %d 项（平移 %d 个、文字归位 %d 个）。"
                 % (ok, moved, reset)]
        if failures:
            lines.append(u"")
            lines.append(u"未处理：")
            for item in failures[:8]:
                lines.append(u"· %s" % item)
        lines.append(u"")
        lines.append(u"改错了可以 Ctrl+Z 整体撤销。")
        forms.alert(u"\n".join(lines), title=u"标注对齐")

    def _on_close(self, sender, args):
        self.Close()


def main():
    if doc is None:
        forms.alert(u"请先打开一个项目。", exitscript=True)
    window = AlignWindow()
    window.ShowDialog()


try:
    main()
except Exception:
    text = traceback.format_exc()
    log_line(text)
    output.print_md(u"脚本出错：\n```\n%s\n```" % text)
    forms.alert(u"脚本出错：\n%s" % text[-800:], title=u"标注对齐")
