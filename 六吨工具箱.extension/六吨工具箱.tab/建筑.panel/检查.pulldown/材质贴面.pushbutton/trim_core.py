# -*- coding: utf-8 -*-
"""装修贴面：小鲶鱼式「圆角贴面 / 洞口封边」的复刻。

两个模式：
  1) 墙面贴面：选中墙 → 按墙面矩形范围向外偏移生成带圆角的贴面板
  2) 洞口封边：选中门/窗 → 在洞口四周生成带圆角的门套/窗套

全部用 DirectShape + 拉伸实体，不改原构件，单事务，Ctrl+Z 可撤销。
"""

import math
import traceback

import clr

clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

from Autodesk.Revit.DB import (  # noqa: E402
    Arc,
    BuiltInCategory,
    BuiltInParameter,
    Color,
    CurveLoop,
    DirectShape,
    Edge,
    ElementId,
    ElementTransformUtils,
    Face,
    FilteredElementCollector,
    GeometryCreationUtilities,
    GeometryObject,
    IFailuresPreprocessor,
    FailureProcessingResult,
    FailureSeverity,
    Line,
    Material,
    Options,
    Plane,
    SolidOptions,
    Transaction,
    ViewDetailLevel,
    Wall,
    XYZ,
)
from Autodesk.Revit.DB.Structure import StructuralType
from Autodesk.Revit.DB import RevitLinkInstance  # noqa: E402
from Autodesk.Revit.UI.Selection import ObjectType  # noqa: E402
from System.Collections.Generic import List as DotNetList  # noqa: E402

from pyrevit import forms, revit  # noqa: E402


CORE_VERSION = u"2026-11-19-h"

MM_TO_FT = 1.0 / 304.8
FT_TO_MM = 304.8

LOG_PATH = r"C:\Users\cyl\Documents\deepseek-harness\default-workspace\_装修贴面日志.txt"


def log_line(text):
    try:
        handle = open(LOG_PATH, "ab")
        handle.write((u"%s\n" % text).encode("utf-8"))
        handle.close()
    except Exception:
        pass


def _active_doc():
    try:
        return revit.doc
    except Exception:
        return None


def mm(value):
    return float(value) * MM_TO_FT


class QuietPreprocessor(IFailuresPreprocessor):
    def PreprocessFailures(self, accessor):
        for message in accessor.GetFailureMessages():
            if message.GetSeverity() == FailureSeverity.Warning:
                accessor.DeleteWarning(message)
        return FailureProcessingResult.Continue


# --------------------------------------------------------------------------
# 几何工具
# --------------------------------------------------------------------------


def face_from_reference(reference):
    """按引用取面；点到棱（Edge）时取它相邻的两个面里面积大的那个。"""
    element = _active_doc().GetElement(reference)
    if element is None:
        return None, None
    geometry = element.GetGeometryObjectFromReference(reference)
    if geometry is None:
        return None, element
    if isinstance(geometry, Edge):
        candidates = []
        for index in (0, 1):
            try:
                face = geometry.GetFace(index)
            except Exception:
                continue
            if face is None:
                continue
            try:
                area = face.Area
            except Exception:
                area = 0.0
            candidates.append((area, face))
        if not candidates:
            return None, element
        candidates.sort(key=lambda row: -row[0])
        return candidates[0][1], element
    if isinstance(geometry, Face):
        return geometry, element
    return None, element


def vertical_faces(element):
    """取构件上面积最大的两个竖直面（|nz| 小）。"""
    result = []
    options = Options()
    options.ComputeReferences = True
    options.DetailLevel = ViewDetailLevel.Fine
    geometry = element.get_Geometry(options)
    if geometry is None:
        return result
    for item in geometry:
        if not hasattr(item, "Faces"):
            continue
        for face in item.Faces:
            try:
                normal = face.ComputeNormal(
                    (face.GetBoundingBox().Min + face.GetBoundingBox().Max) * 0.5)
            except Exception:
                continue
            if abs(normal.Z) > 0.2:
                continue
            result.append((face, normal, face.Area))
    result.sort(key=lambda row: -row[2])
    return result[:2]


def plane_basis(normal):
    normal = normal.Normalize()
    helper = XYZ.BasisZ if abs(normal.Z) < 0.9 else XYZ.BasisX
    u = normal.CrossProduct(helper).Normalize()
    v = normal.CrossProduct(u).Normalize()
    return u, v


