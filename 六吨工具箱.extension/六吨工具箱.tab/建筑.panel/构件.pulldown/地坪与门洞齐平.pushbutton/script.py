# -*- coding: utf-8 -*-
"""地坪与门洞齐平

对照建筑设计说明 8.2：「楼地面构造交接处和地坪高度变化处，除图中另有注明者外均位于齐平门扇开启面处」。

做法：逐扇门，在门洞两侧（沿墙法向、墙厚以外 200 mm）各取一个采样点，
找该点正下方楼板的顶面标高，比较两侧高差；超过容差即列为「不齐平」。

输出：结果窗口 + CSV（ElementId、门类型、两侧标高、高差、结论）+ 选中问题门。
"""

import os

from pyrevit import forms, revit

from Autodesk.Revit.DB import (
    BuiltInCategory,
    BuiltInParameter,
    FilteredElementCollector,
    LocationCurve,
    Wall,
    XYZ,
)
try:
    from Autodesk.Revit.DB.Architecture import Door
except Exception:
    Door = None
from System import Environment
from System.Windows import HorizontalAlignment, TextWrapping, Thickness, FontWeights, VerticalAlignment
from System.Windows.Controls import (
    Button,
    CheckBox,
    ComboBox,
    Dock,
    DockPanel,
    Orientation,
    ScrollBarVisibility,
    ScrollViewer,
    StackPanel,
    TextBlock,
    TextBox,
)
from System.Windows.Media import Brushes, FontFamily

doc = revit.doc
uidoc = revit.uidoc

FT_TO_MM = 304.8
MM_TO_FT = 1.0 / FT_TO_MM


# ---------------------------------------------------------------- 数据
def all_doors():
    result = []
    for element in FilteredElementCollector(doc).OfCategory(
            BuiltInCategory.OST_Doors).WhereElementIsNotElementType():
        try:
            if Door is None or isinstance(element, Door):
                result.append(element)
        except Exception:
            continue
    return result


def all_floors():
    return list(FilteredElementCollector(doc).OfCategory(
        BuiltInCategory.OST_Floors).WhereElementIsNotElementType())


def floor_top_at(floors, x, y, near_z):
    """返回 (顶面 Z, 楼板) —— 取包含该点、且顶面最接近 near_z 的楼板。"""
    best = None
    best_delta = None
    for floor in floors:
        try:
            box = floor.get_BoundingBox(None)
        except Exception:
            continue
        if box is None:
            continue
        if x < box.Min.X or x > box.Max.X or y < box.Min.Y or y > box.Max.Y:
            continue
        delta = abs(box.Max.Z - near_z)
        if best_delta is None or delta < best_delta:
            best_delta = delta
            best = (box.Max.Z, floor)
    return best


def door_width(doo):
    try:
        param = doo.get_Parameter(BuiltInParameter.DOOR_WIDTH)
        if param is not None and param.AsDouble() > 0:
            return param.AsDouble()
    except Exception:
        pass
    try:
        box = doo.get_BoundingBox(None)
        if box is not None:
            return min(box.Max.X - box.Min.X, box.Max.Y - box.Min.Y)
    except Exception:
        pass
    return 900.0 * MM_TO_FT


def wall_normal(doo):
    try:
        angle = doo.FacingOrientation
        if angle is not None and (abs(angle.X) + abs(angle.Y)) > 1e-9:
            return XYZ(angle.X, angle.Y, 0).Normalize()
    except Exception:
        pass
    try:
        host = doo.Host
        if isinstance(host, Wall):
            curve = host.Location.Curve
            p1 = curve.GetEndPoint(0)
            p2 = curve.GetEndPoint(1)
            dx = p2.X - p1.X
            dy = p2.Y - p1.Y
            length = (dx * dx + dy * dy) ** 0.5
            if length > 1e-9:
                return XYZ(-dy / length, dx / length, 0)
    except Exception:
        pass
    return XYZ(0, 1, 0)


