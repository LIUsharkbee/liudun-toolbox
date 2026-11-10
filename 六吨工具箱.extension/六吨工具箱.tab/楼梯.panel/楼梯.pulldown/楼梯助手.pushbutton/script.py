# -*- coding: utf-8 -*-
__title__ = u"楼梯助手"
__doc__ = u"""按层高和踏步尺寸自动算楼梯：画平面辅助线，并可直接生成实体楼梯。
支持直跑、双跑（U 形）、三跑；室外台阶用「弧形台阶」或本工具直跑模式近似。"""

from pyrevit import forms, script
import clr
import math
import os
import traceback

clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')

from Autodesk.Revit.DB import (
    Transaction, XYZ, Line, SketchPlane, Plane, ElementId, BuiltInParameter,
    FilteredElementCollector, BuiltInCategory, Level, StairsEditScope,
    DirectShape, GeometryCreationUtilities, CurveLoop, GeometryObject,
)
from Autodesk.Revit.DB.Architecture import (
    StairsRun, StairsRunJustification, StairsLanding,
)
from System.Collections.Generic import List as DotNetList
from System.Windows import Thickness, TextWrapping, FontWeights, VerticalAlignment
from System.Windows.Controls import (
    TextBlock, TextBox, ComboBox, CheckBox, StackPanel, ScrollViewer, Button, Orientation,
)
from System.Windows.Media import Brushes

FT_TO_MM = 304.8
MM_TO_FT = 1.0 / 304.8
DEG = math.pi / 180.0

LOG_PATH = r"C:\Users\cyl\Documents\deepseek-harness\default-workspace\_楼梯助手日志.txt"

doc = __revit__.ActiveUIDocument.Document
uidoc = __revit__.ActiveUIDocument


def log(text):
    try:
        handle = open(LOG_PATH, "a")
        handle.write(u"{0}\n".format(text))
        handle.close()
    except Exception:
        pass


def all_levels():
    levels = FilteredElementCollector(doc).OfClass(Level).ToElements()
    levels = [level for level in levels if level is not None]
    levels.sort(key=lambda item: item.Elevation)
    return levels


def to_float(text, default):
    try:
        return float((text or u"").strip())
    except Exception:
        return default


def check_step(riser, tread):
    notes = []
    if riser < 150 or riser > 175:
        notes.append(u"踏步高 {0:.1f} mm 不在 150–175 之间".format(riser))
    if tread < 260 or tread > 300:
        notes.append(u"踏面宽 {0:.0f} mm 不在 260–300 之间".format(tread))
    rule = 2 * riser + tread
    if rule < 600 or rule > 640:
        notes.append(u"2R+T = {0:.1f} mm 不在 600–640 之间".format(rule))
    return notes


# ---------------------------------------------------------------- 几何计算
def compute(params):
    form = params["form"]
    height = params["height"]
    riser = max(100.0, params["riser"])
    tread = max(180.0, params["tread"])
    width = max(600.0, params["width"])
    landing = max(600.0, params["landing"])
    gap = max(0.0, params["gap"])

    total = int(round(height / riser))
    if total < 2:
        total = 2
    real_riser = height / float(total)

    counts = []
    runs = []
    landings = []

    if form == 1:
        # 直跑
        first = total
        counts = [first]
        length = first * tread
        runs.append((width / 2.0, 0.0, width / 2.0, length, 0))
        bounds = (width, length)
    elif form == 2:
        # 双跑 U 形
        first = int(math.ceil(total / 2.0))
        second = total - first
        counts = [first, second]
        len1 = first * tread
        len2 = second * tread
        runs.append((width / 2.0, 0.0, width / 2.0, len1, 0))
        runs.append((width + gap + width / 2.0, len1 + landing,
                     width + gap + width / 2.0, len1 + landing - len2, first))
        landings.append((0.0, len1, 2.0 * width + gap, len1 + landing))
        bounds = (2.0 * width + gap, len1 + landing)
    else:
        # 三跑：三个平行梯段 + 两个平台（Z 形折返）
        first = int(math.floor(total / 3.0))
        rest = total - first
        second = int(math.ceil(rest / 2.0))
        third = total - first - second
        if first < 2:
            first = 2
        if second < 2:
            second = 2
        if third < 1:
            third = 1
        counts = [first, second, third]
        len1 = first * tread
        len2 = second * tread
        len3 = third * tread
        x1 = width / 2.0
        x2 = width + gap + width / 2.0
        x3 = 2.0 * (width + gap) + width / 2.0
        runs.append((x1, 0.0, x1, len1, 0))
        runs.append((x2, len1 + landing, x2, len1 + landing - len2, first))
        y3 = len1 + landing - len2 - landing
        runs.append((x3, y3, x3, y3 + len3, first + second))
        landings.append((0.0, len1, 2.0 * width + gap, len1 + landing))
        landings.append((x2 - width / 2.0, y3 - landing,
                         x3 + width / 2.0, y3))
        bounds = (3.0 * width + 2.0 * gap, len1 + landing)

    lines = build_lines(runs, landings, tread, width)

    plan = {
        "form": form,
        "total": total,
        "riser": real_riser,
        "tread": tread,
        "width": width,
        "gap": gap,
        "landing": landing,
        "rotate": params["rotate"],
        "origin": params["origin"],
        "counts": counts,
        "runs": runs,
        "landings": landings,
        "lines": lines,
        "bounds": bounds,
        "notes": check_step(real_riser, tread),
    }
    return plan


