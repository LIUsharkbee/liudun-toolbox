# -*- coding: utf-8 -*-
"""坡屋面一键生成 —— 按檐口轮廓 + 坡度，生成迹线屋顶（FootPrintRoof）。

用法：
  1) 先在平面上选中闭合轮廓（模型线 / 房间边界线 / 墙），或直接点「两点矩形」在图上拉一个矩形；
  2) 填檐口标高、坡度角、悬挑，生成。

说明：
  - 生成的是 Revit 原生「迹线屋顶」，可继续编辑轮廓、加天沟、改材质；
  - 坡度角按「每块轮廓线」设置，屋脊高度由坡度自动算；想精确到脊高就用坡度 = atan((脊高-檐口)/半跨)。
"""

from __future__ import print_function

import math
import os
import traceback

import clr

clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

from Autodesk.Revit.DB import (
    BuiltInCategory,
    BuiltInParameter,
    CurveArray,
    CurveElement,
    CurveLoop,
    ElementId,
    FilteredElementCollector,
    Floor,
    FootPrintRoof,
    Grid,
    Level,
    Line,
    LocationCurve,
    ModelCurveArray,
    Options,
    PlanarFace,
    RoofType,
    Solid,
    Transaction,
    ViewDetailLevel,
    Wall,
    XYZ,
)
from Autodesk.Revit.UI.Selection import ObjectType
from System.Collections.Generic import List as DotNetList

from pyrevit import forms, revit, script

doc = revit.doc
uidoc = revit.uidoc
output = script.get_output()

FT_TO_MM = 304.8
MM_TO_FT = 1.0 / 304.8

XAML = u"""<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
    xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
    Title="坡屋面" Height="560" Width="560" WindowStartupLocation="CenterScreen">
  <ScrollViewer VerticalScrollBarVisibility="Auto">
    <StackPanel Margin="14">
      <TextBlock Text="坡屋面" FontSize="18" FontWeight="Bold" Margin="0,0,0,10"/>

      <TextBlock Text="① 檐口与坡度" FontWeight="Bold" Margin="0,0,0,6"/>
      <Grid Margin="0,0,0,6">
        <Grid.ColumnDefinitions>
          <ColumnDefinition Width="150"/>
          <ColumnDefinition Width="*"/>
        </Grid.ColumnDefinitions>
        <TextBlock Grid.Column="0" Text="檐口标高 (mm)" VerticalAlignment="Center"/>
        <TextBox Grid.Column="1" x:Name="eaveBox" Text="8020"/>
      </Grid>
      <Grid Margin="0,0,0,6">
        <Grid.ColumnDefinitions>
          <ColumnDefinition Width="150"/>
          <ColumnDefinition Width="*"/>
        </Grid.ColumnDefinitions>
        <TextBlock Grid.Column="0" Text="坡度角 (°)" VerticalAlignment="Center"/>
        <TextBox Grid.Column="1" x:Name="slopeBox" Text="30"/>
      </Grid>
      <Grid Margin="0,0,0,6">
        <Grid.ColumnDefinitions>
          <ColumnDefinition Width="150"/>
          <ColumnDefinition Width="*"/>
        </Grid.ColumnDefinitions>
        <TextBlock Grid.Column="0" Text="檐口悬挑 (mm)" VerticalAlignment="Center"/>
        <TextBox Grid.Column="1" x:Name="overBox" Text="0"/>
      </Grid>
      <CheckBox x:Name="dualBox" Content="双坡：只给两条长边设坡度（否则四坡/全部边）"
                IsChecked="True" Margin="0,0,0,8"/>
      <Grid Margin="0,0,0,8">
        <Grid.ColumnDefinitions>
          <ColumnDefinition Width="150"/>
          <ColumnDefinition Width="*"/>
        </Grid.ColumnDefinitions>
        <TextBlock Grid.Column="0" Text="屋脊方向" VerticalAlignment="Center"/>
        <ComboBox Grid.Column="1" x:Name="ridgeBox">
          <ComboBoxItem Content="沿长边（默认）"/>
          <ComboBoxItem Content="沿短边"/>
        </ComboBox>
      </Grid>
      <Grid Margin="0,0,0,10">
        <Grid.ColumnDefinitions>
          <ColumnDefinition Width="150"/>
          <ColumnDefinition Width="*"/>
        </Grid.ColumnDefinitions>
        <TextBlock Grid.Column="0" Text="屋顶类型" VerticalAlignment="Center"/>
        <ComboBox Grid.Column="1" x:Name="typeBox"/>
      </Grid>

      <TextBlock Text="② 檐口轮廓" FontWeight="Bold" Margin="0,0,0,6"/>
      <TextBlock TextWrapping="Wrap" Foreground="Gray" Margin="0,0,0,6"
                 Text="先在平面上框选檐口的线（模型线），再点下面的按钮；工具会自动把线接成闭合轮廓。"/>
      <Button x:Name="btnSel" Content="生成坡屋面" Height="34" FontWeight="Bold" Margin="0,0,0,10"/>

      <TextBlock Text="③ 预览" FontWeight="Bold" Margin="0,0,0,6"/>
      <Border BorderBrush="Gray" BorderThickness="1" Padding="8" Margin="0,0,0,10">
        <TextBlock x:Name="preview" TextWrapping="Wrap" FontFamily="Consolas" FontSize="12"/>
      </Border>

      <Button x:Name="btnClose" Content="关闭" Height="30" Width="100" HorizontalAlignment="Right"/>
    </StackPanel>
  </ScrollViewer>
</Window>"""


