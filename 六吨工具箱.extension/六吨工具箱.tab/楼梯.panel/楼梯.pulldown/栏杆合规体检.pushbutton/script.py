# -*- coding: utf-8 -*-
"""栏杆合规体检

对照建筑设计说明 7.3 / 7.4 检查项目里的栏杆（Railing）：

  * 扶手高度：主扶手高度（自踏步前缘 / 完成面量起）不小于下限（默认 1100 mm）；
  * 杆件净距：按栏杆类型的立柱布置估算净距（中心距 − 杆宽），不大于上限（默认 110 mm）；
  * 底部空隙：立柱底部偏移大于 100 mm 视为「离地 0.10 m 内留空」；
  * 水平段：路径里有长度大于 500 mm 的水平段时单独列出（该段高度同样按下限卡）。

说明：
  1. kN 水平荷载模型里查不了，报告里只做提示；
  2. 净距是按类型参数「立柱布置」估算的，不是从几何量出来的，
     现场仍要实测；估算值会在表里标「估」。

输出：结果窗口（可导出 CSV、可选中不合格的栏杆）。
"""

import math
import os

from pyrevit import forms, revit

from Autodesk.Revit.DB import (
    BuiltInCategory,
    FilteredElementCollector,
)
try:
    from Autodesk.Revit.DB.Architecture import Railing, RailingType
except Exception:
    Railing = None
    RailingType = None
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
def all_railings():
    result = []
    for element in FilteredElementCollector(doc).OfCategory(
            BuiltInCategory.OST_StairsRailing).WhereElementIsNotElementType():
        try:
            if Railing is None or isinstance(element, Railing):
                result.append(element)
        except Exception:
            continue
    return result


def railing_type(railing):
    try:
        rtype = doc.GetElement(railing.GetTypeId())
        if RailingType is None or isinstance(rtype, RailingType):
            return rtype
    except Exception:
        pass
    return None


def host_info(railing):
    try:
        if not railing.HasHost:
            return u"未附着"
        host = doc.GetElement(railing.HostId)
        if host is None:
            return u"未附着"
        name = host.Name
        try:
            if host.Category is not None:
                name = u"{0}-{1}".format(host.Category.Name, host.Name)
        except Exception:
            pass
        return name
    except Exception:
        return u"未附着"


def path_stats(railing):
    """返回 (总长, 水平段列表[(长, 起点Z, 终点Z)])，单位 mm。"""
    length = 0.0
    horizontals = []
    try:
        curves = railing.GetPath()
    except Exception:
        return 0.0, []
    for curve in curves:
        try:
            p1 = curve.GetEndPoint(0)
            p2 = curve.GetEndPoint(1)
        except Exception:
            continue
        seg = math.hypot(p2.X - p1.X, p2.Y - p1.Y) * FT_TO_MM
        length += seg
        dz = abs(p2.Z - p1.Z) * FT_TO_MM
        if dz < 50.0 and seg > 500.0:
            horizontals.append((seg, p1.Z * FT_TO_MM, p2.Z * FT_TO_MM))
    return length, horizontals


def baluster_stats(rtype):
    """按类型参数估算：(最小净距mm, 最小底部空隙mm, 立柱数)。取不到返回 (None, None, 0)。"""
    if rtype is None:
        return None, None, 0
    try:
        placement = rtype.BalusterPlacement
        pattern = placement.BalusterPattern
    except Exception:
        return None, None, 0

    count = 0
    try:
        count = int(pattern.GetBalusterCount)
    except Exception:
        try:
            count = int(pattern.GetBalusterCount())
        except Exception:
            count = 0
    if count <= 0:
        return None, None, 0

    min_gap = None
    min_base = None
    for index in range(count):
        try:
            info = pattern.GetBaluster(index)
            distance = info.DistanceFromPreviousOrSpace * FT_TO_MM
            base = info.BaseOffset * FT_TO_MM
        except Exception:
            continue
        if distance and distance > 0:
            if min_gap is None or distance < min_gap:
                min_gap = distance
        if min_base is None or base < min_base:
            min_base = base
    return min_gap, min_base, count