def build_lines(runs, landings, tread, width):
    lines = []
    half = width / 2.0
    for x1, y1, x2, y2, base in runs:
        dx = x2 - x1
        dy = y2 - y1
        length = math.sqrt(dx * dx + dy * dy)
        if length < 1e-6:
            continue
        ux = dx / length
        uy = dy / length
        nx = -uy
        ny = ux
        ax1 = x1 + nx * half
        ay1 = y1 + ny * half
        ax2 = x2 + nx * half
        ay2 = y2 + ny * half
        bx1 = x1 - nx * half
        by1 = y1 - ny * half
        bx2 = x2 - nx * half
        by2 = y2 - ny * half
        lines.append((ax1, ay1, ax2, ay2, False))
        lines.append((bx1, by1, bx2, by2, False))
        lines.append((ax1, ay1, bx1, by1, False))
        lines.append((ax2, ay2, bx2, by2, False))
        steps = int(round(length / tread))
        step = length / float(steps)
        for index in range(1, steps):
            t = index * step
            sx1 = ax1 + (ax2 - ax1) * (t / length)
            sy1 = ay1 + (ay2 - ay1) * (t / length)
            sx2 = bx1 + (bx2 - bx1) * (t / length)
            sy2 = by1 + (by2 - by1) * (t / length)
            lines.append((sx1, sy1, sx2, sy2, True))

    for x0, y0, x1, y1 in landings:
        lines.append((x0, y0, x1, y0, False))
        lines.append((x1, y0, x1, y1, False))
        lines.append((x1, y1, x0, y1, False))
        lines.append((x0, y1, x0, y0, False))
    return lines


def transform(plan, x, y, z):
    angle = plan["rotate"] * DEG
    cos_a = math.cos(angle)
    sin_a = math.sin(angle)
    origin = plan["origin"]
    xf = x * MM_TO_FT
    yf = y * MM_TO_FT
    return XYZ(origin.X + xf * cos_a - yf * sin_a,
               origin.Y + xf * sin_a + yf * cos_a,
               z)


def draw_lines(plan, level_z, draw_steps):
    transaction = Transaction(doc, u"楼梯助手-辅助线")
    transaction.Start()
    try:
        plane = Plane.CreateByNormalAndOrigin(XYZ.BasisZ, XYZ(0, 0, level_z))
        sketch_plane = SketchPlane.Create(doc, plane)
        count = 0
        for x1, y1, x2, y2, is_step in plan["lines"]:
            if is_step and not draw_steps:
                continue
            start = transform(plan, x1, y1, level_z)
            end = transform(plan, x2, y2, level_z)
            if start.DistanceTo(end) < 1e-4:
                continue
            doc.Create.NewModelCurve(Line.CreateBound(start, end), sketch_plane)
            count += 1
    except Exception as ex:
        if transaction.HasStarted():
            transaction.RollBack()
        log(u"draw_lines 失败：{0}".format(traceback.format_exc()))
        raise
    transaction.Commit()
    return count


def make_stairs(plan, base_level, top_level):
    scope = StairsEditScope(doc, u"楼梯助手")
    stairs_id = scope.Start(base_level.Id, top_level.Id)
    transaction = Transaction(doc, u"楼梯助手-实体")
    transaction.Start()
    try:
        run_ids = []
        for x1, y1, x2, y2, base_risers in plan["runs"]:
            z = base_level.Elevation + base_risers * plan["riser"] * MM_TO_FT
            start = transform(plan, x1, y1, z)
            end = transform(plan, x2, y2, z)
            run = StairsRun.CreateStraightRun(
                doc, stairs_id, Line.CreateBound(start, end),
                StairsRunJustification.Center)
            run_ids.append(run.Id)
        for index in range(len(run_ids) - 1):
            StairsLanding.CreateAutomaticLanding(
                doc, run_ids[index], run_ids[index + 1])
        transaction.Commit()
    except Exception:
        if transaction.HasStarted():
            transaction.RollBack()
        try:
            scope.Cancel()
        except Exception:
            pass
        log(u"make_stairs 失败：{0}".format(traceback.format_exc()))
        raise
    scope.Commit(transaction)
    return stairs_id