def wall_width(doo):
    try:
        host = doo.Host
        if isinstance(host, Wall):
            return host.Width
    except Exception:
        pass
    return 200.0 * MM_TO_FT


def level_z(doo):
    try:
        level = doc.GetElement(doo.LevelId)
        if level is not None:
            return level.Elevation
    except Exception:
        pass
    try:
        return doo.Location.Point.Z
    except Exception:
        return 0.0


def check_door(doo, floors, params):
    loc = None
    try:
        loc = doo.Location.Point
    except Exception:
        loc = None

    width = door_width(doo)
    normal = wall_normal(doo)
    offset = wall_width(doo) / 2.0 + params["sample"] * MM_TO_FT
    base_z = level_z(doo)

    if loc is None:
        return {
            "id": doo.Id.IntegerValue,
            "type": doo.Name,
            "left": None, "right": None, "delta": None,
            "problem": u"取不到门的位置",
        }

    tops = []
    for side in (1.0, -1.0):
        x = loc.X + normal.X * offset * side
        y = loc.Y + normal.Y * offset * side
        hit = floor_top_at(floors, x, y, base_z)
        tops.append(hit)

    if tops[0] is None or tops[1] is None:
        missing = u"左侧" if tops[0] is None else u"右侧"
        if tops[0] is None and tops[1] is None:
            missing = u"两侧"
        return {
            "id": doo.Id.IntegerValue,
            "type": doo.Name,
            "left": tops[0][0] * FT_TO_MM if tops[0] else None,
            "right": tops[1][0] * FT_TO_MM if tops[1] else None,
            "delta": None,
            "problem": u"{0}未找到楼板".format(missing),
        }

    left = tops[0][0] * FT_TO_MM
    right = tops[1][0] * FT_TO_MM
    delta = left - right
    problem = u""
    if abs(delta) > params["tol"] + 0.01:
        problem = u"高差 {0:.1f} mm".format(delta)
    return {
        "id": doo.Id.IntegerValue,
        "type": doo.Name,
        "left": left,
        "right": right,
        "delta": delta,
        "problem": problem,
    }


def fmt(value):
    if value is None:
        return u"—"
    return u"{0:.1f}".format(value)


def report(rows, params):
    bad = [r for r in rows if r["problem"]]
    lines = [
        u"共检查门 {0} 扇，不齐平 / 需复核 {1} 扇".format(len(rows), len(bad)),
        u"容差 {0:.1f} mm，采样点距墙面 {1:.0f} mm".format(params["tol"], params["sample"]),
        u"",
    ]
    for row in rows:
        flag = u"✗ " + row["problem"] if row["problem"] else u"✓"
        lines.append(u"[{0}] {1}｜左 {2}｜右 {3}｜高差 {4}｜{5}".format(
            row["id"], row["type"], fmt(row["left"]), fmt(row["right"]),
            fmt(row["delta"]), flag))
    if not bad:
        lines.append(u"")
        lines.append(u"没有发现不齐平的门洞。")
    lines.append(u"")
    lines.append(u"说明：标高取采样点正下方楼板的包围盒顶面，适用于水平楼板；坡道、降板、无楼板处会提示「未找到楼板」，需人工确认。")
    return u"\n".join(lines)


def export_csv(rows, params):
    desk = Environment.GetFolderPath(Environment.SpecialFolder.Desktop)
    if not desk or not os.path.isdir(desk):
        desk = os.path.expanduser(u"~")
    path = os.path.join(desk, u"地坪与门洞齐平.csv")
    lines = [u"ElementId,门类型,左侧标高mm,右侧标高mm,高差mm,结论"]
    for row in rows:
        lines.append(u",".join([
            u"{0}".format(row["id"]),
            u"\"{0}\"".format(row["type"]),
            fmt(row["left"]), fmt(row["right"]), fmt(row["delta"]),
            u"\"{0}\"".format(row["problem"] or u"齐平"),
        ]))
    with open(path, "wb") as handle:
        handle.write(u"\r\n".join(lines).encode("utf-8-sig"))
    return path


