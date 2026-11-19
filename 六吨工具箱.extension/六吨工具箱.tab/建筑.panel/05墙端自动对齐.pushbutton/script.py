# -*- coding: utf-8 -*-
"""墙端自动对齐：按 L/T 交接把墙端贴到对方墙面，并断开端部连接。"""
import math
import os
import traceback

from Autodesk.Revit.DB import (
    BuiltInCategory, FilteredElementCollector, Line, LocationCurve, Transaction,
    Wall, WallKind, WallUtils, XYZ,
)
from pyrevit import forms, revit

LOG_PATH = r"C:\Users\cyl\Documents\deepseek-harness\default-workspace\_墙端对齐日志.txt"
MM = 1.0 / 304.8
END_TOL = 0.03 * MM
ANGLE_TOL = 3.0
PARALLEL_EPS = 1e-6


def log_line(text):
    try:
        with open(LOG_PATH, "ab") as handle:
            handle.write((u"%s\n" % text).encode("utf-8"))
    except Exception:
        pass


def view_walls(view_id):
    collector = FilteredElementCollector(revit.doc, view_id).OfCategory(BuiltInCategory.OST_Walls)
    walls = []
    for element in collector.WhereElementIsNotElementType():
        if not isinstance(element, Wall):
            continue
        try:
            if element.WallType.Kind != WallKind.Basic:
                continue
        except Exception:
            continue
        location = element.Location
        if not isinstance(location, LocationCurve):
            continue
        if not isinstance(location.Curve, Line):
            continue
        walls.append(element)
    return walls


def endpoints(wall):
    curve = wall.Location.Curve
    return curve.GetEndPoint(0), curve.GetEndPoint(1)


def intersect_2d(p1, d1, p2, d2):
    denominator = d1.X * d2.Y - d1.Y * d2.X
    if abs(denominator) < PARALLEL_EPS:
        return None
    dx = p2.X - p1.X
    dy = p2.Y - p1.Y
    t = (dx * d2.Y - dy * d2.X) / denominator
    return XYZ(p1.X + d1.X * t, p1.Y + d1.Y * t, p1.Z)


def angle_deg(d1, d2):
    value = abs(d1.DotProduct(d2))
    value = max(-1.0, min(1.0, value))
    return math.degrees(math.acos(value))


def param_on_line(point, start, direction):
    return (point.X - start.X) * direction.X + (point.Y - start.Y) * direction.Y


def move_endpoint(wall, keep_index, new_point):
    curve = wall.Location.Curve
    points = [curve.GetEndPoint(0), curve.GetEndPoint(1)]
    points[1 - keep_index] = new_point
    wall.Location.Curve = Line.CreateBound(points[0], points[1])


def disallow(wall, end_index):
    try:
        WallUtils.DisallowWallJoinAtEnd(wall, end_index)
    except Exception:
        pass


def process_pair(first, second):
    a1, b1 = endpoints(first)
    a2, b2 = endpoints(second)
    d1 = (b1 - a1).Normalize()
    d2 = (b2 - a2).Normalize()
    if angle_deg(d1, d2) > ANGLE_TOL:
        return False
    cross = intersect_2d(a1, d1, a2, d2)
    if cross is None:
        return False
    t1 = param_on_line(cross, a1, d1)
    t2 = param_on_line(cross, a2, d2)
    len1 = (b1 - a1).GetLength()
    len2 = (b2 - a2).GetLength()
    at_start1 = abs(t1) < END_TOL
    at_end1 = abs(t1 - len1) < END_TOL
    at_start2 = abs(t2) < END_TOL
    at_end2 = abs(t2 - len2) < END_TOL
    inside1 = -END_TOL < t1 < len1 + END_TOL
    inside2 = -END_TOL < t2 < len2 + END_TOL
    if not (inside1 and inside2):
        return False

    end1 = 0 if at_start1 else (1 if at_end1 else -1)
    end2 = 0 if at_start2 else (1 if at_end2 else -1)
    if end1 < 0 and end2 < 0:
        return False
    if end1 >= 0 and end2 >= 0:
        # L 型：各自退让对方的半墙厚
        target1 = cross + d1 * ((second.Width * 0.5) if at_start1 else -(second.Width * 0.5))
        target2 = cross + d2 * ((first.Width * 0.5) if at_start2 else -(first.Width * 0.5))
        move_endpoint(first, 1 - end1, target1)
        move_endpoint(second, 1 - end2, target2)
        disallow(first, end1)
        disallow(second, end2)
        log_line(u"[L] %s + %s" % (first.Id, second.Id))
        return True

    # T 型：支墙贴到主墙表面
    if end1 >= 0:
        branch, main_wall, branch_end, direction = first, second, end1, d1
    else:
        branch, main_wall, branch_end, direction = second, first, end2, d2
    fixed = (b1 if branch_end == 0 else a1) if branch is first else (b2 if branch_end == 0 else a2)
    normal = XYZ(-direction.Y, direction.X, 0)
    candidates = [cross + normal * (main_wall.Width * 0.5), cross - normal * (main_wall.Width * 0.5)]
    candidates.sort(key=lambda point: (point - fixed).GetLength())
    move_endpoint(branch, 1 - branch_end, candidates[0])
    disallow(branch, branch_end)
    log_line(u"[T] 支墙 %s -> 主墙 %s" % (branch.Id, main_wall.Id))
    return True


def main():
    view = revit.doc.ActiveView
    walls = view_walls(view.Id)
    if len(walls) < 2:
        forms.alert(u"当前视图:%s，墙体数量不足2个，无法进行对齐操作。" % view.Name,
                    title=u"墙端自动对齐")
        return
    log_line(u"[start] 视图=%s 墙数=%d" % (view.Name, len(walls)))
    handled = 0
    skipped = 0
    for index in range(len(walls)):
        for other_index in range(index + 1, len(walls)):
            first = walls[index]
            second = walls[other_index]
            transaction = Transaction(revit.doc, u"墙端自动对齐")
            transaction.Start()
            try:
                if process_pair(first, second):
                    transaction.Commit()
                    handled += 1
                else:
                    transaction.RollBack()
            except Exception:
                try:
                    transaction.RollBack()
                except Exception:
                    pass
                skipped += 1
                log_line(u"[fail] %s/%s %s" % (first.Id, second.Id, traceback.format_exc().splitlines()[-1]))
    log_line(u"[done] 处理=%d 失败=%d" % (handled, skipped))
    forms.alert(u"墙端自动对齐完成：处理 %d 处，跳过失败总计:%d" % (handled, skipped),
                title=u"墙端自动对齐")


try:
    main()
except Exception:
    forms.alert(traceback.format_exc()[-900:], title=u"墙端自动对齐")