def make_outdoor_steps(plan, base_level):
    """室外台阶：逐级做落地实体（DirectShape，楼板类别）。"""
    transaction = Transaction(doc, u"楼梯助手-室外台阶")
    transaction.Start()
    created = []
    try:
        floor_category = ElementId(BuiltInCategory.OST_Floors)
        total = plan["total"]
        tread = plan["tread"]
        width = plan["width"]
        base_z = base_level.Elevation
        for index in range(total):
            top = plan["riser"] * (total - index) * MM_TO_FT + base_z
            height = top - base_z
            if height < 0.01:
                continue
            y0 = index * tread
            y1 = y0 + tread
            p1 = transform(plan, 0.0, y0, base_z)
            p2 = transform(plan, width, y0, base_z)
            p3 = transform(plan, width, y1, base_z)
            p4 = transform(plan, 0.0, y1, base_z)
            loop = CurveLoop()
            loop.Append(Line.CreateBound(XYZ(p1.X, p1.Y, base_z), XYZ(p2.X, p2.Y, base_z)))
            loop.Append(Line.CreateBound(XYZ(p2.X, p2.Y, base_z), XYZ(p3.X, p3.Y, base_z)))
            loop.Append(Line.CreateBound(XYZ(p3.X, p3.Y, base_z), XYZ(p4.X, p4.Y, base_z)))
            loop.Append(Line.CreateBound(XYZ(p4.X, p4.Y, base_z), XYZ(p1.X, p1.Y, base_z)))
            loops = DotNetList[CurveLoop]()
            loops.Add(loop)
            solid = GeometryCreationUtilities.CreateExtrusionGeometry(
                loops, XYZ.BasisZ, height)
            shape = DirectShape.CreateElement(doc, floor_category)
            shape.SetShape(DotNetList[GeometryObject]([solid]))
            mark = shape.get_Parameter(BuiltInParameter.ALL_MODEL_MARK)
            if mark is not None and not mark.IsReadOnly:
                mark.Set(u"室外台阶-{0}".format(index + 1))
            created.append(shape.Id)
        transaction.Commit()
    except Exception:
        if transaction.HasStarted():
            transaction.RollBack()
        log(u"make_outdoor_steps 失败：{0}".format(traceback.format_exc()))
        raise
    return created


def describe(plan):
    lines = []
    if plan["form"] == 1:
        lines.append(u"形式：直跑")
    elif plan["form"] == 2:
        lines.append(u"形式：双跑（U 形，带中间平台）")
    else:
        lines.append(u"形式：三跑")
    lines.append(u"层高 {0:.0f} mm，踏步数 {1}，实际踏步高 {2:.1f} mm，踏面宽 {3:.0f} mm".format(
        plan["total"] * plan["riser"], plan["total"], plan["riser"], plan["tread"]))
    lines.append(u"各跑踏步数：" + u"/".join([str(item) for item in plan["counts"]]))
    lines.append(u"楼梯间净尺寸约 {0:.0f} × {1:.0f} mm（宽 × 长）".format(
        plan["bounds"][0], plan["bounds"][1]))
    lines.append(u"2R+T = {0:.1f} mm".format(2 * plan["riser"] + plan["tread"]))
    for note in plan["notes"]:
        lines.append(u"· " + note)
    return u"\n".join(lines)


# ---------------------------------------------------------------- 界面
def label(text, width=110):
    block = TextBlock()
    block.Text = text
    block.Width = width
    block.VerticalAlignment = VerticalAlignment.Center
    return block


def row(children):
    panel = StackPanel()
    panel.Orientation = Orientation.Horizontal
    for child in children:
        child.Margin = Thickness(2, 2, 2, 2)
        panel.Children.Add(child)
    return panel


def button(text, width=100):
    item = Button()
    item.Content = text
    item.Width = width
    item.Height = 26
    return item