# ---------------------------------------------------------------- 界面
def label(text, width=0):
    block = TextBlock()
    block.Text = text
    block.VerticalAlignment = VerticalAlignment.Center
    block.Margin = Thickness(0, 0, 6, 0)
    if width:
        block.Width = width
    return block


def text_box(text, width=90):
    box = TextBox()
    box.Text = text
    box.Width = width
    box.Margin = Thickness(0, 2, 6, 2)
    return box


def row(label_text, controls, label_width=150):
    panel = StackPanel()
    panel.Orientation = Orientation.Horizontal
    panel.Margin = Thickness(0, 3, 0, 3)
    panel.Children.Add(label(label_text, label_width))
    for control in controls:
        panel.Children.Add(control)
    return panel


def section(title, content):
    panel = StackPanel()
    heading = TextBlock()
    heading.Text = title
    heading.FontWeight = FontWeights.Bold
    heading.Margin = Thickness(0, 10, 0, 4)
    panel.Children.Add(heading)
    panel.Children.Add(content)
    return panel


def button(text, width=90):
    control = Button()
    control.Content = text
    control.Width = width
    control.Margin = Thickness(0, 0, 8, 0)
    control.Padding = Thickness(6, 4, 6, 4)
    return control


class OptionsWindow(forms.WPFWindow):
    def __init__(self):
        forms.WPFWindow.__init__(self, """
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        Title="地坪与门洞齐平" Height="400" Width="520"
        WindowStartupLocation="CenterScreen" ResizeMode="CanResize">
  <ScrollViewer VerticalScrollBarVisibility="Auto">
    <StackPanel Margin="14" />
  </ScrollViewer>
</Window>
""", literal_string=True)
        self.result = None
        root = self.Content.Content

        self.tol_box = text_box(u"5")
        self.sample_box = text_box(u"200")
        self.scope = ComboBox()
        self.scope.Items.Add(u"全部门")
        self.scope.Items.Add(u"选中的门")
        self.scope.SelectedIndex = 0
        self.scope.Width = 160

        params = StackPanel()
        params.Children.Add(row(u"允许高差 (mm)", [self.tol_box]))
        params.Children.Add(row(u"采样点距墙面 (mm)", [self.sample_box]))
        params.Children.Add(row(u"检查范围", [self.scope]))

        root.Children.Add(section(u"① 参数", params))

        hint = TextBlock()
        hint.Text = (u"依据设计说明 8.2：楼地面构造交接处和地坪高度变化处，"
                     u"除图中另有注明者外均位于齐平门扇开启面处。\n"
                     u"做法：在门洞两侧沿墙法向、墙厚以外取点，比较正下方楼板顶标高。")
        hint.TextWrapping = TextWrapping.Wrap
        hint.Foreground = Brushes.Gray
        hint.Margin = Thickness(0, 8, 0, 4)
        root.Children.Add(hint)

        buttons = StackPanel()
        buttons.Orientation = Orientation.Horizontal
        buttons.HorizontalAlignment = HorizontalAlignment.Right
        ok = button(u"开始检查", 100)
        ok.Click += self._on_ok
        cancel = button(u"取消", 80)
        cancel.Click += self._on_cancel
        buttons.Children.Add(ok)
        buttons.Children.Add(cancel)
        root.Children.Add(buttons)

    def _collect(self):
        def num(box, default):
            try:
                return float(u"{0}".format(box.Text).strip())
            except Exception:
                return default
        return {
            "tol": num(self.tol_box, 5.0),
            "sample": num(self.sample_box, 200.0),
            "scope": self.scope.SelectedIndex,
        }

    def _on_ok(self, sender, args):
        self.result = self._collect()
        self.Close()

    def _on_cancel(self, sender, args):
        self.result = None
        self.Close()


