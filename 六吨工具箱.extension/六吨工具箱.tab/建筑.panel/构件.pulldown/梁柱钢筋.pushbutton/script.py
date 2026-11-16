# -*- coding: utf-8 -*-
"""梁柱钢筋一键生成（带启动日志）"""

from __future__ import print_function

import math
import time
import traceback

LOG_PATH = r"C:\Users\cyl\Documents\deepseek-harness\default-workspace\_钢筋日志.txt"


def log_line(text):
    try:
        f = open(LOG_PATH, "ab")
        f.write((u"%s %s\n" % (time.strftime("%H:%M:%S"), text)).encode("utf-8"))
        f.close()
    except Exception:
        pass


log_line(u"== 模块开始 ==")

from Autodesk.Revit.DB import (
    BuiltInCategory,
    FilteredElementCollector,
    Line,
    Options,
    Transaction,
    XYZ,
)

log_line(u"DB 导入 OK")

try:
    from Autodesk.Revit.DB.Structure import (
        Rebar,
        RebarBarType,
        RebarHookOrientation,
        RebarStyle,
    )
    log_line(u"钢筋类型导入 OK")
except Exception:
    import Autodesk.Revit.DB.Structure as _STRUCT
    Rebar = _STRUCT.Rebar
    RebarBarType = _STRUCT.RebarBarType
    RebarHookOrientation = _STRUCT.RebarHookOrientation
    RebarStyle = _STRUCT.RebarStyle
    log_line(u"钢筋类型导入 走模块属性兜底")

from System.Collections.Generic import List as DotNetList

from pyrevit import revit, forms, script

log_line(u"pyrevit 导入 OK")

doc = revit.doc
uidoc = revit.uidoc
output = script.get_output()

MM_TO_FT = 1.0 / 304.8
FT_TO_MM = 304.8

LOG_PATH = r"C:\Users\cyl\Documents\deepseek-harness\default-workspace\_钢筋日志.txt"


def log_line(text):
    try:
        f = open(LOG_PATH, "ab")
        f.write((u"%s %s\n" % (time.strftime("%H:%M:%S"), text)).encode("utf-8"))
        f.close()
    except Exception:
        pass


def text_of(value):
    if isinstance(value, unicode):
        return value
    try:
        return unicode(value)
    except Exception:
        return u"%s" % value


def name_of(element, default=u""):
    try:
        return text_of(element.Name)
    except Exception:
        return default


# ---------------------------------------------------------------- 类型
def all_bar_types():
    types = []
    for element in FilteredElementCollector(doc).OfClass(RebarBarType):
        types.append(element)
    types.sort(key=lambda t: num_of(name_of(t)))
    return types


def num_of(text):
    digits = u""
    for ch in text:
        if ch.isdigit() or ch == u".":
            digits += ch
        elif digits:
            break
    try:
        return float(digits)
    except Exception:
        return 0.0


def pick_bar_type(diameter_mm):
    """按直径挑一个 RebarBarType：优先名字含直径数字且最接近的。"""
    best = None
    best_delta = None
    for bar_type in all_bar_types():
        try:
            dia = bar_type.BarDiameter * FT_TO_MM
        except Exception:
            continue
        delta = abs(dia - diameter_mm)
        if dia >= 4.0 and (best_delta is None or delta < best_delta):
            best = bar_type
            best_delta = delta
    return best


# ---------------------------------------------------------------- 几何
def local_frame(host):
    """返回 (origin, x, y, z, 截面范围)。x 为构件主轴。"""
    from Autodesk.Revit.DB import LocationCurve

    location = host.Location
    curve = None
    try:
        if isinstance(location, LocationCurve):
            curve = location.Curve
    except Exception:
        curve = None

    if curve is not None:
        p0 = curve.GetEndPoint(0)
        p1 = curve.GetEndPoint(1)
        direction = (p1 - p0).Normalize()
    else:
        p0 = XYZ(0, 0, 0)
        direction = XYZ(0, 1, 0)

    up = XYZ(0, 0, 1)
    if abs(direction.DotProduct(up)) > 0.9:
        # 竖向构件（柱）：主轴朝上，用 X/Y 做截面轴
        x_axis = XYZ(0, 0, 1)
        y_axis = XYZ(1, 0, 0)
        z_axis = XYZ(0, 1, 0)
    else:
        x_axis = XYZ(direction.X, direction.Y, 0)
        if x_axis.GetLength() < 1e-9:
            x_axis = XYZ(0, 1, 0)
        else:
            x_axis = x_axis.Normalize()
        z_axis = up
        y_axis = z_axis.CrossProduct(x_axis).Normalize()

    origin = p0
    bbox = None
    try:
        bbox = host.get_BoundingBox(None)
    except Exception:
        bbox = None

    if bbox is None:
        return origin, x_axis, y_axis, z_axis, (-1, 1), (-0.15, 0.15), (-0.2, 0.2)

    corners = []
    for xi in (bbox.Min.X, bbox.Max.X):
        for yi in (bbox.Min.Y, bbox.Max.Y):
            for zi in (bbox.Min.Z, bbox.Max.Z):
                corners.append(XYZ(xi, yi, zi))

    xs = []
    ys = []
    zs = []
    for point in corners:
        rel = point - origin
        xs.append(rel.DotProduct(x_axis))
        ys.append(rel.DotProduct(y_axis))
        zs.append(rel.DotProduct(z_axis))

    return origin, x_axis, y_axis, z_axis, (min(xs), max(xs)), (min(ys), max(ys)), (min(zs), max(zs))