# ---------------------------------------------------------------- 数据

def type_name(element, default=u"(无名)"):
    try:
        name = element.Name
        if name:
            return name
    except Exception:
        pass
    try:
        name = element.get_Name()
        if name:
            return name
    except Exception:
        pass
    try:
        param = element.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM)
        if param is not None:
            return param.AsString()
    except Exception:
        pass
    return default


def all_levels():
    levels = list(FilteredElementCollector(doc).OfClass(Level))
    levels = [l for l in levels if not getattr(l, "IsTemplate", False)]
    levels.sort(key=lambda l: l.Elevation)
    return levels


def all_roof_types():
    types = list(FilteredElementCollector(doc).OfClass(RoofType))
    types.sort(key=lambda t: type_name(t))
    return types


def level_for_height(height_mm):
    """取最接近檐口标高的标高。"""
    target = height_mm * MM_TO_FT
    levels = all_levels()
    if not levels:
        return None
    best = levels[0]
    for level in levels:
        if abs(level.Elevation - target) < abs(best.Elevation - target):
            best = level
    return best


def selected_curves():
    """把选中图元的边线取成 [(x, y), ...] 的闭合轮廓（优先模型线）。"""
    loops = []
    ids = uidoc.Selection.GetElementIds()
    for element_id in ids:
        element = doc.GetElement(element_id)
        if element is None:
            continue
        if isinstance(element, CurveElement):
            try:
                curve = element.GeometryCurve
                points = [(p.X, p.Y) for p in curve.Tessellate()]
                loops.append(points)
            except Exception:
                pass
    return loops


def round_points(points, tol=0.02):
    """去重 + 去重尾点。"""
    result = []
    for point in points:
        if not result:
            result.append(point)
            continue
        last = result[-1]
        if abs(point[0] - last[0]) > tol or abs(point[1] - last[1]) > tol:
            result.append(point)
    if len(result) > 2:
        first, last = result[0], result[-1]
        if abs(first[0] - last[0]) <= tol and abs(first[1] - last[1]) <= tol:
            result.pop()
    return result


def near(a, b, tol=0.02):
    return abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol


def assemble_loops(segments, tol=0.02):
    """把一堆线段按端点接龙成闭合轮廓。"""
    loops = []
    remaining = list(segments)
    while remaining:
        head = remaining.pop(0)
        chain = [head[0], head[1]]
        changed = True
        while changed:
            changed = False
            for index, seg in enumerate(remaining):
                if near(seg[0], chain[-1], tol):
                    chain.append(seg[1])
                    remaining.pop(index)
                    changed = True
                    break
                if near(seg[1], chain[-1], tol):
                    chain.append(seg[0])
                    remaining.pop(index)
                    changed = True
                    break
        if len(chain) >= 4 and near(chain[0], chain[-1], tol):
            loops.append(chain[:-1])
    return loops