class StairWindow(forms.WPFWindow):
    XAML = """
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="楼梯助手" Height="620" Width="560"
        WindowStartupLocation="CenterScreen" ResizeMode="CanResize">
  <ScrollViewer VerticalScrollBarVisibility="Auto">
    <StackPanel x:Name="Root" Margin="12" />
  </ScrollViewer>
</Window>
"""

    def __init__(self, levels, origin=None):
        forms.WPFWindow.__init__(self, self.XAML, literal_string=True)
        self.levels = levels
        self.origin = origin
        self.result = None
        root = self.Content.Content

        self.form_box = ComboBox()
        self.form_box.Width = 220
        self.form_box.Items.Add(u"直跑（单跑）")
        self.form_box.Items.Add(u"双跑（U 形，带平台）")
        self.form_box.Items.Add(u"三跑")
        self.form_box.SelectedIndex = 1

        self.level_box = ComboBox()
        self.level_box.Width = 260
        for level in levels:
            self.level_box.Items.Add(u"{0}（{1:.3f} m）".format(
                level.Name, level.Elevation * FT_TO_MM / 1000.0))
        if levels:
            self.level_box.SelectedIndex = 0

        self.height_box = TextBox()
        self.height_box.Text = u"3000"
        self.height_box.Width = 110

        self.riser_box = TextBox()
        self.riser_box.Text = u"150"
        self.riser_box.Width = 90

        self.tread_box = TextBox()
        self.tread_box.Text = u"300"
        self.tread_box.Width = 90

        self.width_box = TextBox()
        self.width_box.Text = u"1200"
        self.width_box.Width = 90

        self.landing_box = TextBox()
        self.landing_box.Text = u"1200"
        self.landing_box.Width = 90

        self.gap_box = TextBox()
        self.gap_box.Text = u"100"
        self.gap_box.Width = 90

        self.rotate_box = TextBox()
        self.rotate_box.Text = u"0"
        self.rotate_box.Width = 90

        self.origin_text = TextBlock()
        self.origin_text.Text = u"未拾取（默认模型原点 0,0）"
        self.origin_text.Foreground = Brushes.Gray
        if origin is not None:
            self.origin_text.Text = u"({0:.1f}, {1:.1f}) mm".format(
                origin.X * FT_TO_MM, origin.Y * FT_TO_MM)

        self.draw_steps = CheckBox()
        self.draw_steps.Content = u"同时画踏步线"
        self.draw_steps.IsChecked = True

        self.mode_box = ComboBox()
        self.mode_box.Width = 220
        self.mode_box.Items.Add(u"只画辅助线")
        self.mode_box.Items.Add(u"只生成实体楼梯")
        self.mode_box.Items.Add(u"两者都做")
        self.mode_box.SelectedIndex = 2

        self.preview = TextBlock()
        self.preview.TextWrapping = TextWrapping.Wrap
        self.preview.Margin = Thickness(0, 8, 0, 6)
        self.preview.Height = 200

        self.status = TextBlock()
        self.status.Foreground = Brushes.Gray
        self.status.TextWrapping = TextWrapping.Wrap

        root.Children.Add(self._section(u"① 形式与标高", row([
            label(u"楼梯形式", 80), self.form_box])))
        root.Children.Add(self._section(u"② 尺寸（mm）", self._grid()))
        root.Children.Add(self._section(u"③ 做法", row([
            label(u"执行方式", 80), self.mode_box, self.draw_steps])))

        action = row([
            button(u"拾取起点", 100),
            button(u"预览", 80),
            button(u"执行", 90),
            button(u"取消", 80),
        ])
        action.Children[0].Click += self._on_pick
        action.Children[1].Click += self._on_preview
        action.Children[2].Click += self._on_go
        action.Children[3].Click += self._on_cancel
        action.Margin = Thickness(0, 10, 0, 4)

        root.Children.Add(self._section(u"④ 结果", self.preview))
        root.Children.Add(self.status)
        root.Children.Add(action)

        self._on_preview(None, None)

    def _section(self, title, content):
        panel = StackPanel()
        heading = TextBlock()
        heading.Text = title
        heading.FontWeight = FontWeights.Bold
        heading.Margin = Thickness(0, 8, 0, 4)
        panel.Children.Add(heading)
        panel.Children.Add(content)
        return panel

    def _grid(self):
        panel = StackPanel()
        panel.Children.Add(row([label(u"基准标高", 80), self.level_box]))
        panel.Children.Add(row([label(u"层高", 80), self.height_box,
                                label(u"踏步高", 70), self.riser_box]))
        panel.Children.Add(row([label(u"踏面宽", 80), self.tread_box,
                                label(u"梯段宽", 70), self.width_box]))
        panel.Children.Add(row([label(u"平台深", 80), self.landing_box,
                                label(u"梯井", 70), self.gap_box]))
        panel.Children.Add(row([label(u"旋转角 °", 80), self.rotate_box,
                                label(u"起点", 40), self.origin_text]))
        return panel

    def _collect(self):
        params = {
            "form": self.form_box.SelectedIndex + 1,
            "height": to_float(self.height_box.Text, 3000.0),
            "riser": to_float(self.riser_box.Text, 150.0),
            "tread": to_float(self.tread_box.Text, 300.0),
            "width": to_float(self.width_box.Text, 1200.0),
            "landing": to_float(self.landing_box.Text, 1200.0),
            "gap": to_float(self.gap_box.Text, 100.0),
            "rotate": to_float(self.rotate_box.Text, 0.0),
            "origin": self.origin if self.origin is not None else XYZ(0, 0, 0),
        }
        index = self.level_box.SelectedIndex
        if index < 0 or index >= len(self.levels):
            raise Exception(u"请选择一个基准标高。")
        params["level"] = self.levels[index]
        params["mode"] = self.mode_box.SelectedIndex
        params["draw_steps"] = bool(self.draw_steps.IsChecked)
        return params

    def _update_preview(self, params, plan):
        self.preview.Text = describe(plan)
        base = params["level"]
        top = None
        for level in self.levels:
            if level.Elevation > base.Elevation + 0.01:
                top = level
                break
        if top is None:
            self.preview.Text += u"\n\n· 没有找到更高的标高，实体楼梯需要上方标高。"
        else:
            self.preview.Text += u"\n上方标高：{0}（{1:.3f} m）".format(
                top.Name, top.Elevation * FT_TO_MM / 1000.0)
        self.preview.Text += u"\n\n执行方式：{0}".format(
            [u"只画辅助线", u"只生成实体楼梯", u"两者都做"][params["mode"]])

    def _on_preview(self, sender, args):
        try:
            params = self._collect()
            plan = compute(params)
        except Exception as ex:
            self.preview.Text = u"计算出错：{0}".format(ex)
            return
        self._update_preview(params, plan)

    def _on_pick(self, sender, args):
        try:
            self._collect()
        except Exception as ex:
            forms.alert(u"{0}".format(ex), title=u"楼梯助手", warn_icon=True)
            return
        self.result = ("pick", None)
        self.Close()

    def _on_go(self, sender, args):
        try:
            params = self._collect()
            plan = compute(params)
        except Exception as ex:
            forms.alert(u"计算出错：\n{0}".format(ex), title=u"楼梯助手", warn_icon=True)
            return
        self.result = ("go", plan, params)
        self.Close()

    def _on_cancel(self, sender, args):
        self.result = None
        self.Close()