class ResultWindow(forms.WPFWindow):
    def __init__(self, rows, params):
        forms.WPFWindow.__init__(self, """
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        Title="地坪与门洞齐平 - 结果" Height="600" Width="880"
        WindowStartupLocation="CenterScreen" ResizeMode="CanResize">
  <DockPanel Margin="10" />
</Window>
""", literal_string=True)
        self.rows = rows
        self.params = params
        dock = self.Content

        header = TextBlock()
        header.Text = u"不齐平 / 需复核 {0} 扇 / 共 {1} 扇".format(
            len([r for r in rows if r["problem"]]), len(rows))
        header.FontWeight = FontWeights.Bold
        header.Margin = Thickness(0, 0, 0, 6)
        DockPanel.SetDock(header, Dock.Top)
        dock.Children.Add(header)

        buttons = StackPanel()
        buttons.Orientation = Orientation.Horizontal
        buttons.HorizontalAlignment = HorizontalAlignment.Right
        buttons.Margin = Thickness(0, 8, 0, 0)
        export = button(u"导出 CSV", 100)
        export.Click += self._on_export
        select = button(u"选中问题门", 110)
        select.Click += self._on_select
        close = button(u"关闭", 80)
        close.Click += self._on_close
        buttons.Children.Add(export)
        buttons.Children.Add(select)
        buttons.Children.Add(close)
        DockPanel.SetDock(buttons, Dock.Bottom)
        dock.Children.Add(buttons)

        box = TextBox()
        box.IsReadOnly = True
        box.AcceptsReturn = True
        box.TextWrapping = TextWrapping.NoWrap
        box.VerticalScrollBarVisibility = ScrollBarVisibility.Auto
        box.HorizontalScrollBarVisibility = ScrollBarVisibility.Auto
        box.FontFamily = FontFamily(u"Consolas")
        box.Text = report(rows, params)
        dock.Children.Add(box)

    def _on_export(self, sender, args):
        try:
            path = export_csv(self.rows, self.params)
            forms.alert(u"已导出：\n{0}".format(path), title=u"地坪与门洞齐平")
        except Exception as ex:
            forms.alert(u"导出失败：{0}".format(ex), title=u"地坪与门洞齐平", warn_icon=True)

    def _on_select(self, sender, args):
        bad = [r["id"] for r in self.rows if r["problem"]]
        if not bad:
            forms.alert(u"没有发现不齐平的门洞。", title=u"地坪与门洞齐平")
            return
        try:
            from System.Collections.Generic import List as DotNetList
            from Autodesk.Revit.DB import ElementId
            ids = DotNetList[ElementId]()
            for value in bad:
                ids.Add(ElementId(value))
            uidoc.Selection.SetElementIds(ids)
            forms.alert(u"已在模型中选中 {0} 扇门。".format(len(bad)),
                        title=u"地坪与门洞齐平")
        except Exception as ex:
            forms.alert(u"选中失败：{0}".format(ex), title=u"地坪与门洞齐平", warn_icon=True)

    def _on_close(self, sender, args):
        self.Close()


# ---------------------------------------------------------------- 入口
def main():
    options = OptionsWindow()
    options.ShowDialog()
    params = options.result
    if not params:
        return

    doors = all_doors()
    if params["scope"] == 1:
        chosen = []
        try:
            for element_id in uidoc.Selection.GetElementIds():
                element = doc.GetElement(element_id)
                if Door is None or isinstance(element, Door):
                    chosen.append(element)
        except Exception:
            chosen = []
        if chosen:
            doors = chosen

    if not doors:
        forms.alert(u"项目里没有找到门（Door）。", title=u"地坪与门洞齐平", warn_icon=True)
        return

    floors = all_floors()
    if not floors:
        forms.alert(u"项目里没有楼板，无法比较地坪标高。", title=u"地坪与门洞齐平", warn_icon=True)
        return

    rows = []
    for doo in doors:
        try:
            rows.append(check_door(doo, floors, params))
        except Exception as ex:
            rows.append({
                "id": doo.Id.IntegerValue, "type": doo.Name,
                "left": None, "right": None, "delta": None,
                "problem": u"{0}".format(ex),
            })

    window = ResultWindow(rows, params)
    window.ShowDialog()


main()