def curves_from_ids(ids):
    """把图元 id 里的模型线取成线段列表。"""
    segments = []
    for element_id in ids:
        element = doc.GetElement(element_id)
        if element is None:
            continue
        if isinstance(element, CurveElement):
            try:
                points = [(p.X, p.Y) for p in element.GeometryCurve.Tessellate()]
            except Exception:
                continue
            for i in range(len(points) - 1):
                segments.append((points[i], points[i + 1]))
    return segments


def signed_area(points):
    total = 0.0
    count = len(points)
    for i in range(count):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % count]
        total += x1 * y2 - x2 * y1
    return total / 2.0


def ensure_ccw(points):
    if signed_area(points) < 0:
        return list(reversed(points))
    return points


def offset_polygon(points, distance):
    """把闭合多边形向外偏移（仅凸多边形严格正确，凹的也能近似）。"""
    if abs(distance) < 1e-9:
        return points
    count = len(points)
    lines = []
    for i in range(count):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % count]
        dx, dy = x2 - x1, y2 - y1
        length = math.hypot(dx, dy)
        if length < 1e-9:
            continue
        nx, ny = dy / length, -dx / length
        lines.append((x1 + nx * distance, y1 + ny * distance,
                      x2 + nx * distance, y2 + ny * distance))
    if len(lines) != count:
        return points
    result = []
    for i in range(count):
        prev = lines[i - 1]
        cur = lines[i]
        hit = line_intersect(prev, cur)
        result.append(hit if hit else (cur[0], cur[1]))
    return result


def line_intersect(a, b):
    x1, y1, x2, y2 = a
    x3, y3, x4, y4 = b
    den = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(den) < 1e-9:
        return None
    t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / den
    return (x1 + t * (x2 - x1), y1 + t * (y2 - y1))


# ---------------------------------------------------------------- 生成

def log_line(text):
    try:
        path = r"C:\Users\cyl\Documents\deepseek-harness\default-workspace\_屋面日志.txt"
        handle = open(path, "ab")
        try:
            handle.write((text + u"\n").encode("utf-8"))
        finally:
            handle.close()
    except Exception:
        pass


def set_curve_slope(roof, curve, angle, define=True):
    """给屋顶轮廓线设坡度（Revit 2020 实测：只能走轮廓线参数的路径）。"""
    element = curve
    ok = False
    try:
        element = curve.Element if hasattr(curve, "Element") else curve
    except Exception:
        element = curve

    try:
        param = element.get_Parameter(BuiltInParameter.ROOF_CURVE_IS_SLOPE_DEFINING)
        if param is None:
            log_line(u"[p] IS_SLOPE_DEFINING 参数不存在")
        elif param.IsReadOnly:
            log_line(u"[p] IS_SLOPE_DEFINING 只读")
        else:
            param.Set(1 if define else 0)
            ok = True
            log_line(u"[p] IS_SLOPE_DEFINING = {0}".format(1 if define else 0))
    except Exception as error:
        log_line(u"[p] IS_SLOPE_DEFINING 异常：{0}".format(error))

    try:
        slope_param = element.get_Parameter(BuiltInParameter.ROOF_SLOPE) if define else None
        if not define:
            pass
        elif slope_param is None:
            log_line(u"[p] ROOF_SLOPE 参数不存在")
        elif slope_param.IsReadOnly:
            log_line(u"[p] ROOF_SLOPE 只读")
        else:
            slope_param.Set(angle)
            ok = True
            log_line(u"[p] ROOF_SLOPE ok = {0:.3f}".format(angle))
    except Exception as error:
        log_line(u"[p] ROOF_SLOPE 异常：{0}".format(error))

    if not ok:
        try:
            dx = roof.DefinesSlope
            log_line(u"[p] DefinesSlope 类型 {0}".format(type(dx)))
        except Exception as error:
            log_line(u"[p] 读 DefinesSlope 失败：{0}".format(error))
        try:
            roof.DefinesSlope[curve] = True
            ok = True
            log_line(u"[p] 索引属性 ok")
        except Exception as error:
            log_line(u"[p] 索引属性失败：{0}".format(error))
    return ok