def pt(origin, x_axis, y_axis, z_axis, x, y, z):
    return origin + x_axis.Multiply(x) + y_axis.Multiply(y) + z_axis.Multiply(z)


def curve_list(curves):
    result = DotNetList[object]()
    for item in curves:
        result.Add(item)
    return result


def add_rebar(host, curves, normal, style, bar_type, spacing_ft=None, start_ft=0.0, length_ft=0.0):
    result = DotNetList[type(curves[0])]()
    for curve in curves:
        result.Add(curve)
    rebar = Rebar.CreateFromCurves(
        doc, style, bar_type, None, None, host, normal, result,
        RebarHookOrientation.Left, RebarHookOrientation.Left, True, True)
    if style == RebarStyle.StirrupTie and spacing_ft and length_ft > 0:
        accessor = rebar.GetShapeDrivenAccessor()
        accessor.SetLayoutAsMaximumSpacing(spacing_ft, length_ft, True, True, True)
    return rebar


# ---------------------------------------------------------------- 生成
def make_beam_rebar(host, params):
    """梁：上下纵筋 + 矩形箍筋。"""
    origin, x_axis, y_axis, z_axis, (x0, x1), (y0, y1), (z0, z1) = local_frame(host)
    cover = params["cover"] * MM_TO_FT
    dia = params["main_dia"] * MM_TO_FT
    stirrup_dia = params["stirrup_dia"] * MM_TO_FT

    length = x1 - x0
    if length <= 2 * cover:
        return 0, [u"梁太短"]

    # 箍筋范围
    sy0, sy1 = y0 + cover, y1 - cover
    sz0, sz1 = z0 + cover, z1 - cover
    if sy1 - sy0 < 2 * stirrup_dia or sz1 - sz0 < 2 * stirrup_dia:
        return 0, [u"截面太小，放不下箍筋"]

    main_type = pick_bar_type(params["main_dia"])
    stirrup_type = pick_bar_type(params["stirrup_dia"])
    if main_type is None or stirrup_type is None:
        return 0, [u"项目里没有对应直径的钢筋类型（直径 %s / %s）" % (params["main_dia"], params["stirrup_dia"])]

    made = 0
    failures = []

    # 纵筋：上部 / 下部各 n 根，沿 y 均布
    inset = cover + dia * 0.5
    levels = [(z1 - inset, params["top_bars"]), (z0 + inset, params["bottom_bars"])]
    for z_level, count in levels:
        count = max(1, int(count))
        for index in range(count):
            if count == 1:
                y = (sy0 + sy1) * 0.5
            else:
                y = sy0 + (sy1 - sy0) * index / float(count - 1)
                y = min(max(y, y0 + inset), y1 - inset)
            p0 = pt(origin, x_axis, y_axis, z_axis, x0 + cover, y, z_level)
            p1 = pt(origin, x_axis, y_axis, z_axis, x1 - cover, y, z_level)
            try:
                add_rebar(host, [Line.CreateBound(p0, p1)], z_axis, RebarStyle.Standard, main_type)
                made += 1
            except Exception as ex:
                failures.append(u"纵筋失败：%s" % ex)

    # 箍筋：闭合矩形，法向沿梁轴
    corners = [
        pt(origin, x_axis, y_axis, z_axis, x0 + cover, sy0, sz0),
        pt(origin, x_axis, y_axis, z_axis, x0 + cover, sy1, sz0),
        pt(origin, x_axis, y_axis, z_axis, x0 + cover, sy1, sz1),
        pt(origin, x_axis, y_axis, z_axis, x0 + cover, sy0, sz1),
    ]
    edges = []
    for index in range(4):
        a = corners[index]
        b = corners[(index + 1) % 4]
        edges.append(Line.CreateBound(a, b))
    start = params["stirrup_start"] * MM_TO_FT
    array_len = max(0.0, length - 2 * start)
    try:
        add_rebar(host, edges, x_axis, RebarStyle.StirrupTie, stirrup_type,
                  spacing_ft=params["stirrup_spacing"] * MM_TO_FT,
                  length_ft=array_len)
        made += 1
    except Exception as ex:
        failures.append(u"箍筋失败：%s" % ex)

    return made, failures