def face_corners(face):
    """把一个面的外轮廓简化为四个角（矩形近似）。"""
    points = []
    for loop in face.GetEdgesAsCurveLoops():
        for curve in loop:
            for parameter in (0.0, 0.5, 1.0):
                points.append(curve.Evaluate(parameter, True))
    if len(points) < 4:
        return None
    xs = [point.X for point in points]
    ys = [point.Y for point in points]
    zs = [point.Z for point in points]
    if max(zs) - min(zs) < 1e-6:
        return None
    normal = XYZ(0, 0, 1)
    try:
        box = face.GetBoundingBox()
        normal = face.ComputeNormal((box.Min + box.Max) * 0.5)
    except Exception:
        pass
    u, v = plane_basis(normal)
    us = [point.DotProduct(u) for point in points]
    vs = [point.DotProduct(v) for point in points]
    center = (face.GetBoundingBox().Min + face.GetBoundingBox().Max) * 0.5
    base = center - u * center.DotProduct(u) - v * center.DotProduct(v)
    origin = base + u * ((min(us) + max(us)) * 0.5) + v * ((min(vs) + max(vs)) * 0.5)
    half_u = (max(us) - min(us)) * 0.5
    half_v = (max(vs) - min(vs)) * 0.5
    if half_u < mm(20) or half_v < mm(20):
        return None
    return origin, u, v, half_u, half_v


def arc_between(start, end, center):
    radius = (start - center).GetLength()
    normal = (start - center).CrossProduct(end - center).Normalize()
    start_angle = normal.AngleOnPlaneTo(start - center, normal) if False else None
    return Arc.Create(start, end, end)


def rounded_rect_loop(origin, u, v, half_u, half_v, radius, z_shift):
    """以 origin 为中心、u/v 为轴，按逆时针生成圆角矩形 CurveLoop。"""
    half_u = float(half_u)
    half_v = float(half_v)
    radius = max(0.0, min(float(radius), half_u * 0.95, half_v * 0.95))

    def point(x, y):
        return origin + u * x + v * y + XYZ(0, 0, z_shift)

    loop = CurveLoop()
    if radius <= 1e-6:
        corners = [point(-half_u, -half_v), point(half_u, -half_v),
                   point(half_u, half_v), point(-half_u, half_v)]
        for index in range(4):
            loop.Append(Line.CreateBound(corners[index], corners[(index + 1) % 4]))
        return loop

    pi = math.pi
    centers = [
        (half_u - radius, -half_v + radius, -pi / 2.0, 0.0),
        (half_u - radius, half_v - radius, 0.0, pi / 2.0),
        (-half_u + radius, half_v - radius, pi / 2.0, pi),
        (-half_u + radius, -half_v + radius, pi, pi * 1.5),
    ]
    for index, (cx, cy, a0, a1) in enumerate(centers):
        center = point(cx, cy)
        loop.Append(Arc.Create(center, radius, a0, a1, u, v))
        nx, ny, _na, _nb = centers[(index + 1) % 4]
        end_point = center + u * (radius * math.cos(a1)) + v * (radius * math.sin(a1))
        next_center = point(nx, ny)
        start_point = next_center + u * (radius * math.cos(_na)) + v * (radius * math.sin(_na))
        if (start_point - end_point).GetLength() > 1e-6:
            loop.Append(Line.CreateBound(end_point, start_point))
    return loop


def extrude(loops, direction, distance, material_id):
    options = SolidOptions(material_id, ElementId.InvalidElementId)
    return GeometryCreationUtilities.CreateExtrusionGeometry(
        loops, direction.Normalize(), distance, options)


def make_direct_shape(solids, name):
    category = ElementId(BuiltInCategory.OST_GenericModel)
    shape = DirectShape.CreateElement(_active_doc(), category)
    shape.SetShape(solids)
    try:
        shape.SetName(name)
    except Exception:
        pass
    return shape


# --------------------------------------------------------------------------
# 模式一：墙面贴面
# --------------------------------------------------------------------------


def offset_loops(face, normal, offset_distance):
    """把面的每个环按 offset 偏移，返回 (outer_loop, [hole_loops])；失败返回 (None, [])。"""
    loops = []
    try:
        loops = list(face.GetEdgesAsCurveLoops())
    except Exception:
        return None, []
    if not loops:
        return None, []
    loops.sort(key=lambda loop: -abs(loop.GetExactLength()))
    outer = None
    holes = []
    for index, loop in enumerate(loops):
        try:
            shifted = CurveLoop.CreateViaOffset(loop, offset_distance, normal)
        except Exception:
            continue
        if index == 0:
            outer = shifted
        else:
            holes.append(shifted)
    return outer, holes


def is_rectangular(face, tolerance=1e-3):
    try:
        loops = list(face.GetEdgesAsCurveLoops())
    except Exception:
        return False
    if not loops:
        return False
    outer = loops[0]
    lines = 0
    for curve in outer:
        if isinstance(curve, Line):
            lines += 1
    return lines == 4 and len(outer) == 4


