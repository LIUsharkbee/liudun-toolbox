# -*- coding: utf-8 -*-
"""房间创建边界：把房间的闭合边界落成房间分隔线，固化房间范围。"""
import os
import traceback

import clr

from Autodesk.Revit.DB import (
    BuiltInCategory, BuiltInParameter, CurveArray, ElementId, FilteredElementCollector,
    IFailuresPreprocessor, FailureProcessingResult, FailureSeverity, ModelCurve,
    Plane, SketchPlane, Transaction, ViewPlan, XYZ,
)
from Autodesk.Revit.DB.Architecture import Room
from Autodesk.Revit.DB import SpatialElementBoundaryOptions
from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType
from System.Collections.Generic import List as DotNetList

from pyrevit import forms, revit, script

LOG_PATH = r"C:\Users\cyl\Documents\deepseek-harness\default-workspace\_房间边界日志.txt"
TOL_MM = 2.0
MIN_SEG_MM = 0.08
MM_TO_FT = 1.0 / 304.8


def log_line(text):
    try:
        with open(LOG_PATH, "ab") as handle:
            handle.write((u"%s\n" % text).encode("utf-8"))
    except Exception:
        pass


class RoomFilter(ISelectionFilter):
    def AllowElement(self, element):
        return isinstance(element, Room) and element.Area > 0

    def AllowReference(self, reference, point):
        return True


class QuietPreprocessor(IFailuresPreprocessor):
    def PreprocessFailures(self, accessor):
        for message in accessor.GetFailureMessages():
            if message.GetSeverity() == FailureSeverity.Warning:
                accessor.DeleteWarning(message)
        return FailureProcessingResult.Continue


def level_z(view):
    if isinstance(view, ViewPlan) and view.GenLevel is not None:
        return view.GenLevel.Elevation
    return 0.0


def flatten(curve, z):
    start = curve.GetEndPoint(0)
    end = curve.GetEndPoint(1)
    return ((start.X, start.Y), (end.X, end.Y))


def segment_key(pair):
    a, b = pair
    if (a[0], a[1]) > (b[0], b[1]):
        a, b = b, a
    return a + b


def same_segment(first, second, tol):
    def close(p, q):
        return abs(p[0] - q[0]) <= tol and abs(p[1] - q[1]) <= tol

    a1, b1 = first
    a2, b2 = second
    return (close(a1, a2) and close(b1, b2)) or (close(a1, b2) and close(b1, a2))


def existing_segments(doc, view):
    result = []
    collector = FilteredElementCollector(doc, view.Id).OfClass(ModelCurve)
    for element in collector:
        try:
            style = element.LineStyle
        except Exception:
            style = None
        name = u""
        if style is not None and style.Name:
            name = style.Name
        category = element.Category.Name if element.Category is not None else u""
        if u"房间分隔" in name or u"Room Separation" in name or u"房间分隔" in category:
            try:
                result.append(flatten(element.GeometryCurve, level_z(view)))
            except Exception:
                continue
    return result


def room_curves(room, z):
    options = SpatialElementBoundaryOptions()
    loops = room.GetBoundarySegments(options)
    result = []
    for loop in loops:
        for segment in loop:
            curve = segment.GetCurve()
            if curve is None:
                continue
            pair = flatten(curve, z)
            length = ((pair[0][0] - pair[1][0]) ** 2 + (pair[0][1] - pair[1][1]) ** 2) ** 0.5
            if length * 304.8 < MIN_SEG_MM:
                continue
            result.append(pair)
    return result


def curve_from_pair(doc, pair, z):
    from Autodesk.Revit.DB import Line
    a, b = pair
    return Line.CreateBound(XYZ(a[0], a[1], z), XYZ(b[0], b[1], z))


def main():
    doc = revit.doc
    uidoc = revit.uidoc
    view = doc.ActiveView
    if not isinstance(view, ViewPlan):
        forms.alert(u"当前视图无法转换，请打开楼层平面视图。", title=u"房间创建边界")
        return

    z = level_z(view)

    # 房间在图元选择里很难框选，改成「在房间里点一下」：可连续点，Esc 结束
    rooms = []
    seen = set()
    collector = FilteredElementCollector(doc, view.Id).OfCategory(BuiltInCategory.OST_Rooms)
    candidates = [element for element in collector if isinstance(element, Room) and element.Area > 0]
    log_line(u"[pick] 候选房间=%d" % len(candidates))
    while True:
        try:
            point = uidoc.Selection.PickPoint(u"在房间里点一下（可连续点多个房间，Esc 结束）")
        except Exception:
            break
        hit = None
        for room in candidates:
            try:
                if room.IsPointInRoom(point):
                    hit = room
                    break
            except Exception:
                continue
        if hit is None:
            log_line(u"[pick] 该点不在任何已放置房间里：%s" % point)
            continue
        if hit.Id.IntegerValue in seen:
            continue
        seen.add(hit.Id.IntegerValue)
        rooms.append(hit)
    if not rooms:
        log_line(u"[pick] 没有选中房间")
        forms.alert(u"没有选中任何房间。请打开楼层平面，在房间内部点一下再确认。", title=u"房间创建边界")
        return

    existing = existing_segments(doc, view)
    log_line(u"[start] 视图=%s 房间=%d 现有多段=%d" % (view.Name, len(rooms), len(existing)))

    created_total = 0
    failures = []
    transaction = Transaction(doc, u"房间识别边界")
    transaction.Start()
    transaction.GetFailureHandlingOptions().SetFailuresPreprocessor(QuietPreprocessor())
    try:
        plane = Plane.CreateByNormalAndOrigin(XYZ(0, 0, 1), XYZ(0, 0, z))
        sketch_plane = SketchPlane.Create(doc, plane)
        for room in rooms:
            try:
                pairs = room_curves(room, z)
            except Exception:
                failures.append(u"%s：取边界失败" % room.Id)
                continue
            missing = []
            for pair in pairs:
                if any(same_segment(pair, old, TOL_MM * MM_TO_FT) for old in existing):
                    continue
                if any(same_segment(pair, item, TOL_MM * MM_TO_FT) for item in missing):
                    continue
                missing.append(pair)
            log_line(u"房间ID:%s,边界线总计:%d,缺失:%d" % (room.Id, len(pairs), len(missing)))
            if not missing:
                log_line(u"房间边界已创建房间分割线,房间ID:%s" % room.Id)
                continue
            array = CurveArray()
            for pair in missing:
                array.Append(curve_from_pair(doc, pair, z))
            try:
                doc.Create.NewRoomBoundaryLines(sketch_plane, array, view)
                created_total += len(missing)
                existing.extend(missing)
                log_line(u"成功创建:%d,房间ID:%s" % (len(missing), room.Id))
            except Exception:
                failures.append(u"%s：%s" % (room.Id, traceback.format_exc().splitlines()[-1]))
        if created_total:
            transaction.Commit()
        else:
            transaction.RollBack()
    except Exception:
        try:
            transaction.RollBack()
        except Exception:
            pass
        failures.append(traceback.format_exc().splitlines()[-1])

    message = u"成功转换房间总计:%d" % created_total
    if failures:
        message += u"\n失败 %d 个：%s" % (len(failures), failures[0])
    log_line(u"[done] %s" % message.replace(u"\n", u" | "))
    forms.alert(message, title=u"房间创建边界")


try:
    main()
except Exception:
    forms.alert(traceback.format_exc()[-900:], title=u"房间创建边界")