def make_column_rebar(host, params):
    """柱：四角（或更多）纵筋 + 矩形箍筋。"""
    origin, x_axis, y_axis, z_axis, (x0, x1), (y0, y1), (z0, z1) = local_frame(host)
    cover = params["cover"] * MM_TO_FT
    dia = params["main_dia"] * MM_TO_FT
    stirrup_dia = params["stirrup_dia"] * MM_TO_FT

    height = x1 - x0  # 柱的主轴是 Z
    if height <= 2 * cover:
        return 0, [u"柱太短"]

    main_type = pick_bar_type(params["main_dia"])
    stirrup_type = pick_bar_type(params["stirrup_dia"])
    if main_type is None or stirrup_type is None:
        return 0, [u"项目里没有对应直径的钢筋类型"]

    made = 0
    failures = []

    count = max(4, int(params["column_bars"]))
    if count % 4 != 0:
        count = (count // 4) * 4
    per_side = count // 4 + 1
    inset_y = cover + dia * 0.5
    inset_z = cover + dia * 0.5
    y_positions = [y0 + inset_y, y1 - inset_y]
    z_positions = [z0 + inset_z, z1 - inset_z]
    extra_y = [y0 + (y1 - y0) * i / float(per_side - 1) for i in range(1, per_side - 1)] if per_side > 2 else []
    extra_z = [z0 + (z1 - z0) * i / float(per_side - 1) for i in range(1, per_side - 1)] if per_side > 2 else []

    points = []
    for yy in y_positions:
        for zz in z_positions:
            points.append((yy, zz))
    for yy in extra_y:
        points.append((yy, z0 + inset_z))
        points.append((yy, z1 - inset_z))
    for zz in extra_z:
        points.append((y0 + inset_y, zz))
        points.append((y1 - inset_y, zz))

    for (yy, zz) in points:
        p0 = pt(origin, x_axis, y_axis, z_axis, x0 + cover, yy, zz)
        p1 = pt(origin, x_axis, y_axis, z_axis, x1 - cover, yy, zz)
        try:
            add_rebar(host, [Line.CreateBound(p0, p1)], y_axis, RebarStyle.Standard, main_type)
            made += 1
        except Exception as ex:
            failures.append(u"纵筋失败：%s" % ex)

    sy0, sy1 = y0 + cover, y1 - cover
    sz0, sz1 = z0 + cover, z1 - cover
    base_z = x0 + cover
    corners = [
        pt(origin, x_axis, y_axis, z_axis, base_z, sy0, sz0),
        pt(origin, x_axis, y_axis, z_axis, base_z, sy1, sz0),
        pt(origin, x_axis, y_axis, z_axis, base_z, sy1, sz1),
        pt(origin, x_axis, y_axis, z_axis, base_z, sy0, sz1),
    ]
    edges = []
    for index in range(4):
        edges.append(Line.CreateBound(corners[index], corners[(index + 1) % 4]))
    start = params["stirrup_start"] * MM_TO_FT
    array_len = max(0.0, height - 2 * start)
    try:
        add_rebar(host, edges, x_axis, RebarStyle.StirrupTie, stirrup_type,
                  spacing_ft=params["stirrup_spacing"] * MM_TO_FT,
                  length_ft=array_len)
        made += 1
    except Exception as ex:
        failures.append(u"箍筋失败：%s" % ex)

    return made, failures


def collect_hosts(kind):
    hosts = []
    ids = uidoc.Selection.GetElementIds()
    for element_id in ids:
        element = doc.GetElement(element_id)
        if element is None:
            continue
        try:
            category = element.Category
            category_id = category.Id.IntegerValue if category is not None else -1
        except Exception:
            category_id = -1
        if kind == u"梁" and category_id == int(BuiltInCategory.OST_StructuralFraming):
            hosts.append(element)
        elif kind == u"柱" and category_id == int(BuiltInCategory.OST_StructuralColumns):
            hosts.append(element)
        elif kind == u"自动":
            if category_id in (int(BuiltInCategory.OST_StructuralFraming),
                               int(BuiltInCategory.OST_StructuralColumns)):
                hosts.append(element)
    return hosts


def run(params):
    hosts = collect_hosts(params["scope"])
    if not hosts:
        return 0, [u"没有选中结构梁 / 结构柱"]

    ok = 0
    skipped = 0
    failures = []
    transaction = Transaction(doc, u"梁柱钢筋")
    transaction.Start()
    try:
        for host in hosts:
            kind = u"柱" if params["scope"] == u"柱" or _is_column(host) else u"梁"
            try:
                made, errs = (make_column_rebar(host, params) if kind == u"柱"
                              else make_beam_rebar(host, params))
            except Exception as ex:
                log_line(u"%s 生成异常: %s\n%s" % (kind, ex, traceback.format_exc()))
                made, errs = 0, [text_of(ex)]
            if made:
                ok += 1
            else:
                skipped += 1
            label = u"%s %s" % (kind, name_of(host, u"<无类型>"))
            for item in errs:
                failures.append(u"%s：%s" % (label, item))
        if ok == 0:
            transaction.RollBack()
            return 0, failures or [u"全部失败"]
        transaction.Commit()
    except Exception:
        transaction.RollBack()
        raise

    return ok, failures


def _is_column(host):
    try:
        return host.Category.Id.IntegerValue == int(BuiltInCategory.OST_StructuralColumns)
    except Exception:
        return False


XAML = u"""
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="梁柱钢筋" Height="560" Width="620"
        WindowStartupLocation="CenterScreen">
  <ScrollViewer VerticalScrollBarVisibility="Auto">
    <StackPanel Margin="16">
      <TextBlock Text="梁柱钢筋一键生成" FontSize="18" FontWeight="Bold"/>
      <TextBlock Margin="0,6,0,10" Foreground="#666" TextWrapping="Wrap"
                 Text="先在模型里选中结构梁 / 结构柱，再点生成。使用项目现成的钢筋类型；没有对应直径会自动匹配最接近的。"/>

      <TextBlock Text="① 范围" FontWeight="Bold" Margin="0,4,0,4"/>
      <Grid Margin="0,0,0,4">
        <Grid.ColumnDefinitions>
          <ColumnDefinition Width="110"/>
          <ColumnDefinition Width="*"/>
        </Grid.ColumnDefinitions>
        <TextBlock Text="生成对象" VerticalAlignment="Center"/>
        <ComboBox x:Name="scopeBox" Grid.Column="1" Height="26"/>
      </Grid>

      <TextBlock Text="② 钢筋参数" FontWeight="Bold" Margin="0,10,0,4"/>
      <Grid Margin="0,0,0,4">
        <Grid.ColumnDefinitions>
          <ColumnDefinition Width="110"/>
          <ColumnDefinition Width="*"/>
          <ColumnDefinition Width="110"/>
          <ColumnDefinition Width="*"/>
        </Grid.ColumnDefinitions>
        <TextBlock Text="纵筋直径 (mm)" VerticalAlignment="Center"/>
        <ComboBox x:Name="mainDiaBox" Grid.Column="1" Height="26"/>
        <TextBlock Text="箍筋直径 (mm)" Grid.Column="2" VerticalAlignment="Center" Margin="8,0,0,0"/>
        <ComboBox x:Name="stirrupDiaBox" Grid.Column="3" Height="26"/>
      </Grid>
      <Grid Margin="0,0,0,4">
        <Grid.ColumnDefinitions>
          <ColumnDefinition Width="110"/>
          <ColumnDefinition Width="*"/>
          <ColumnDefinition Width="110"/>
          <ColumnDefinition Width="*"/>
        </Grid.ColumnDefinitions>
        <TextBlock Text="箍筋间距 (mm)" VerticalAlignment="Center"/>
        <TextBox x:Name="spacingBox" Grid.Column="1" Height="26" Text="200"/>
        <TextBlock Text="保护层 (mm)" Grid.Column="2" VerticalAlignment="Center" Margin="8,0,0,0"/>
        <TextBox x:Name="coverBox" Grid.Column="3" Height="26" Text="25"/>
      </Grid>
      <Grid Margin="0,0,0,4">
        <Grid.ColumnDefinitions>
          <ColumnDefinition Width="110"/>
          <ColumnDefinition Width="*"/>
          <ColumnDefinition Width="110"/>
          <ColumnDefinition Width="*"/>
        </Grid.ColumnDefinitions>
        <TextBlock Text="梁上部根数" VerticalAlignment="Center"/>
        <TextBox x:Name="topBox" Grid.Column="1" Height="26" Text="2"/>
        <TextBlock Text="梁下部根数" Grid.Column="2" VerticalAlignment="Center" Margin="8,0,0,0"/>
        <TextBox x:Name="bottomBox" Grid.Column="3" Height="26" Text="2"/>
      </Grid>
      <Grid Margin="0,0,0,4">
        <Grid.ColumnDefinitions>
          <ColumnDefinition Width="110"/>
          <ColumnDefinition Width="*"/>
          <ColumnDefinition Width="110"/>
          <ColumnDefinition Width="*"/>
        </Grid.ColumnDefinitions>
        <TextBlock Text="柱纵筋根数" VerticalAlignment="Center"/>
        <TextBox x:Name="columnBox" Grid.Column="1" Height="26" Text="4"/>
        <TextBlock Text="箍筋起步 (mm)" Grid.Column="2" VerticalAlignment="Center" Margin="8,0,0,0"/>
        <TextBox x:Name="startBox" Grid.Column="3" Height="26" Text="50"/>
      </Grid>

      <TextBlock x:Name="infoText" Margin="0,10,0,6" Foreground="#444" TextWrapping="Wrap"
                 Text="柱纵筋按四边均布，根数取 4 的倍数；梁纵筋沿截面宽度均布。"/>
      <StackPanel Orientation="Horizontal">
        <Button x:Name="btnRun" Content="生成钢筋" Width="110" Height="30" FontWeight="Bold" Margin="0,0,8,0"/>
        <Button x:Name="btnClose" Content="关闭" Width="80" Height="30"/>
      </StackPanel>
    </StackPanel>
  </ScrollViewer>
</Window>
"""


class RebarWindow(forms.WPFWindow):
    def __init__(self):
        forms.WPFWindow.__init__(self, XAML, literal_string=True)

        for label in [u"自动（梁 + 柱）", u"只做梁", u"只做柱"]:
            self.scopeBox.Items.Add(label)
        self.scopeBox.SelectedIndex = 0

        for label in [u"12", u"14", u"16", u"18", u"20", u"22", u"25"]:
            self.mainDiaBox.Items.Add(label)
        self.mainDiaBox.SelectedIndex = 4

        for label in [u"6", u"8", u"10", u"12"]:
            self.stirrupDiaBox.Items.Add(label)
        self.stirrupDiaBox.SelectedIndex = 1

        self.btnRun.Click += self._on_run
        self.btnClose.Click += self._on_close

    def _num(self, box, default):
        try:
            return float(box.Text)
        except Exception:
            return default

    def _combo(self, box, default):
        try:
            return float(unicode(box.Items[box.SelectedIndex]).strip())
        except Exception:
            return default

    def _params(self):
        return {
            "scope": [u"自动", u"梁", u"柱"][max(0, self.scopeBox.SelectedIndex)],
            "main_dia": self._combo(self.mainDiaBox, 20.0),
            "stirrup_dia": self._combo(self.stirrupDiaBox, 8.0),
            "stirrup_spacing": self._num(self.spacingBox, 200.0),
            "cover": self._num(self.coverBox, 25.0),
            "top_bars": self._num(self.topBox, 2),
            "bottom_bars": self._num(self.bottomBox, 2),
            "column_bars": self._num(self.columnBox, 4),
            "stirrup_start": self._num(self.startBox, 50.0),
        }

    def _on_run(self, sender, args):
        params = self._params()
        try:
            ok, failures = run(params)
        except Exception:
            tb = traceback.format_exc()
            log_line(tb)
            forms.alert(u"生成失败：\n{0}".format(tb[-700:]), title=u"梁柱钢筋")
            return

        lines = [u"成功 {0} 个构件。".format(ok)]
        if failures:
            lines.append(u"未完成 {0} 条：".format(len(failures)))
            lines.extend(failures[:10])
        self.infoText.Text = u"\n".join(lines)
        forms.alert(u"\n".join(lines), title=u"梁柱钢筋")

    def _on_close(self, sender, args):
        self.Close()


def main():
    log_line(u"main 进入")
    if doc is None:
        forms.alert(u"请先打开一个项目。", exitscript=True)
    window = RebarWindow()
    log_line(u"窗口创建完成")
    window.ShowDialog()
    log_line(u"ShowDialog 返回")


try:
    main()
except Exception:
    tb = traceback.format_exc()
    log_line(tb)
    output.print_md(u"脚本出错：\n```\n{0}\n```".format(tb))
    forms.alert(u"脚本出错：\n{0}".format(tb[-700:]), title=u"梁柱钢筋")