def check_railing(railing, params):
    rtype = railing_type(railing)
    type_name = rtype.Name if rtype is not None else u"<无类型>"

    hand_mm = None
    top_mm = None
    if rtype is not None:
        try:
            hand_mm = rtype.PrimaryHandrailHeight * FT_TO_MM
        except Exception:
            hand_mm = None
        try:
            top_mm = rtype.TopRailHeight * FT_TO_MM
        except Exception:
            top_mm = None

    height_mm = hand_mm
    if (height_mm is None or height_mm <= 1.0) and top_mm:
        height_mm = top_mm

    length_mm, horizontals = path_stats(railing)
    gap_mm, base_mm, balusters = baluster_stats(rtype)

    problems = []
    if params["check_height"] and height_mm is not None:
        if height_mm + 0.5 < params["min_height"]:
            problems.append(u"高度 {0:.0f} < {1:.0f}".format(
                height_mm, params["min_height"]))
    if params["check_gap"] and gap_mm is not None:
        net = gap_mm - params["bar_width"]
        if net > params["max_gap"] + 0.5:
            problems.append(u"净距约 {0:.0f} > {1:.0f}".format(net, params["max_gap"]))
    if params["check_base"] and base_mm is not None:
        if base_mm > 100.0 + 0.5:
            problems.append(u"底部空隙 {0:.0f} > 100".format(base_mm))
    if params["check_level"] and horizontals:
        longest = max(h[0] for h in horizontals)
        if longest > 500.0:
            problems.append(u"水平段 {0:.0f}（按 1.10 m 控制）".format(longest))

    return {
        "id": railing.Id.IntegerValue,
        "type": type_name,
        "host": host_info(railing),
        "length": length_mm,
        "height": height_mm,
        "gap": gap_mm,
        "base": base_mm,
        "balusters": balusters,
        "horizontal": max([h[0] for h in horizontals]) if horizontals else 0.0,
        "problems": problems,
    }


def scan(railings, params):
    rows = []
    for railing in railings:
        try:
            rows.append(check_railing(railing, params))
        except Exception as ex:
            rows.append({
                "id": railing.Id.IntegerValue,
                "type": u"<读取失败>",
                "host": u"",
                "length": 0.0,
                "height": None,
                "gap": None,
                "base": None,
                "balusters": 0,
                "horizontal": 0.0,
                "problems": [u"{0}".format(ex)],
            })
    return rows


def fmt(value):
    if value is None:
        return u"—"
    return u"{0:.0f}".format(value)


def report(rows, params):
    total = len(rows)
    bad = [r for r in rows if r["problems"]]
    lines = [
        u"共检查栏杆 {0} 个，不合格 / 需复核 {1} 个".format(total, len(bad)),
        u"控制值：扶手高度 ≥ {0:.0f} mm，杆件净距 ≤ {1:.0f} mm（杆宽按 {2:.0f} mm 估）".format(
            params["min_height"], params["max_gap"], params["bar_width"]),
        u"",
    ]
    for row in rows:
        flag = u"✗ " + u"；".join(row["problems"]) if row["problems"] else u"✓"
        lines.append(
            u"[{0}] {1}｜{2}｜高 {3}｜净距 {4}｜底缝 {5}｜水平段 {6}｜{7}".format(
                row["id"], row["type"], row["host"], fmt(row["height"]),
                fmt(row["gap"]), fmt(row["base"]), fmt(row["horizontal"]), flag))
    if not bad:
        lines.append(u"")
        lines.append(u"没有发现不合格项。")
    lines.append(u"")
    lines.append(u"提示：1 kN 水平荷载、封样等条款属于产品 / 施工资料，模型里无法核验。")
    return u"\n".join(lines)