def build_wall_skin(walls, offset_mm, thickness_mm, radius_mm, material_id, both_sides):
    loops_holder = DotNetList[GeometryObject]()
    created = 0
    failures = []
    for wall in walls:
        try:
            faces = vertical_faces(wall)
            if not faces:
                failures.append(u"%s：取不到竖直面" % wall.Id)
                continue
            if not both_sides:
                faces = faces[:1]
            for face, normal, _area in faces:
                info = face_corners(face)
                if info is None:
                    failures.append(u"%s：面轮廓太小" % wall.Id)
                    continue
                origin, u, v, half_u, half_v = info
                direction = normal.Normalize()
                loops = DotNetList[CurveLoop]()
                if is_rectangular(face):
                    shifted = origin + direction * mm(offset_mm)
                    loop = rounded_rect_loop(
                        shifted, u, v,
                        half_u, half_v,
                        mm(radius_mm), 0.0)
                    loops.Add(loop)
                else:
                    # 跟随真实轮廓（含洞口）：外环外扩，内环内缩保持洞口净空
                    outer, holes = offset_loops(face, direction, mm(offset_mm))
                    if outer is None:
                        failures.append(u"%s：面轮廓偏移失败" % wall.Id)
                        continue
                    loops.Add(outer)
                    for hole in holes:
                        reversed_hole = CurveLoop()
                        for curve in reversed(list(hole)):
                            reversed_hole.Append(curve.CreateReversed())
                        loops.Add(reversed_hole)
                solid = extrude(loops, direction, mm(thickness_mm), material_id)
                loops_holder.Add(solid)
                created += 1
        except Exception:
            failures.append(u"%s：%s" % (wall.Id, traceback.format_exc().splitlines()[-1]))
    if created:
        make_direct_shape(loops_holder, u"装修贴面-墙皮")
    return created, failures


# --------------------------------------------------------------------------
# 模式二：洞口封边
# --------------------------------------------------------------------------


def host_wall_of(element):
    try:
        host = element.Host
        if isinstance(host, Wall):
            return host
    except Exception:
        pass
    try:
        if isinstance(element, Wall):
            return element
    except Exception:
        pass
    return None


def opening_box(element, wall):
    """返回 (width, height, base_point)：以墙的局部坐标系为准。"""
    box = element.get_BoundingBox(None)
    if box is None:
        return None
    corner1 = box.Min
    corner2 = box.Max
    location = wall.Location
    curve = location.Curve
    start = curve.GetEndPoint(0)
    end = curve.GetEndPoint(1)
    axis = (end - start).Normalize()
    width = abs((corner2 - corner1).DotProduct(axis))
    height = abs(corner2.Z - corner1.Z)
    center = (corner1 + corner2) * 0.5
    # 洞口中点在墙厚方向上的位置
    if width < mm(200) or height < mm(200):
        return None
    return width, height, center, axis, start, end


def build_opening_trim(openings, out_mm, border_mm, radius_mm, material_id):
    holder = DotNetList[GeometryObject]()
    created = 0
    failures = []
    for element in openings:
        try:
            wall = host_wall_of(element)
            if wall is None:
                failures.append(u"%s：找不到宿主墙" % element.Id)
                continue
            data = opening_box(element, wall)
            if data is None:
                failures.append(u"%s：洞口尺寸取不到" % element.Id)
                continue
            width, height, center, axis, start, end = data

            # 墙的厚度方向 = 墙定位线方向 × Z
            normal = axis.CrossProduct(XYZ.BasisZ).Normalize()
            u = axis
            v = XYZ.BasisZ
            half_w = width * 0.5
            half_h = height * 0.5

            # 外框比洞口大 border，内框是带圆角的洞口
            outer = rounded_rect_loop(
                center + normal * mm(out_mm), u, v,
                half_w + mm(border_mm), half_h + mm(border_mm),
                mm(radius_mm) + mm(border_mm), 0.0)
            inner = rounded_rect_loop(
                center + normal * mm(out_mm), u, v,
                half_w, half_h, mm(radius_mm), 0.0)
            # 内框反向，形成环
            reversed_inner = CurveLoop()
            curves = list(inner)
            for curve in reversed(curves):
                reversed_inner.Append(curve.CreateReversed())

            loops = DotNetList[CurveLoop]()
            loops.Add(outer)
            loops.Add(reversed_inner)
            solid = extrude(loops, normal, mm(border_mm), material_id)
            holder.Add(solid)

            # 另一侧也做一份
            back = rounded_rect_loop(
                center - normal * mm(out_mm), u, v,
                half_w + mm(border_mm), half_h + mm(border_mm),
                mm(radius_mm) + mm(border_mm), 0.0)
            back_inner = rounded_rect_loop(
                center - normal * mm(out_mm), u, v,
                half_w, half_h, mm(radius_mm), 0.0)
            back_reversed = CurveLoop()
            for curve in reversed(list(back_inner)):
                back_reversed.Append(curve.CreateReversed())
            loops2 = DotNetList[CurveLoop]()
            loops2.Add(back)
            loops2.Add(back_reversed)
            holder.Add(extrude(loops2, -normal, mm(border_mm), material_id))
            created += 1
        except Exception:
            failures.append(u"%s：%s" % (element.Id, traceback.format_exc().splitlines()[-1]))
    if created:
        make_direct_shape(holder, u"装修贴面-洞口封边")
    return created, failures