def make_roof(corners, params):
    eave_mm = params["eave"]
    slope_deg = params["slope"]
    overhang_mm = params["overhang"]
    roof_type = params["roof_type"]

    points = ensure_ccw(round_points([(p[0], p[1]) for p in corners]))
    if len(points) < 3:
        raise Exception(u"轮廓点数不足（至少 3 个点），请确认选择的是闭合轮廓。")

    if overhang_mm:
        points = offset_polygon(points, overhang_mm * MM_TO_FT)

    level = level_for_height(eave_mm)
    if level is None:
        raise Exception(u"项目里没有标高，无法确定屋顶所在标高。")
    if roof_type is None:
        raise Exception(u"项目里没有屋顶类型。")

    z = level.Elevation
    log_line(u"[{0}] 点 {1} 个 · 檐口 {2} mm · 标高 {3} = {4:.0f} mm · 类型 {5}".format(
        u"start", len(points), eave_mm, type_name(level), level.Elevation / MM_TO_FT * 1.0,
        type_name(roof_type)))

    curves = CurveArray()
    count = len(points)
    for i in range(count):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % count]
        try:
            curves.Append(Line.CreateBound(XYZ(x1, y1, z), XYZ(x2, y2, z)))
        except Exception as error:
            raise Exception(u"轮廓第 {0} 段无效（可能长度为 0）：{1}".format(i + 1, error))

    reference = clr.Reference[ModelCurveArray](ModelCurveArray())
    try:
        roof = doc.Create.NewFootPrintRoof(curves, level, roof_type, reference)
    except Exception as error:
        log_line(u"[fail] NewFootPrintRoof: {0}".format(error))
        raise Exception(u"创建迹线屋顶失败：{0}".format(error))

    angle = slope_deg * math.pi / 180.0
    slopes = 0
    try:
        model_curves = reference.Value
        if model_curves is not None:
            total = model_curves.Size
            # 双坡：只给两条较长的对边设坡度（屋脊沿长向，两端出山墙）
            long_edges = []
            if params.get("slope_mode") == "long" and len(points) == 4:
                lengths = []
                for i in range(4):
                    a = points[i]
                    b = points[(i + 1) % 4]
                    lengths.append(math.hypot(b[0] - a[0], b[1] - a[1]))
                if params.get("ridge", 0) == 1:
                    order = sorted(range(4), key=lambda k: lengths[k])
                else:
                    order = sorted(range(4), key=lambda k: -lengths[k])
                long_edges = sorted(order[:2])
                log_line(u"[p] 边长 {0}，选边 {1}".format(
                    [round(v, 2) for v in lengths], long_edges))
            matched = 0
            for i in range(total):
                curve = model_curves.get_Item(i)
                # get_Item 返回的是 ModelCurve 元素，几何在 GeometryCurve 上
                geometry = curve
                try:
                    if hasattr(curve, "GeometryCurve"):
                        geometry = curve.GeometryCurve
                except Exception:
                    geometry = curve
                if long_edges:
                    # Revit 返回的边顺序不一定与输入一致，按中点匹配
                    try:
                        p0 = geometry.GetEndPoint(0)
                        p1 = geometry.GetEndPoint(1)
                        mid = ((p0.X + p1.X) / 2.0, (p0.Y + p1.Y) / 2.0)
                    except Exception as error:
                        log_line(u"[warn] 取边中点失败：{0}".format(error))
                        continue
                    best_index = None
                    best_dist = None
                    for k in range(len(points)):
                        a = points[k]
                        b = points[(k + 1) % len(points)]
                        em = ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
                        dist = math.hypot(mid[0] - em[0], mid[1] - em[1])
                        if best_dist is None or dist < best_dist:
                            best_dist = dist
                            best_index = k
                    if best_index not in long_edges:
                        # 非檐口边：显式取消「定义坡度」，否则 Revit 会按默认四面坡生成
                        set_curve_slope(roof, curve, angle, define=False)
                        continue
                    matched += 1
                if set_curve_slope(roof, curve, angle, define=True):
                    slopes += 1
            log_line(u"[p] 共 {0} 条边，匹配到长边 {1} 条，成功设坡 {2} 条".format(total, matched, slopes))
    except Exception as error:
        log_line(u"[warn] slope: {0}".format(error))

    if slopes == 0:
        raise Exception(u"屋顶已建，但坡度设置失败（{0} 条边）。日志：C:\\Users\\cyl\\Documents\\deepseek-harness\\default-workspace\\_屋面日志.txt".format(total))

    offset = eave_mm * MM_TO_FT - z
    param = roof.get_Parameter(BuiltInParameter.ROOF_LEVEL_OFFSET_PARAM)
    if param is not None and not param.IsReadOnly:
        try:
            param.Set(offset)
        except Exception as error:
            log_line(u"[warn] offset: {0}".format(error))

    log_line(u"[ok] roof {0} slopes {1}".format(roof.Id.IntegerValue, slopes))
    return roof, points