def export_csv(rows, params):
    desk = Environment.GetFolderPath(Environment.SpecialFolder.Desktop)
    if not desk or not os.path.isdir(desk):
        desk = os.path.expanduser(u"~")
    path = os.path.join(desk, u"栏杆合规体检.csv")
    lines = [u"ElementId,类型,附着,路径长mm,扶手高度mm,立柱中心距mm,底部空隙mm,立柱数,水平段mm,结论"]
    for row in rows:
        lines.append(u",".join([
            u"{0}".format(row["id"]),
            u"\"{0}\"".format(row["type"]),
            u"\"{0}\"".format(row["host"]),
            fmt(row["length"]), fmt(row["height"]), fmt(row["gap"]),
            fmt(row["base"]), u"{0}".format(row["balusters"]),
            fmt(row["horizontal"]),
            u"\"{0}\"".format(u"；".join(row["problems"]) or u"合格"),
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


def row(label_text, controls, label_width=130):
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
        Title="栏杆合规体检" Height="440" Width="540"
        WindowStartupLocation="CenterScreen" ResizeMode="CanResize">
  <ScrollViewer VerticalScrollBarVisibility="Auto">
    <StackPanel Margin="14" />
  </ScrollViewer>
</Window>
""", literal_string=True)
        self.result = None
        root = self.Content.Content

        self.height_box = text_box(u"1100")
        self.gap_box = text_box(u"110")
        self.width_box = text_box(u"40")
        self.scope = ComboBox()
        self.scope.Items.Add(u"全部栏杆")
        self.scope.Items.Add(u"选中的栏杆")
        self.scope.SelectedIndex = 0
        self.scope.Width = 160

        checks = StackPanel()
        self.check_height = CheckBox()
        self.check_height.Content = u"检查扶手高度"
        self.check_height.IsChecked = True
        checks.Children.Add(self.check_height)
        self.check_gap = CheckBox()
        self.check_gap.Content = u"检查杆件净距（按类型估算）"
        self.check_gap.IsChecked = True
        checks.Children.Add(self.check_gap)
        self.check_base = CheckBox()
        self.check_base.Content = u"检查底部 100 mm 空隙"
        self.check_base.IsChecked = True
        checks.Children.Add(self.check_base)
        self.check_level = CheckBox()
        self.check_level.Content = u"标出长度 > 500 mm 的水平段"
        self.check_level.IsChecked = True
        checks.Children.Add(self.check_level)

        params = StackPanel()
        params.Children.Add(row(u"扶手最小高度 (mm)", [self.height_box]))
        params.Children.Add(row(u"杆件净距上限 (mm)", [self.gap_box]))
        params.Children.Add(row(u"立柱杆宽估算 (mm)", [self.width_box]))
        params.Children.Add(row(u"检查范围", [self.scope]))

        root.Children.Add(section(u"① 控制值", params))
        root.Children.Add(section(u"② 检查项", checks))

        hint = TextBlock()
        hint.Text = (u"依据：设计说明 7.3 —— 扶手高度自踏步前缘量起不小于 1.10 m；"
                     u"垂直杆件净距不大于 0.11 m；离地 0.10 m 高度内不能留空。"
                     u"净距与底部空隙取栏杆类型里的立柱布置参数，属估算值。")
        hint.TextWrapping = TextWrapping.Wrap
        hint.Foreground = Brushes.Gray
        hint.Margin = Thickness(0, 8, 0, 4)
        root.Children.Add(hint)

        buttons = StackPanel()
        buttons.Orientation = Orientation.Horizontal
        buttons.HorizontalAlignment = HorizontalAlignment.Right
        ok = button(u"开始扫描", 100)
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
            "min_height": num(self.height_box, 1100.0),
            "max_gap": num(self.gap_box, 110.0),
            "bar_width": num(self.width_box, 40.0),
            "check_height": bool(self.check_height.IsChecked),
            "check_gap": bool(self.check_gap.IsChecked),
            "check_base": bool(self.check_base.IsChecked),
            "check_level": bool(self.check_level.IsChecked),
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
        Title="栏杆合规体检 - 结果" Height="620" Width="900"
        WindowStartupLocation="CenterScreen" ResizeMode="CanResize">
  <DockPanel Margin="10">
    <StackPanel DockPanel.Dock="Bottom" Orientation="Horizontal"
                HorizontalAlignment="Right" Margin="0,8,0,0" />
  </DockPanel>
</Window>
""", literal_string=True)
        self.rows = rows
        self.params = params
        dock = self.Content

        header = TextBlock()
        header.Text = u"不合格 / 需复核 {0} 个 / 共 {1} 个".format(
            len([r for r in rows if r["problems"]]), len(rows))
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
        select = button(u"选中不合格", 110)
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
            forms.alert(u"已导出：\n{0}".format(path), title=u"栏杆合规体检")
        except Exception as ex:
            forms.alert(u"导出失败：{0}".format(ex), title=u"栏杆合规体检", warn_icon=True)

    def _on_select(self, sender, args):
        bad = [r["id"] for r in self.rows if r["problems"]]
        if not bad:
            forms.alert(u"没有不合格项。", title=u"栏杆合规体检")
            return
        try:
            from System.Collections.Generic import List as DotNetList
            from Autodesk.Revit.DB import ElementId
            ids = DotNetList[ElementId]()
            for value in bad:
                ids.Add(ElementId(value))
            uidoc.Selection.SetElementIds(ids)
            forms.alert(u"已在模型中选中 {0} 个不合格栏杆。".format(len(bad)),
                        title=u"栏杆合规体检")
        except Exception as ex:
            forms.alert(u"选中失败：{0}".format(ex), title=u"栏杆合规体检", warn_icon=True)

    def _on_close(self, sender, args):
        self.Close()


# ---------------------------------------------------------------- 入口
def main():
    options = OptionsWindow()
    options.ShowDialog()
    params = options.result
    if not params:
        return

    railings = all_railings()
    if params["scope"] == 1:
        chosen = []
        try:
            for element_id in uidoc.Selection.GetElementIds():
                element = doc.GetElement(element_id)
                if Railing is None or isinstance(element, Railing):
                    chosen.append(element)
        except Exception:
            chosen = []
        if chosen:
            railings = chosen

    if not railings:
        forms.alert(u"项目里没有找到栏杆（Railing）。", title=u"栏杆合规体检", warn_icon=True)
        return

    rows = scan(railings, params)
    window = ResultWindow(rows, params)
    window.ShowDialog()


main()