# --------------------------------------------------------------------------
# 任意轮廓倒圆角（对应面板上的「切圆角」）
# --------------------------------------------------------------------------


def simplify_points(points, tolerance):
    """去掉共线点，保留拐角。"""
    result = []
    for index, point in enumerate(points):
        prev_point = points[index - 1]
        next_point = points[(index + 1) % len(points)]
        a = point - prev_point
        b = next_point - point
        if a.GetLength() < tolerance or b.GetLength() < tolerance:
            continue
        cross = a.CrossProduct(b).GetLength()
        if cross < 1e-9 * max(1.0, a.GetLength() * b.GetLength()):
            continue
        result.append(point)
    return result if len(result) >= 3 else points


def rounded_loop_from_points(points, radius, plane_normal, z_shift):
    """把多边形（顶点序列）每个角倒成圆弧，返回 CurveLoop。"""
    points = simplify_points(points, mm(1))
    count = len(points)
    if count < 3:
        return None
    loop = CurveLoop()
    start_points = []
    end_points = []
    arc_centers = []
    for index in range(count):
        prev_point = points[index - 1]
        point = points[index]
        next_point = points[(index + 1) % count]
        to_prev = (prev_point - point)
        to_next = (next_point - point)
        len_prev = to_prev.GetLength()
        len_next = to_next.GetLength()
        if len_prev < 1e-6 or len_next < 1e-6:
            start_points.append(point)
            end_points.append(point)
            arc_centers.append(point)
            continue
        unit_prev = to_prev.Normalize()
        unit_next = to_next.Normalize()
        cos_value = max(-1.0, min(1.0, unit_prev.DotProduct(unit_next)))
        angle = math.acos(cos_value)
        if angle < 1e-3 or angle > math.pi - 1e-3:
            start_points.append(point)
            end_points.append(point)
            arc_centers.append(point)
            continue
        tangent = radius / math.tan(angle * 0.5)
        limit = min(len_prev, len_next) * 0.45
        if tangent > limit:
            tangent = limit
        effective_radius = tangent * math.tan(angle * 0.5)
        first = point + unit_prev * tangent
        second = point + unit_next * tangent
        bisector = (unit_prev + unit_next)
        if bisector.GetLength() < 1e-9:
            start_points.append(first)
            end_points.append(second)
            arc_centers.append(point)
            continue
        bisector = bisector.Normalize()
        half = (math.pi - angle) * 0.5
        distance = effective_radius / max(1e-6, math.sin(half))
        center = point + bisector * distance
        start_points.append(first + XYZ(0, 0, z_shift))
        end_points.append(second + XYZ(0, 0, z_shift))
        arc_centers.append(center + XYZ(0, 0, z_shift))

    for index in range(count):
        # 直线：上一个角的终点 -> 这个角的起点
        line_start = end_points[index - 1]
        line_end = start_points[index]
        if (line_end - line_start).GetLength() > mm(0.5):
            try:
                loop.Append(Line.CreateBound(line_start, line_end))
            except Exception:
                pass
        # 圆弧
        arc_start = start_points[index]
        arc_end = end_points[index]
        if (arc_end - arc_start).GetLength() > mm(0.5):
            try:
                loop.Append(Arc.Create(arc_start, arc_end, arc_centers[index]))
            except Exception:
                try:
                    loop.Append(Line.CreateBound(arc_start, arc_end))
                except Exception:
                    pass
    return loop


def loop_points(loop):
    points = []
    for curve in loop:
        for parameter in (0.0, 1.0):
            points.append(curve.Evaluate(parameter, True))
    return points


def pick_faces(count, prompt):
    """连续点选若干个面，返回 [(element, face, point), ...]。"""
    picked = []
    while len(picked) < count:
        try:
            reference = uidoc.Selection.PickObject(
                ObjectType.PointOnElement,
                u"%s（已选 %d / %d，Esc 取消）" % (prompt, len(picked), count))
        except Exception:
            break
        face, element = face_from_reference(reference)
        if face is None:
            continue
        picked.append((element, face, reference.GlobalPoint))
    return picked


def circle_loop(center, radius, u, v):
    loop = CurveLoop()
    loop.Append(Arc.Create(center, radius, 0.0, math.pi * 2.0, u, v))
    return loop


def line_intersect_2d(p1, d1, p2, d2):
    """XY 平面两条直线求交：p 为点、d 为方向。"""
    denominator = d1.X * d2.Y - d1.Y * d2.X
    if abs(denominator) < 1e-9:
        return None
    t = ((p2.X - p1.X) * d2.Y - (p2.Y - p1.Y) * d2.X) / denominator
    return XYZ(p1.X + d1.X * t, p1.Y + d1.Y * t, 0.0)