def describe(points, params):
    area = abs(signed_area(points)) * 0.09290304
    eave = params["eave"]
    slope = params["slope"]
    half_span_mm = 0.0
    if len(points) >= 3:
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        half_span_mm = min(max(xs) - min(xs), max(ys) - min(ys)) * 0.5 / MM_TO_FT
    rise = half_span_mm * math.tan(slope * math.pi / 180.0)
    ridge = eave + rise
    lines = [
        u"檐口标高：+{0:.0f} mm".format(eave),
        u"坡度：{0:.1f}°（1:{1:.2f}）".format(slope, 1.0 / max(math.tan(slope * math.pi / 180.0), 1e-6)),
        u"檐口轮廓：{0} 个点，投影面积 {1:.2f} m²".format(len(points), area),
        u"估算半跨：{0:.0f} mm".format(half_span_mm),
        u"估算屋脊：+{0:.0f} mm".format(ridge),
        u"屋顶类型：{0}".format(type_name(params["roof_type"])),
    ]
    if params["overhang"]:
        lines.append(u"檐口悬挑：{0:.0f} mm".format(params["overhang"]))
    return u"\n".join(lines)


def run_generation(groups, params):
    made = 0
    failures = []
    transaction = Transaction(doc, u"生成坡屋面")
    try:
        transaction.Start()
        for corners in groups:
            try:
                make_roof(corners, params)
                made += 1
            except Exception as error:
                failures.append(u"{0}".format(error))
        if made == 0:
            transaction.RollBack()
        else:
            transaction.Commit()
    except Exception:
        try:
            transaction.RollBack()
        except Exception:
            pass
        failures.append(traceback.format_exc()[-600:])
    return made, failures


# ---------------------------------------------------------------- 界面