def run_plan(plan, params):
    mode = params["mode"]
    base_level = params["level"]
    top_level = None
    for level in all_levels():
        if level.Elevation > base_level.Elevation + 0.01:
            top_level = level
            break

    messages = []
    if mode in (0, 2):
        count = draw_lines(plan, base_level.Elevation, params["draw_steps"])
        messages.append(u"已画 {0} 条模型线".format(count))
    if mode in (1, 2):
        if plan["form"] == 3:
            messages.append(u"三跑暂不支持自动生成实体，请用辅助线手动建（Revit 自带楼梯）。")
        elif top_level is None:
            messages.append(u"没有更高的标高，跳过实体生成。")
        else:
            if plan["form"] == 4:
                created = make_outdoor_steps(plan, base_level)
                messages.append(u"已生成 {0} 级室外台阶".format(len(created)))
            else:
                stairs_id = make_stairs(plan, base_level, top_level)
                uidoc.Selection.SetElementIds(DotNetList[ElementId]([stairs_id]))
                messages.append(u"已生成实体楼梯（Id {0}）".format(stairs_id.IntegerValue))
    return messages


def run_window():
    origin = None
    while True:
        levels = all_levels()
        if not levels:
            forms.alert(u"项目里没有标高。", title=u"楼梯助手", warn_icon=True)
            return
        window = StairWindow(levels, origin)
        window.ShowDialog()
        if not window.result:
            return
        if window.result[0] == "pick":
            try:
                origin = uidoc.Selection.PickPoint(u"点一下楼梯起点（平面上的位置）")
            except Exception:
                origin = None
            continue
        plan = window.result[1]
        params = window.result[2]
        try:
            messages = run_plan(plan, params)
        except Exception as ex:
            forms.alert(u"执行失败：\n{0}\n\n日志：{1}".format(ex, LOG_PATH),
                        title=u"楼梯助手", warn_icon=True)
            return
        forms.alert(u"\n".join(messages) + u"\n\n" + describe(plan) +
                    u"\n\n改错了可以 Ctrl+Z 整体撤销。",
                    title=u"楼梯助手")
        return


run_window()