def corner_tangent_line(panel):
    """把面板的外表面看成 XY 上的一条直线（点 + 方向）。"""
    origin, u, _v, _hu, _hv, normal = panel
    return (XYZ(origin.X, origin.Y, 0.0), XYZ(u.X, u.Y, 0.0)), XYZ(normal.X, normal.Y, 0.0)


def pick_largest_faces(faces, count):
    scored = []
    for face in faces:
        area = 0.0
        try:
            area = face.Area
        except Exception:
            pass
        scored.append((area, face))
    scored.sort(key=lambda item: -item[0])
    return [face for area, face in scored[:max(1, count)]]


def build_corner_round(panels, radius_mm, thickness_mm, material_id, base_z, height_ft):
    """两个相邻面：在转角处补一块『扇形圆角板』，把尖角抹成圆弧。"""
    if len(panels) < 2:
        return None, u"需要两个面"
    (p1, d1), n1 = corner_tangent_line(panels[0])
    (p2, d2), n2 = corner_tangent_line(panels[1])
    corner = line_intersect_2d(p1, d1, p2, d2)
    if corner is None:
        return None, u"两个面平行，无法求交"

    o1 = XYZ(panels[0][0].X, panels[0][0].Y, 0.0)
    o2 = XYZ(panels[1][0].X, panels[1][0].Y, 0.0)

    def arm(direction, origin):
        vector = XYZ(direction.X, direction.Y, 0.0)
        if vector.GetLength() < 1e-9:
            return XYZ.BasisX
        vector = vector.Normalize()
        if (origin - corner).DotProduct(vector) < 0:
            vector = vector.Negate()
        return vector

    a1 = arm(d1, o1)
    a2 = arm(d2, o2)
    angle1 = math.atan2(a1.Y, a1.X)
    angle2 = math.atan2(a2.Y, a2.X)
    delta = angle2 - angle1
    while delta > math.pi:
        delta -= math.pi * 2.0
    while delta < -math.pi:
        delta += math.pi * 2.0
    if abs(delta) < 1e-3:
        return None, u"两臂夹角为零"
    angle2 = angle1 + delta

    radius = mm(max(radius_mm, 10.0))
    center = XYZ(corner.X, corner.Y, base_z)
    u = XYZ.BasisX
    v = XYZ.BasisY
    start_point = center + u * (radius * math.cos(angle1)) + v * (radius * math.sin(angle1))
    loop = CurveLoop()
    loop.Append(Arc.Create(center, radius, angle1, angle2, u, v))
    loop.Append(Line.CreateBound(start_point, center))
    end_point = center + u * (radius * math.cos(angle2)) + v * (radius * math.sin(angle2))
    loop.Append(Line.CreateBound(center, end_point))
    loops = DotNetList[CurveLoop]()
    loops.Add(loop)
    solid = extrude(loops, XYZ.BasisZ, height_ft, material_id)
    return solid, None


def _segment_distance_2d(p, a, b):
    ax, ay = a.X, a.Y
    bx, by = b.X, b.Y
    px, py = p.X, p.Y
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    if length_sq < 1e-12:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length_sq))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def wall_inventory():
    """诊断：列出文档里所有墙的数量、端点、与链接数量。"""
    doc = _active_doc()
    lines = []
    if doc is None:
        return [u"没有文档"]
    collector = FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_Walls)
    walls = list(collector.WhereElementIsNotElementType())
    lines.append(u"[inv] 文档墙数=%d" % len(walls))
    for wall in walls[:20]:
        try:
            curve = wall.Location.Curve
            a = curve.GetEndPoint(0)
            b = curve.GetEndPoint(1)
            lines.append(u"[inv] %s len=%.0fmm a=(%.0f,%.0f) b=(%.0f,%.0f)" % (
                wall.Id, curve.Length * 304.8, a.X * 304.8, a.Y * 304.8, b.X * 304.8, b.Y * 304.8))
        except Exception:
            lines.append(u"[inv] %s 取不到定位线" % wall.Id)
    try:
        links = list(FilteredElementCollector(doc).OfClass(RevitLinkInstance))
        lines.append(u"[inv] 链接数=%d" % len(links))
        for link in links:
            link_doc = link.GetLinkDocument()
            if link_doc is None:
                lines.append(u"[inv] 链接 %s 未加载" % link.Id)
                continue
            link_walls = list(FilteredElementCollector(link_doc).OfCategory(BuiltInCategory.OST_Walls).WhereElementIsNotElementType())
            lines.append(u"[inv] 链接 %s 里墙数=%d" % (link.Id, len(link_walls)))
    except Exception:
        lines.append(u"[inv] 链接检查失败")
    return lines