class RoofWindow(forms.WPFWindow):
    def __init__(self, state):
        forms.WPFWindow.__init__(self, XAML, literal_string=True)
        self.state = state
        self.levels = all_levels()
        self.types = all_roof_types()

        self.eave_box = self.FindName("eaveBox")
        self.slope_box = self.FindName("slopeBox")
        self.over_box = self.FindName("overBox")
        self.type_box = self.FindName("typeBox")
        self.dual_box = self.FindName("dualBox")
        self.ridge_box = self.FindName("ridgeBox")
        self.ridge_box.SelectedIndex = 0
        self.preview = self.FindName("preview")

        for roof_type in self.types:
            self.type_box.Items.Add(type_name(roof_type))
        if self.types:
            self.type_box.SelectedIndex = 0

        self.FindName("btnSel").Click += self._on_selection
        self.FindName("btnClose").Click += self._on_close

        self.eave_box.TextChanged += self._on_preview
        self.slope_box.TextChanged += self._on_preview
        self._on_preview(None, None)

    def _on_preview(self, sender, args):
        try:
            params = self._collect()
            corners = self._preview_corners()
            if corners:
                self.preview.Text = describe(corners, params)
            else:
                self.preview.Text = u"还没有轮廓：请选中闭合模型线，或点「两点矩形」。"
        except Exception:
            self.preview.Text = u"参数还没填完。"

    def _preview_corners(self):
        loops = selected_curves()
        if loops:
            return ensure_ccw(round_points(loops[0]))
        return None

    def _to_float(self, text, default):
        try:
            return float((text or u"").strip())
        except Exception:
            return default

    def _collect(self):
        index = self.type_box.SelectedIndex
        roof_type = self.types[index] if 0 <= index < len(self.types) else None
        if roof_type is None:
            raise Exception(u"项目里没有屋顶类型，请先新建一个「基本屋顶」。")
        return {
            "eave": self._to_float(self.eave_box.Text, 8020.0),
            "slope": self._to_float(self.slope_box.Text, 30.0),
            "overhang": self._to_float(self.over_box.Text, 0.0),
            "roof_type": roof_type,
            "slope_mode": "long" if bool(self.dual_box.IsChecked) else "all",
            "ridge": int(self.ridge_box.SelectedIndex) if self.ridge_box.SelectedIndex is not None else 0,
        }

    def _on_selection(self, sender, args):
        ids = list(uidoc.Selection.GetElementIds())
        if not ids:
            self.preview.Text = u"还没有选中东西。先在平面上框选檐口的线/轴线/墙/楼板，再点这个按钮。"
            return
        segments, direct_loops, counts = segments_from_ids(ids)
        groups = []
        for points in direct_loops:
            cleaned = ensure_ccw(round_points(points))
            if len(cleaned) >= 3:
                groups.append(cleaned)
        for points in assemble_loops(segments):
            cleaned = ensure_ccw(round_points(points))
            if len(cleaned) >= 3:
                groups.append(cleaned)
        if not groups:
            polygon = polygon_from_segments(segments)
            if polygon:
                cleaned = ensure_ccw(round_points(polygon))
                if len(cleaned) >= 3:
                    groups.append(cleaned)
        head = (u"选中：线 {0}、轴线 {1}、墙 {2}、楼板 {3}；"
                u"得到 {4} 段线、{5} 个闭合轮廓。").format(
                    counts["line"], counts["grid"], counts["wall"], counts["floor"],
                    len(segments), len(groups))
        if not groups:
            self.preview.Text = head + u"\n没能围成闭合轮廓：检查转角是否缺线、线条是否重复。"
            return
        self.state["params"] = self._collect()
        self.state["groups"] = groups
        self.state["action"] = "selection"
        self.Close()

    def _on_box(self, sender, args):
        self.state["params"] = self._collect()
        self.state["action"] = "box"
        self.Close()

    def _on_pick(self, sender, args):
        self.state["params"] = self._collect()
        self.state["action"] = "pick"
        self.Close()

    def _on_close(self, sender, args):
        self.state["action"] = None
        self.Close()


def append_curve(segments, curve):
    try:
        points = [(p.X, p.Y) for p in curve.Tessellate()]
    except Exception:
        return
    for i in range(len(points) - 1):
        segments.append((points[i], points[i + 1]))


def floor_outline(floor):
    """取楼板顶面的最大一圈外轮廓（平面点，单位 ft）。"""
    options = Options()
    options.DetailLevel = ViewDetailLevel.Fine
    geometry = floor.get_Geometry(options)
    if geometry is None:
        return None
    best = None
    best_area = 0.0
    for obj in geometry:
        if not isinstance(obj, Solid):
            continue
        for face in obj.Faces:
            if not isinstance(face, PlanarFace):
                continue
            if face.Normal.Z < 0.9:
                continue
            for edge_loop in face.EdgeLoops:
                points = []
                for edge in edge_loop:
                    try:
                        curve = edge.AsCurve()
                    except Exception:
                        continue
                    for p in curve.Tessellate():
                        pt = (p.X, p.Y)
                        if not points or not near(pt, points[-1]):
                            points.append(pt)
                if len(points) >= 3 and near(points[0], points[-1]):
                    points.pop()
                if len(points) < 3:
                    continue
                area = abs(signed_area(points))
                if area > best_area:
                    best_area = area
                    best = points
    return best


def segments_from_ids(ids):
    """支持：模型线/详图线、轴线、墙（定位线）、楼板（顶面外轮廓）。"""
    segments = []
    loops = []
    counts = {"line": 0, "grid": 0, "wall": 0, "floor": 0}
    for element_id in ids:
        element = doc.GetElement(element_id)
        if element is None:
            continue
        if isinstance(element, CurveElement):
            counts["line"] += 1
            append_curve(segments, element.GeometryCurve)
        elif isinstance(element, Grid):
            counts["grid"] += 1
            append_curve(segments, element.Curve)
        elif isinstance(element, Wall):
            counts["wall"] += 1
            try:
                location = element.Location
                if isinstance(location, LocationCurve):
                    append_curve(segments, location.Curve)
            except Exception:
                pass
        elif isinstance(element, Floor):
            counts["floor"] += 1
            outline = floor_outline(element)
            if outline:
                loops.append(outline)
    return segments, loops, counts