def crossing_walls(wall, max_gap_mm=3000.0):
    """找与这面墙「转角相接」的墙：端点距离最近、或墙线在此墙端部附近相交。"""
    doc = _active_doc()
    candidates = []
    if doc is None or wall is None:
        return candidates
    try:
        curve = wall.Location.Curve
    except Exception:
        return candidates
    ends = [curve.GetEndPoint(0), curve.GetEndPoint(1)]
    collector = FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_Walls)
    for other in collector.WhereElementIsNotElementType():
        if other.Id == wall.Id:
            continue
        try:
            other_curve = other.Location.Curve
            a = other_curve.GetEndPoint(0)
            b = other_curve.GetEndPoint(1)
        except Exception:
            continue
        best = min(_segment_distance_2d(point, a, b) for point in ends)
        if best <= mm(max_gap_mm):
            candidates.append((best, other))
    candidates.sort(key=lambda item: item[0])
    return candidates


def neighbors_of(wall, tol_mm=20.0):
    """找与某面墙端点相接的墙（用于只选一面墙时自动找相邻墙）。"""
    doc = _active_doc()
    result = []
    if doc is None or wall is None:
        return result
    try:
        curve = wall.Location.Curve
    except Exception:
        return result
    ends = [curve.GetEndPoint(0), curve.GetEndPoint(1)]
    tol = mm(tol_mm)
    collector = FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_Walls)
    for other in collector.WhereElementIsNotElementType():
        if other.Id == wall.Id:
            continue
        try:
            other_curve = other.Location.Curve
        except Exception:
            continue
        for point in ends:
            for index in (0, 1):
                if (other_curve.GetEndPoint(index) - point).GetLength() < tol:
                    result.append((other, point))
                    break
            else:
                continue
            break
    return result


def round_single_wall(wall, radius_mm, material_id=None, delete_original=True):
    """把单面墙替换成「立面四角带圆角」的实体（原墙删除，可撤销）。"""
    doc = _active_doc()
    if doc is None:
        return 0, [u"没有活动文档"]
    try:
        curve = wall.Location.Curve
    except Exception:
        return 0, [u"取不到墙定位线"]
    start = curve.GetEndPoint(0)
    end = curve.GetEndPoint(1)
    length = (end - start).GetLength()
    height = 0.0
    try:
        height = wall.get_Parameter(BuiltInParameter.WALL_USER_HEIGHT_PARAM).AsDouble()
    except Exception:
        box = wall.get_BoundingBox(None)
        height = box.Max.Z - box.Min.Z if box else 0.0
    if length <= 0 or height <= 0:
        return 0, [u"墙长或墙高为零"]

    direction = (end - start).Normalize()
    normal = XYZ(-direction.Y, direction.X, 0)
    origin = (start + end) * 0.5
    if wall.Flipped:
        normal = normal.Negate()
    origin = origin - normal * (wall.Width * 0.5)
    radius = mm(max(radius_mm, 1.0))
    loop = rounded_rect_loop(origin, direction, XYZ(0, 0, 1),
                             length * 0.5, height * 0.5, radius, 0.0)
    loops = DotNetList[CurveLoop]()
    loops.Add(loop)
    transaction = Transaction(doc, u"单墙整体圆角")
    transaction.Start()
    transaction.GetFailureHandlingOptions().SetFailuresPreprocessor(QuietPreprocessor())
    try:
        solid = GeometryCreationUtilities.CreateExtrusionGeometry(loops, normal, wall.Width)
        shape = DirectShape.CreateElement(doc, ElementId(BuiltInCategory.OST_GenericModel))
        shape.SetShape(DotNetList[GeometryObject]().Add if False else DotNetList[GeometryObject]())
        holder = DotNetList[GeometryObject]()
        holder.Add(solid)
        shape.SetShape(holder)
        shape.SetName(u"圆角墙 %s" % wall.Id)
        if material_id is not None:
            try:
                shape.SetElementType  # noqa: B018
            except Exception:
                pass
        if delete_original:
            doc.Delete(wall.Id)
        transaction.Commit()
        return 1, []
    except Exception:
        try:
            transaction.RollBack()
        except Exception:
            pass
        return 0, [traceback.format_exc().splitlines()[-1]]


def round_wall_corner(walls, radius_mm):
    """把两面墙的转角切成圆角：两墙各自缩短，再补一段弧形墙。"""
    doc = _active_doc()
    if doc is None:
        return 0, [u"没有活动文档"]
    if len(walls) != 2:
        return 0, [u"请选中两面相邻的墙（当前 %d 面）" % len(walls)]

    lines = []
    for wall in walls:
        location = wall.Location
        if not hasattr(location, "Curve"):
            return 0, [u"%s 不是直墙" % wall.Id]
        curve = location.Curve
        if not hasattr(curve, "GetEndPoint"):
            return 0, [u"%s 不是直线墙" % wall.Id]
        lines.append((curve.GetEndPoint(0), curve.GetEndPoint(1)))

    tol = mm(20)
    corner = None
    pairs = [(0, 0), (0, 1), (1, 0), (1, 1)]
    for i, j in pairs:
        if (lines[0][i] - lines[1][j]).GetLength() < tol:
            corner = lines[0][i]
            break
    if corner is None:
        return 0, [u"两面墙没有共用的角点，请选相连的两面墙"]

    # 每面墙「远离转角」的方向
    def arm(index, other, far):
        vector = (far - corner)
        if vector.GetLength() < 1e-9:
            return None
        return vector.Normalize()

    arms = []
    for index in range(2):
        a, b = lines[index]
        if (a - corner).GetLength() < tol:
            arms.append(arm(index, 0, b))
        else:
            arms.append(arm(index, 1, a))
    if arms[0] is None or arms[1] is None:
        return 0, [u"墙长为零"]

    cos_value = max(-1.0, min(1.0, arms[0].DotProduct(arms[1])))
    angle = math.acos(cos_value)
    if angle < math.radians(2.0) or angle > math.pi - math.radians(2.0):
        return 0, [u"两面墙几乎平行，无法倒圆角"]

    width_ft = min(walls[0].Width, walls[1].Width)
    radius_outer = mm(max(radius_mm, 10.0))
    radius_center = radius_outer - width_ft * 0.5
    if radius_center <= mm(5):
        return 0, [u"圆角半径必须大于墙厚的一半（当前墙厚 %.0f mm）" % (width_ft * 304.8)]

    tangent = radius_center / math.tan(angle * 0.5)
    bisector = (arms[0] + arms[1])
    if bisector.GetLength() < 1e-9:
        return 0, [u"两面墙共线，无法倒圆角"]
    bisector = bisector.Normalize()
    center = corner + bisector * (radius_center / math.sin(angle * 0.5))

    log_line(u"[round] corner=%s angle=%.1f r_out=%.0f r_c=%.0f" % (corner, math.degrees(angle), radius_outer * 304.8, radius_center * 304.8))
    point_a = corner + arms[0] * tangent
    point_b = corner + arms[1] * tangent

    level_id = walls[0].LevelId
    wall_type_id = walls[0].WallType.Id
    height = 0.0
    try:
        height = walls[0].get_Parameter(BuiltInParameter.WALL_USER_HEIGHT_PARAM).AsDouble()
    except Exception:
        height = 0.0
    if height <= 0:
        return 0, [u"取不到墙高度"]
    base_offset = 0.0
    try:
        base_offset = walls[0].get_Parameter(BuiltInParameter.WALL_BASE_OFFSET).AsDouble()
    except Exception:
        base_offset = 0.0

    transaction = Transaction(doc, u"墙体倒圆角")
    transaction.Start()
    transaction.GetFailureHandlingOptions().SetFailuresPreprocessor(QuietPreprocessor())
    created = 0
    failures = []
    try:
        for index, wall in enumerate(walls):
            location = wall.Location
            curve = location.Curve
            start = curve.GetEndPoint(0)
            end = curve.GetEndPoint(1)
            if (start - corner).GetLength() < tol:
                new_line = Line.CreateBound(end, point_a if index == 0 else point_b)
            else:
                new_line = Line.CreateBound(point_a if index == 0 else point_b, start)
            if new_line.Length <= mm(10):
                failures.append(u"%s：缩短后墙长为零" % wall.Id)
                continue
            location.Curve = new_line

        angle_a = math.atan2(point_a.Y - center.Y, point_a.X - center.X)
        angle_b = math.atan2(point_b.Y - center.Y, point_b.X - center.X)
        delta = angle_b - angle_a
        while delta > math.pi:
            delta -= math.pi * 2.0
        while delta < -math.pi:
            delta += math.pi * 2.0
        arc = Arc.Create(center, radius_center, angle_a, angle_a + delta,
                         XYZ.BasisX, XYZ.BasisY)
        curved = Wall.Create(doc, arc, wall_type_id, level_id, height, base_offset, False, False)
        if curved is not None:
            created += 1
        else:
            failures.append(u"弧形墙创建失败")
        if created:
            transaction.Commit()
        else:
            transaction.RollBack()
    except Exception:
        try:
            transaction.RollBack()
        except Exception:
            pass
        failures.append(traceback.format_exc().splitlines()[-1])
    return created, failures