def _line_cross(a1, a2, b1, b2):
    """两条无限直线的交点；平行返回 None。"""
    x1, y1 = a1
    x2, y2 = a2
    x3, y3 = b1
    x4, y4 = b2
    den = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(den) < 1e-9:
        return None
    px = ((x1 * y2 - y1 * x2) * (x3 - x4) - (x1 - x2) * (x3 * y4 - y3 * x4)) / den
    py = ((x1 * y2 - y1 * x2) * (y3 - y4) - (y1 - y2) * (x3 * y4 - y3 * x4)) / den
    return (px, py)


def polygon_from_segments(segments, tol=0.02):
    """把若干「长线」（轴线/墙）按两两交点拼成多边形。

    轴线的端点通常不重合，所以接龙会失败；这里改用交点求角点。
    """
    if len(segments) < 3 or len(segments) > 8:
        return None
    points = []
    for i in range(len(segments)):
        for j in range(i + 1, len(segments)):
            point = _line_cross(segments[i][0], segments[i][1], segments[j][0], segments[j][1])
            if point is None:
                continue
            if not any(near(point, existing, tol) for existing in points):
                points.append(point)
    if len(points) < 3:
        return None
    cx = sum(p[0] for p in points) / len(points)
    cy = sum(p[1] for p in points) / len(points)
    points.sort(key=lambda p: math.atan2(p[1] - cy, p[0] - cx))
    return points


def pick_box_outline(params):
    made = 0
    failures = []
    while True:
        try:
            refs = uidoc.Selection.PickObjects(
                ObjectType.Element, u"框选闭合轮廓（可框选多条模型线），回车结束")
        except Exception:
            break
        ids = [ref.ElementId for ref in refs]
        segments = curves_from_ids(ids)
        if not segments:
            failures.append(u"框选范围里没有模型线，请确认选的是线而不是墙/文字。")
            continue
        loops = assemble_loops(segments)
        loops = [ensure_ccw(round_points(points)) for points in loops if len(points) >= 3]
        if not loops:
            failures.append(u"线段没有接成闭合轮廓：转角处可能断开，或混进了别的线。")
            continue
        ok, bad = run_generation(loops, params)
        made += ok
        failures.extend(bad)
    return made, failures


def pick_two_points(params):
    made = 0
    failures = []
    while True:
        try:
            first = uidoc.Selection.PickPoint(u"点檐口第一角（Esc 结束）")
        except Exception:
            break
        try:
            second = uidoc.Selection.PickPoint(u"点檐口对角")
        except Exception:
            break
        corners = [
            (first.X, first.Y),
            (second.X, first.Y),
            (second.X, second.Y),
            (first.X, second.Y),
        ]
        ok, bad = run_generation([corners], params)
        made += ok
        failures.extend(bad)
    return made, failures


def main():
    if doc is None:
        forms.alert(u"请先打开一个项目。", exitscript=True)

    while True:
        state = {"action": None, "params": None, "groups": None}
        window = RoofWindow(state)
        window.ShowDialog()
        action = state.get("action")
        if not action:
            return
        params = state["params"]
        groups = state["groups"] or []
        if not groups:
            forms.alert(u"没有可用轮廓。", title=u"坡屋面")
            continue
        made, failures = run_generation(groups, params)
        message = u"生成完成：成功 {0} 块".format(made)
        if failures:
            message += u"，失败 {0} 块：\n{1}".format(len(failures), u"\n".join(failures[:5]))
        forms.alert(message, title=u"坡屋面")


try:
    main()
except Exception:
    detail = traceback.format_exc()
    output.print_md(u"```\n{0}\n```".format(detail))
    forms.alert(u"坡屋面生成出错：\n{0}".format(detail[-800:]), title=u"坡屋面")