def fillet_picked_faces(faces, radius_mm, offset_mm, thickness_mm, material_id):
    """点面 → 按该面的矩形范围生成『带圆角的厚板』（圆角在平面轮廓上，厚度沿法向）。"""
    holder = DotNetList[GeometryObject]()
    created = 0
    failures = []
    panels = []
    for element, face, _point in faces:
        try:
            box = face.GetBoundingBox()
            center_uv = (box.Min + box.Max) * 0.5
            normal = face.ComputeNormal(center_uv)
            normal = normal.Normalize()
            u, v = plane_basis(normal)

            # 用面的世界坐标包围盒在 u/v 上的投影求半宽半高
            corners = []
            for loop in face.GetEdgesAsCurveLoops():
                for curve in loop:
                    corners.append(curve.Evaluate(0.0, True))
                    corners.append(curve.Evaluate(1.0, True))
            if not corners:
                failures.append(u"%s：取不到面轮廓" % element.Id)
                continue
            us = [(p - corners[0]).DotProduct(u) for p in corners]
            vs = [(p - corners[0]).DotProduct(v) for p in corners]
            origin = corners[0] + u * ((min(us) + max(us)) * 0.5) + v * ((min(vs) + max(vs)) * 0.5)
            half_u = (max(us) - min(us)) * 0.5
            half_v = (max(vs) - min(vs)) * 0.5
            if half_u < mm(5) or half_v < mm(5):
                failures.append(u"%s：面太小" % element.Id)
                continue

            base = origin + normal * mm(offset_mm)
            loop = rounded_rect_loop(base, u, v, half_u, half_v, mm(radius_mm), 0.0)
            loops_out = DotNetList[CurveLoop]()
            loops_out.Add(loop)
            solid = None
            try:
                solid = extrude(loops_out, normal, mm(max(thickness_mm, 2.0)), material_id)
            except Exception:
                loops_out = DotNetList[CurveLoop]()
                loops_out.Add(rounded_rect_loop(base, u, v, half_u, half_v, 0.0, 0.0))
                solid = extrude(loops_out, normal, mm(max(thickness_mm, 2.0)), material_id)
            holder.Add(solid)
            created += 1
            panels.append((base, u, v, half_u, half_v, normal, half_v))
        except Exception:
            failures.append(u"%s：%s" % (element.Id, traceback.format_exc().splitlines()[-1]))
    if len(panels) >= 2:
        try:
            base_z = min(panel[0].Z for panel in panels)
            height = mm(max(thickness_mm, 2.0))
            solid, error = build_corner_round(
                panels, radius_mm, thickness_mm, material_id, base_z, height)
            if solid is not None:
                holder.Add(solid)
                created += 1
            elif error:
                failures.append(error)
        except Exception:
            failures.append(traceback.format_exc().splitlines()[-1])
    if created:
        make_direct_shape(holder, u"圆角贴面")
    return created, failures


def run_action(doc, action, params):
    """在外部窗口里调用：action = wall / trim / fillet，params 为 dict。"""
    transaction = Transaction(doc, u"装修贴面")
    transaction.Start()
    transaction.GetFailureHandlingOptions().SetFailuresPreprocessor(QuietPreprocessor())
    created = 0
    failures = []
    try:
        if action == "wall":
            walls = params.get("walls") or []
            created, failures = build_wall_skin(
                walls,
                params.get("offset_mm", 20.0),
                params.get("thick_mm", 10.0),
                params.get("radius_mm", 120.0),
                params.get("material_id", ElementId.InvalidElementId),
                params.get("both_sides", False))
        elif action == "trim":
            created, failures = build_opening_trim(
                params.get("openings") or [],
                params.get("out_mm", 10.0),
                params.get("border_mm", 10.0),
                params.get("radius_mm", 120.0),
                params.get("material_id", ElementId.InvalidElementId))
        elif action == "fillet":
            created, failures = fillet_picked_faces(
                params.get("picks") or [],
                params.get("radius_mm", 120.0),
                params.get("offset_mm", 20.0),
                max(params.get("thick_mm", 10.0), 2.0),
                params.get("material_id", ElementId.InvalidElementId))
        if created == 0:
            transaction.RollBack()
        else:
            transaction.Commit()
    except Exception:
        try:
            transaction.RollBack()
        except Exception:
            pass
        failures.append(traceback.format_exc().splitlines()[-1])
    return created, failures


def pick_faces_for_window(uidoc, count, prompt):
    """供外部窗口调用：隐藏窗口后连续点选面。"""
    from Autodesk.Revit.UI.Selection import ObjectType as _ObjectType
    picked = []
    while len(picked) < count:
        try:
            reference = uidoc.Selection.PickObject(
                _ObjectType.PointOnElement,
                u"%s（已选 %d / %d，Esc 取消）" % (prompt, len(picked), count))
        except Exception:
            break
        face = None
        element = None
        try:
            from Autodesk.Revit.DB import Edge, Face
        except Exception:
            Edge = None
            Face = None
        try:
            element = uidoc.Document.GetElement(reference)
            geometry = element.GetGeometryObjectFromReference(reference)
            if geometry is not None and Face is not None and isinstance(geometry, Face):
                face = geometry
            elif geometry is not None and Edge is not None and isinstance(geometry, Edge):
                best = None
                for index in (0, 1):
                    try:
                        candidate = geometry.GetFace(index)
                    except Exception:
                        continue
                    if candidate is None:
                        continue
                    try:
                        area = candidate.Area
                    except Exception:
                        area = 0.0
                    if best is None or area > best[0]:
                        best = (area, candidate)
                face = best[1] if best else None
        except Exception:
            continue
        if face is None:
            continue
        picked.append((element, face, reference.GlobalPoint))
    return picked
