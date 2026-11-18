# -*- coding: utf-8 -*-
"""轴网生成工具 —— 逐根点选版：点一根轴线，立刻建一根轴并把那根 CAD 线盖掉。"""

import math
import traceback

import clr
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")
clr.AddReference("System")

from Autodesk.Revit.DB import (
    BuiltInParameter, Color, ElementId, FilteredElementCollector, Grid,
    ImportInstance, Line, OverrideGraphicSettings, Transaction, XYZ,
)
from Autodesk.Revit.Exceptions import OperationCanceledException
from System.Collections.Generic import List as DotNetList
from System.Windows import Window as WpfWindow, WindowState
from System.Windows.Markup import XamlReader

from pyrevit import forms, revit

doc = revit.doc
uidoc = revit.uidoc
app = doc.Application

FT_TO_MM = 304.8
MM_TO_FT = 1.0 / 304.8

LOG_PATH = r"C:\Users\cyl\Documents\deepseek-harness\default-workspace\_轴网生成日志.txt"


def log_line(text):
    try:
        handle = open(LOG_PATH, "ab")
        try:
            handle.write((u"%s\n" % text).encode("utf-8"))
        finally:
            handle.close()
    except Exception:
        pass


def to_float(text, fallback):
    try:
        return float((u"%s" % text).strip())
    except Exception:
        return fallback


# ---------------------------------------------------------------- CAD 线段
def cad_instances():
    result = []
    collector = FilteredElementCollector(doc, doc.ActiveView.Id).OfClass(ImportInstance)
    for element in collector:
        try:
            if element.IsLinked:
                continue
        except Exception:
            pass
        result.append(element)
    return result


def layer_of(obj):
    try:
        style_id = obj.GraphicsStyleId
        if style_id is None or style_id.IntegerValue < 0:
            return u""
        style = doc.GetElement(style_id)
        if style is None:
            return u""
        category = style.GraphicsStyleCategory
        if category is None:
            return u""
        return u"%s" % category.Name
    except Exception:
        return u""


def collect_segments():
    segments = []
    options = app.Create.NewGeometryOptions()
    options.ComputeReferences = False
    options.DetailLevel = doc.ActiveView.DetailLevel

    def walk(obj):
        try:
            if hasattr(obj, "GetInstanceGeometry"):
                for child in obj.GetInstanceGeometry():
                    walk(child)
                return
            if hasattr(obj, "Tessellate"):
                points = obj.Tessellate()
                count = len(points)
                for index in range(count - 1):
                    segments.append((points[index], points[index + 1]))
        except Exception:
            pass

    for imported in cad_instances():
        try:
            transform = imported.GetTransform()
        except Exception:
            transform = None
        try:
            geometry = imported.get_Geometry(options)
        except Exception:
            continue
        if geometry is None:
            continue
        before = len(segments)
        for item in geometry:
            walk(item)
        if transform is not None:
            for index in range(before, len(segments)):
                start, end = segments[index]
                moved = (transform.OfPoint(start), transform.OfPoint(end))
                segments[index] = (start, end, moved)
        else:
            for index in range(before, len(segments)):
                start, end = segments[index]
                segments[index] = (start, end, (start, end))
    return segments


def point_segment_distance(point, start, end):
    ax, ay = start.X, start.Y
    bx, by = end.X, end.Y
    px, py = point.X, point.Y
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    if length_sq < 1e-12:
        return math.sqrt((px - ax) ** 2 + (py - ay) ** 2)
    t = ((px - ax) * dx + (py - ay) * dy) / length_sq
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    cx, cy = ax + t * dx, ay + t * dy
    return math.sqrt((px - cx) ** 2 + (py - cy) ** 2)



def merge_collinear(segments, tol_mm=80.0):
    """把同一位置上的碎线（虚线、间断线）合并成整条线，返回 [(start, end)]。"""
    tol = tol_mm * MM_TO_FT
    vertical = []
    horizontal = []
    for row in segments:
        start, end = row[0], row[1]
        dx = end.X - start.X
        dy = end.Y - start.Y
        if abs(dx) < 1e-9 and abs(dy) < 1e-9:
            continue
        if abs(dx) <= abs(dy):
            key = (start.X + end.X) * 0.5
            low = min(start.Y, end.Y)
            high = max(start.Y, end.Y)
            bucket = vertical
        else:
            key = (start.Y + end.Y) * 0.5
            low = min(start.X, end.X)
            high = max(start.X, end.X)
            bucket = horizontal

        target = None
        for row in bucket:
            if abs(row[0] - key) <= tol:
                target = row
                break
        if target is None:
            bucket.append([key, low, high])
        else:
            if low < target[1]:
                target[1] = low
            if high > target[2]:
                target[2] = high

    merged = []
    for key, low, high in vertical:
        merged.append((XYZ(key, low, 0.0), XYZ(key, high, 0.0)))
    for key, low, high in horizontal:
        merged.append((XYZ(low, key, 0.0), XYZ(high, key, 0.0)))
    return merged

def nearest_segment(point, segments):
    best = None
    best_distance = None
    for start_point, end_point in segments:
        distance = point_segment_distance(point, start_point, end_point)
        if best_distance is None or distance < best_distance:
            best_distance = distance
            best = (start_point, end_point)
    return best, best_distance


# ---------------------------------------------------------------- 轴号与命名
def existing_names():
    names = set()
    for element in FilteredElementCollector(doc).OfClass(Grid):
        try:
            names.add((u"%s" % element.Name).upper())
        except Exception:
            pass
    return names


def unique_name(name, used):
    candidate = name
    index = 2
    while candidate.upper() in used:
        candidate = u"%s(%d)" % (name, index)
        index += 1
    used.add(candidate.upper())
    return candidate


def set_grid_name(grid, name):
    try:
        grid.Name = name
        return True
    except Exception:
        pass
    try:
        parameter = grid.get_Parameter(BuiltInParameter.DATUM_TEXT)
        if parameter is not None and not parameter.IsReadOnly:
            parameter.Set(name)
            return True
    except Exception:
        pass
    return False


def letter_name(index):
    letters = u"ABCDEFGHJKLMNPQRSTUVWXYZ"
    if index < len(letters):
        return letters[index]
    return u"%s%d" % (letters[index % len(letters)], index // len(letters) + 1)


def make_mask(segment_start, segment_end):
    """沿这根线画一条白色粗线，把它盖掉。"""
    try:
        dx = segment_end.X - segment_start.X
        dy = segment_end.Y - segment_start.Y
        length = math.sqrt(dx * dx + dy * dy)
        offset_x = 0.0
        offset_y = 0.0
        if length > 1e-6:
            offset_x = -dy / length * (60.0 * MM_TO_FT)
            offset_y = dx / length * (60.0 * MM_TO_FT)
        line = Line.CreateBound(
            XYZ(segment_start.X + offset_x, segment_start.Y + offset_y, 0.0),
            XYZ(segment_end.X + offset_x, segment_end.Y + offset_y, 0.0))
        detail = doc.Create.NewDetailCurve(doc.ActiveView, line)
        if detail is None:
            return None
        settings = OverrideGraphicSettings()
        settings.SetProjectionLineColor(Color(255, 255, 255))
        settings.SetProjectionLineWeight(10)
        doc.ActiveView.SetElementOverrides(detail.Id, settings)
        return detail.Id
    except Exception:
        log_line(u"mask failed: %s" % traceback.format_exc()[-200:])
        return None


def clear_masks(state):
    ids = DotNetList[ElementId]()
    for mask_id in state.get("mask_ids") or []:
        ids.Add(mask_id)
    if ids.Count == 0:
        return 0
    count = ids.Count
    transaction = Transaction(doc, u"清除遮盖线")
    transaction.Start()
    try:
        doc.Delete(ids)
        transaction.Commit()
    except Exception:
        transaction.RollBack()
        return 0
    state["mask_ids"] = []
    return count


# ---------------------------------------------------------------- 接口
def pick_and_create(kind, state):
    rows = collect_segments()
    if not rows:
        forms.alert(u"当前视图里没有可用的 CAD 线段。\n"
                    u"请确认图纸是「导入」而不是链接，并且没有被隐藏。",
                    title=u"轴网生成工具")
        return

    raw_pairs = [(row[0], row[1]) for row in rows]
    moved_pairs = []
    for row in rows:
        if len(row) >= 4:
            moved_pairs.append((row[2], row[3]))
        else:
            moved_pairs.append((row[0], row[1]))
    merged_raw = merge_collinear(raw_pairs)
    merged_moved = merge_collinear(moved_pairs)


    try:
        point = uidoc.Selection.PickPoint(u"点选一根轴线（Esc 结束）")
    except OperationCanceledException:
        return
    except Exception as error:
        forms.alert(u"拾取失败：%s" % error, title=u"轴网生成工具")
        return

    best_raw, distance_raw = nearest_segment(point, merged_raw)
    best_moved, distance_moved = nearest_segment(point, merged_moved)
    if distance_raw is None:
        distance_raw = 1e18
    if distance_moved is None:
        distance_moved = 1e18
    if distance_moved <= distance_raw:
        best, distance = best_moved, distance_moved
    else:
        best, distance = best_raw, distance_raw

    # 容差放宽到 5m：先保证能吸附到最近的那条线，再由用户看结果
    if best is None or distance is None or distance > 5000.0 * MM_TO_FT:
        forms.alert(u"没点到线上（偏离 %.0f mm）。\n"
                    u"放大图纸，点在轴线上再试。" % ((distance or 0.0) * FT_TO_MM),
                    title=u"轴网生成工具")
        return

    start, end = best
    dx = end.X - start.X
    dy = end.Y - start.Y
    is_vertical = abs(dx) < abs(dy)

    margin = to_float(state.get("margin_text"), 800.0) * MM_TO_FT
    transaction = Transaction(doc, u"生成轴线")
    transaction.Start()
    try:
        if is_vertical:
            x = (start.X + end.X) * 0.5
            y1 = min(start.Y, end.Y) - margin
            y2 = max(start.Y, end.Y) + margin
            grid = Grid.Create(doc, Line.CreateBound(XYZ(x, y1, 0.0), XYZ(x, y2, 0.0)))
            position = x * FT_TO_MM
        else:
            y = (start.Y + end.Y) * 0.5
            x1 = min(start.X, end.X) - margin
            x2 = max(start.X, end.X) + margin
            grid = Grid.Create(doc, Line.CreateBound(XYZ(x1, y, 0.0), XYZ(x2, y, 0.0)))
            position = y * FT_TO_MM

        transaction.Commit()
    except Exception as error:
        transaction.RollBack()
        forms.alert(u"创建轴线失败：%s" % error, title=u"轴网生成工具")
        return

    try:
        transaction = Transaction(doc, u"轴号双头")
        transaction.Start()
        set_double_bubble(grid)
        transaction.Commit()
    except Exception:
        try:
            transaction.RollBack()
        except Exception:
            pass

    items = state.setdefault("vertical" if is_vertical else "horizontal", [])
    base_name = u"%d" % (len(items) + 1) if is_vertical else letter_name(len(items))
    final_name = rename_grid(grid, base_name)

    record = {"id": grid.Id, "name": final_name, "position": position,
              "vertical": is_vertical}
    state.setdefault("records", []).append(record)

    if state.get("mask_on", True):
        mask_id = make_mask(start, end)
        if mask_id is not None:
            state.setdefault("mask_ids", []).append(mask_id)

    try:
        selected = DotNetList[ElementId]()
        selected.Add(grid.Id)
        uidoc.Selection.SetElementIds(selected)
        uidoc.ShowElements(grid.Id)
    except Exception:
        pass

    log_line(u"pick %s %.1f name=%s" % (u"V" if is_vertical else u"H", position, final_name))


def pick_many(kind, state):
    """连续拾取：点一根建一根，按 Esc 结束再回窗口。"""
    rows = collect_segments()
    if not rows:
        forms.alert(u"当前视图里没有可用的 CAD 线段。\n"
                    u"请确认图纸是「导入」而不是链接，并且没有被隐藏。",
                    title=u"轴网生成工具")
        return
    raw_pairs = [(row[0], row[1]) for row in rows]
    moved_pairs = []
    for row in rows:
        if len(row) >= 4:
            moved_pairs.append((row[2], row[3]))
        else:
            moved_pairs.append((row[0], row[1]))
    merged_raw = merge_collinear(raw_pairs)
    merged_moved = merge_collinear(moved_pairs)

    created_count = 0
    while True:
        try:
            point = uidoc.Selection.PickPoint(u"点选轴线（连续点，Esc 结束）")
        except OperationCanceledException:
            break
        except Exception as error:
            forms.alert(u"拾取失败：%s" % error, title=u"轴网生成工具")
            break

        pair = None
        distance = None
        for candidates in (merged_moved, merged_raw):
            best, dist = nearest_segment(point, candidates)
            if best is None:
                continue
            if distance is None or dist < distance:
                pair, distance = best, dist

        if pair is None or distance is None or distance > 5000.0 * MM_TO_FT:
            continue

        start, end = pair
        dx = end.X - start.X
        dy = end.Y - start.Y
        is_vertical = abs(dx) < abs(dy)
        margin = to_float(state.get("margin_text"), 800.0) * MM_TO_FT

        transaction = Transaction(doc, u"生成轴线")
        transaction.Start()
        try:
            if is_vertical:
                x = (start.X + end.X) * 0.5
                grid = Grid.Create(doc, Line.CreateBound(
                    XYZ(x, min(start.Y, end.Y) - margin, 0.0),
                    XYZ(x, max(start.Y, end.Y) + margin, 0.0)))
                position = x * FT_TO_MM
            else:
                y = (start.Y + end.Y) * 0.5
                grid = Grid.Create(doc, Line.CreateBound(
                    XYZ(min(start.X, end.X) - margin, y, 0.0),
                    XYZ(max(start.X, end.X) + margin, y, 0.0)))
                position = y * FT_TO_MM
            transaction.Commit()
        except Exception as error:
            try:
                transaction.RollBack()
            except Exception:
                pass
            forms.alert(u"建轴失败：%s" % error, title=u"轴网生成工具")
            continue

        try:
            transaction = Transaction(doc, u"轴号双头")
            transaction.Start()
            set_double_bubble(grid)
            transaction.Commit()
        except Exception:
            pass

        items = state.setdefault("vertical" if is_vertical else "horizontal", [])
        base_name = u"%d" % (len(items) + 1) if is_vertical else letter_name(len(items))
        name = rename_grid(grid, base_name)

        state.setdefault("records", []).append(
            {"id": grid.Id, "name": name, "position": position, "vertical": is_vertical})
        state.setdefault("vertical" if is_vertical else "horizontal", []).append(
            (u"v" if is_vertical else u"h", name, position))

        if state.get("mask_on", True):
            mask_id = make_mask(start, end)
            if mask_id is not None:
                state.setdefault("mask_ids", []).append(mask_id)

        created_count += 1
        state["status"] = u"已连续建 %d 根，Esc 回窗口" % created_count
        log_line(u"pick %s %.1f name=%s" % (u"V" if is_vertical else u"H", position, name))

    rebuild_lists(state)
    state["status"] = u"本次连续拾取完成：新建 %d 根" % created_count


def tidy_grids(state):
    """把选中的轴网（没选就取当前视图全部）按统一外伸长度重建，让轴号自动对齐成一行。"""
    ids = revit.uidoc.Selection.GetElementIds()
    grids = []
    for element_id in ids:
        element = doc.GetElement(element_id)
        if isinstance(element, Grid):
            grids.append(element)
    if not grids:
        for element in FilteredElementCollector(doc, doc.ActiveView.Id).OfClass(Grid):
            grids.append(element)
    if not grids:
        forms.alert(u"当前视图里没有轴线。", title=u"轴网生成工具")
        return

    rows = []
    for grid in grids:
        try:
            curve = grid.Curve
            start = curve.GetEndPoint(0)
            end = curve.GetEndPoint(1)
            name = u"%s" % grid.Name
        except Exception:
            continue
        dx = end.X - start.X
        dy = end.Y - start.Y
        points = [start, end]
        if abs(dx) <= abs(dy):
            rows.append({"vertical": True, "position": (start.X + end.X) * 0.5,
                         "name": name, "id": grid.Id, "points": points})
        else:
            rows.append({"vertical": False, "position": (start.Y + end.Y) * 0.5,
                         "name": name, "id": grid.Id, "points": points})

    if not rows:
        forms.alert(u"选中的轴线取不到定位线。", title=u"轴网生成工具")
        return

    margin = to_float(state.get("margin_text"), 800.0) * MM_TO_FT

    # 用「所有轴线端点的包围盒」定统一长度，而不是只用轴线位置——
    # 只取位置会让另一方向的跨度变成 0，轴就会缩成一小段。
    x_values = []
    y_values = []
    for row in rows:
        for point in row["points"]:
            x_values.append(point.X)
            y_values.append(point.Y)
    if not x_values:
        forms.alert(u"取不到轴线的端点。", title=u"轴网生成工具")
        return
    x_lo = min(x_values) - margin
    x_hi = max(x_values) + margin
    y_lo = min(y_values) - margin
    y_hi = max(y_values) + margin

    # 跨度太小时兜底成 6 米，避免缩成一点
    min_span = 6000.0 * MM_TO_FT
    if (x_hi - x_lo) < min_span:
        center = (x_lo + x_hi) * 0.5
        x_lo, x_hi = center - min_span * 0.5, center + min_span * 0.5
    if (y_hi - y_lo) < min_span:
        center = (y_lo + y_hi) * 0.5
        y_lo, y_hi = center - min_span * 0.5, center + min_span * 0.5

    transaction = Transaction(doc, u"规整轴网")
    transaction.Start()
    try:
        old_ids = DotNetList[ElementId]()
        for row in rows:
            old_ids.Add(row["id"])
        doc.Delete(old_ids)

        created = 0
        for row in rows:
            if row["vertical"]:
                x = row["position"]
                line = Line.CreateBound(
                    XYZ(x, y_lo, 0.0), XYZ(x, y_hi, 0.0))
            else:
                y = row["position"]
                line = Line.CreateBound(
                    XYZ(x_lo, y, 0.0), XYZ(x_hi, y, 0.0))
            grid = Grid.Create(doc, line)
            set_double_bubble(grid)
            try:
                if not grid.get_Parameter(BuiltInParameter.GRID_END_SEGMENTS_LENGTH).IsReadOnly:
                    grid.get_Parameter(BuiltInParameter.GRID_END_SEGMENTS_LENGTH).Set(margin)
            except Exception:
                pass
            set_grid_name(grid, row["name"])
            created += 1
        transaction.Commit()
    except Exception as error:
        try:
            transaction.RollBack()
        except Exception:
            pass
        forms.alert(u"规整失败：%s" % error, title=u"轴网生成工具")
        return

    state["records"] = []
    state["mask_ids"] = state.get("mask_ids") or []
    rebuild_lists(state)
    state["status"] = u"已规整 %d 根轴：轴号统一到同一端线，双头显示" % created
    log_line(u"tidy grids %d" % created)


def rebuild_lists(state):
    vertical = []
    horizontal = []
    for record in state.get("records") or []:
        row = (u"v" if record["vertical"] else u"h", record["name"], record["position"])
        if record["vertical"]:
            vertical.append(row)
        else:
            horizontal.append(row)
    vertical.sort(key=lambda item: item[2])
    horizontal.sort(key=lambda item: item[2])
    state["vertical"] = vertical
    state["horizontal"] = horizontal


def undo_last(state):
    records = state.get("records") or []
    if not records:
        return
    record = records.pop()
    mask_ids = state.get("mask_ids") or []
    to_delete = DotNetList[ElementId]()
    to_delete.Add(record["id"])
    if mask_ids:
        to_delete.Add(mask_ids.pop())
    transaction = Transaction(doc, u"撤销轴线")
    transaction.Start()
    try:
        doc.Delete(to_delete)
        transaction.Commit()
    except Exception:
        transaction.RollBack()
    rebuild_lists(state)


def rename_grid(grid, base_name):
    """在独立事务里给轴线改名；名字冲突时自动加后缀，最终仍失败则返回原名称。"""
    used = existing_names()
    try:
        used.remove((u"%s" % grid.Name).upper())
    except Exception:
        pass
    target = unique_name(base_name, used)
    transaction = Transaction(doc, u"重命名轴线")
    transaction.Start()
    try:
        ok = set_grid_name(grid, target)
        transaction.Commit()
    except Exception:
        transaction.RollBack()
        ok = False
    if not ok:
        try:
            return u"%s" % grid.Name
        except Exception:
            return target
    return target


def set_double_bubble(grid):
    """轴线两端都显示轴号气泡。"""
    for parameter_id in (BuiltInParameter.GRID_BUBBLE_END_1,
                         BuiltInParameter.GRID_BUBBLE_END_2,
                         BuiltInParameter.DATUM_BUBBLE_END_1,
                         BuiltInParameter.DATUM_BUBBLE_END_2):
        try:
            parameter = grid.get_Parameter(parameter_id)
            if parameter is None or parameter.IsReadOnly:
                continue
            parameter.Set(1)
        except Exception:
            pass


def rename_all(state):
    records = state.get("records") or []
    if not records:
        forms.alert(u"还没有生成任何轴线。", title=u"轴网生成工具")
        return
    vertical = sorted([r for r in records if r["vertical"]], key=lambda r: r["position"])
    horizontal = sorted([r for r in records if not r["vertical"]], key=lambda r: r["position"])
    used = existing_names()
    transaction = Transaction(doc, u"重排轴号")
    transaction.Start()
    try:
        for index, record in enumerate(vertical):
            grid = doc.GetElement(record["id"])
            if grid is not None:
                set_double_bubble(grid)
            if grid is None:
                continue
            new_name = unique_name(u"%d" % (index + 1), used)
            set_grid_name(grid, new_name)
            record["name"] = new_name
        for index, record in enumerate(horizontal):
            grid = doc.GetElement(record["id"])
            if grid is not None:
                set_double_bubble(grid)
            if grid is None:
                continue
            new_name = unique_name(letter_name(index), used)
            set_grid_name(grid, new_name)
            record["name"] = new_name
        transaction.Commit()
    except Exception:
        transaction.RollBack()
    state["vertical"] = [(u"v", r["name"], r["position"]) for r in vertical]
    state["horizontal"] = [(u"h", r["name"], r["position"]) for r in horizontal]
    forms.alert(u"已重排：竖轴 %d 根（1、2、3…）、横轴 %d 根（A、B、C…）。"
                % (len(vertical), len(horizontal)), title=u"轴网生成工具")


def toggle_cad(state):
    imports = cad_instances()
    ids = DotNetList[ElementId]()
    for imported in imports:
        ids.Add(imported.Id)
    if ids.Count == 0:
        forms.alert(u"当前视图没有导入的 CAD。", title=u"轴网生成工具")
        return
    transaction = Transaction(doc, u"显示/隐藏 CAD")
    transaction.Start()
    try:
        if state.get("cad_hidden"):
            doc.ActiveView.UnhideElements(ids)
            state["cad_hidden"] = False
        else:
            doc.ActiveView.HideElements(ids)
            state["cad_hidden"] = True
        transaction.Commit()
    except Exception:
        transaction.RollBack()


# ---------------------------------------------------------------- 窗口
XAML = u"""
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="轴网生成工具 · 逐根点选" Height="620" Width="600"
        WindowStartupLocation="CenterScreen" ResizeMode="CanResize">
  <ScrollViewer VerticalScrollBarVisibility="Auto" HorizontalScrollBarVisibility="Auto">
    <StackPanel Margin="16">
      <TextBlock Text="逐根点选：点一根轴线 → 立刻建一根轴，并把那根 CAD 线盖成白色（看起来就消失了）。"
                 TextWrapping="Wrap" Margin="0,0,0,4"/>
      <TextBlock Text="窗口会先收起，Esc 可中断；拾取时请放大到能看清轴线。"
                 TextWrapping="Wrap" Foreground="#FF5A6B85" Margin="0,0,0,12"/>

      <Grid Margin="0,0,0,10">
        <Grid.ColumnDefinitions>
          <ColumnDefinition Width="*"/>
          <ColumnDefinition Width="*"/>
          <ColumnDefinition Width="*"/>
        </Grid.ColumnDefinitions>
        <Button x:Name="btnPickV" Grid.Column="0" Content="拾取竖轴" Height="36" Margin="0,0,6,0"/>
        <Button x:Name="btnPickH" Grid.Column="1" Content="拾取横轴" Height="36" Margin="3,0,3,0"/>
        <Button x:Name="btnUndo" Grid.Column="2" Content="撤销上一根" Height="36" Margin="6,0,0,0"/>
      </Grid>

      <Grid Margin="0,0,0,10">
        <Grid.ColumnDefinitions>
          <ColumnDefinition Width="70"/>
          <ColumnDefinition Width="80"/>
          <ColumnDefinition Width="*"/>
          <ColumnDefinition Width="110"/>
        </Grid.ColumnDefinitions>
        <TextBlock Text="轴外伸" VerticalAlignment="Center"/>
        <TextBox x:Name="marginBox" Grid.Column="1" Text="800" Height="28"/>
        <CheckBox x:Name="maskBox" Grid.Column="2" Content="盖住已拾取的线" IsChecked="True"
                  VerticalAlignment="Center" Margin="8,0,0,0"/>
        <Button x:Name="btnCad" Grid.Column="3" Content="隐藏 CAD" Height="30"/>
      </Grid>

      <TextBlock x:Name="statText" Text="竖向 0 根 · 横向 0 根" Margin="0,0,0,2"/>
      <TextBlock x:Name="statusText" Text="" Foreground="#FF2E7D5B" Margin="0,0,0,6"/>
      <ListBox x:Name="resultList" Height="240" FontFamily="Consolas"/>

      <Grid Margin="0,10,0,0">
        <Grid.ColumnDefinitions>
          <ColumnDefinition Width="*"/>
          <ColumnDefinition Width="*"/>
          <ColumnDefinition Width="*"/>
          <ColumnDefinition Width="*"/>
        </Grid.ColumnDefinitions>
        <Button x:Name="btnRename" Grid.Column="0" Content="重排轴号" Height="34" Margin="0,0,5,0"/>
        <Button x:Name="btnClearMask" Grid.Column="1" Content="清除遮盖" Height="34" Margin="2,0,2,0"/>
        <Button x:Name="btnTest" Grid.Column="2" Content="测试建轴" Height="34" Margin="5,0,5,0"/>
        <Button x:Name="btnClose" Grid.Column="3" Content="完成" Height="34" Margin="2,0,0,0"/>
      </Grid>
      <Button x:Name="btnTidy" Content="规整轴号（选中/全部轴网：统一长度 + 双头对齐）"
              Height="34" Margin="0,8,0,0"/>
    </StackPanel>
  </ScrollViewer>
</Window>
"""


class GridPickWindow(object):
    """不用 shim 的窗口封装：直接 XamlReader 解析 + WPF 原生模态，保证拾取上下文干净。"""

    def __init__(self, state):
        self.state = state
        self.action = u"close"
        self.window = XamlReader.Parse(XAML)
        self.window.Title = u"轴网生成工具 · 逐根点选"
        self.margin_box = self.window.FindName("marginBox")
        self.mask_box = self.window.FindName("maskBox")
        self.stat_text = self.window.FindName("statText")
        self.result_list = self.window.FindName("resultList")
        self.cad_button = self.window.FindName("btnCad")
        self.status_text = self.window.FindName("statusText")

        self.window.FindName("btnPickV").Click += self._on_pick_v
        self.window.FindName("btnPickH").Click += self._on_pick_h
        self.window.FindName("btnUndo").Click += self._on_undo
        self.window.FindName("btnRename").Click += self._on_rename
        self.window.FindName("btnClearMask").Click += self._on_clear_mask
        self.cad_button.Click += self._on_toggle_cad
        self.window.FindName("btnTest").Click += self._on_test
        self.window.FindName("btnTidy").Click += self._on_tidy
        self.window.FindName("btnClose").Click += self._on_close

        self.cad_button.Content = u"显示 CAD" if state.get("cad_hidden") else u"隐藏 CAD"
        self.margin_box.Text = state.get("margin_text") or u"800"
        self.mask_box.IsChecked = state.get("mask_on", True)
        self._refresh()

    def _refresh(self):
        vertical = self.state.get("vertical") or []
        horizontal = self.state.get("horizontal") or []
        self.stat_text.Text = u"竖向 %d 根 · 横向 %d 根" % (len(vertical), len(horizontal))
        self.result_list.Items.Clear()
        for _, name, position in vertical:
            self.result_list.Items.Add(u"竖轴 %-6s %11.1f mm" % (name, position))
        for _, name, position in horizontal:
            self.result_list.Items.Add(u"横轴 %-6s %11.1f mm" % (name, position))
        self.status_text.Text = self.state.get("status") or u""

    def _close_with(self, action):
        self.action = action
        self.state["margin_text"] = self.margin_box.Text
        self.state["mask_on"] = bool(self.mask_box.IsChecked)
        self.window.Close()

    def _on_pick_v(self, sender, args):
        self._close_with(u"pick_v")

    def _on_pick_h(self, sender, args):
        self._close_with(u"pick_h")

    def _on_undo(self, sender, args):
        self._close_with(u"undo")

    def _on_rename(self, sender, args):
        self._close_with(u"rename")

    def _on_clear_mask(self, sender, args):
        self._close_with(u"clear_mask")

    def _on_toggle_cad(self, sender, args):
        self._close_with(u"toggle_cad")

    def _on_test(self, sender, args):
        self._close_with(u"test")

    def _on_tidy(self, sender, args):
        self._close_with(u"tidy")

    def _on_close(self, sender, args):
        self._close_with(u"close")

    def show(self):
        window = self.window
        try:
            window.Topmost = True
            window.ShowInTaskbar = True
            window.WindowState = WindowState.Normal
        except Exception:
            pass
        try:
            window.ShowDialog()
        except Exception:
            log_line(u"show failed: %s" % traceback.format_exc()[-300:])
        return self.action


def create_test_grid(state):
    """在图纸附近建一根测试轴，用来确认「能不能建出来、能不能看见」。"""
    try:
        point = uidoc.Selection.PickPoint(u"点一个位置，在这里建一根测试轴线")
    except OperationCanceledException:
        return
    except Exception as error:
        forms.alert(u"拾取失败：%s" % error, title=u"轴网生成工具")
        return

    transaction = Transaction(doc, u"测试轴线")
    transaction.Start()
    try:
        grid = Grid.Create(doc, Line.CreateBound(
            XYZ(point.X, point.Y - 4000 * MM_TO_FT, 0.0),
            XYZ(point.X, point.Y + 4000 * MM_TO_FT, 0.0)))
        transaction.Commit()
    except Exception as error:
        transaction.RollBack()
        forms.alert(u"建轴失败：%s" % error, title=u"轴网生成工具")
        return

    try:
        transaction = Transaction(doc, u"测试轴号")
        transaction.Start()
        set_double_bubble(grid)
        transaction.Commit()
    except Exception:
        pass
    name = rename_grid(grid, u"T")
    state["status"] = u"测试轴已建：%s" % name
    try:
        selected = DotNetList[ElementId]()
        selected.Add(grid.Id)
        uidoc.Selection.SetElementIds(selected)
        uidoc.ShowElements(grid.Id)
    except Exception:
        pass
    log_line(u"test grid %s at %.1f" % (name, point.X * FT_TO_MM))


def main():
    log_line(u"== start ==")
    state = {"vertical": [], "horizontal": [], "records": [], "mask_ids": [],
             "cad_hidden": False, "mask_on": True, "margin_text": u"800",
             "status": u""}
    while True:
        try:
            window = GridPickWindow(state)
        except Exception:
            log_line(u"window build failed: %s" % traceback.format_exc())
            forms.alert(u"窗口创建失败：\n%s" % traceback.format_exc()[-600:],
                        title=u"轴网生成工具")
            break
        action = window.show()
        log_line(u"action=%s" % action)

        if action == u"pick_v":
            pick_many("v", state)
        elif action == u"pick_h":
            pick_many("h", state)
        elif action == u"undo":
            undo_last(state)
        elif action == u"rename":
            rename_all(state)
        elif action == u"clear_mask":
            count = clear_masks(state)
            state["status"] = u"已删除 %d 条遮盖线" % count
        elif action == u"toggle_cad":
            toggle_cad(state)
        elif action == u"tidy":
            tidy_grids(state)
        elif action == u"test":
            create_test_grid(state)
        else:
            break

    clear_masks(state)
    total = len(state.get("records") or [])
    if total:
        forms.alert(u"完成：共建 %d 根轴线。" % total, title=u"轴网生成工具")


try:
    main()
except Exception:
    log_line(traceback.format_exc())
    forms.alert(u"脚本出错：\n%s" % traceback.format_exc()[-800:], title=u"轴网生成工具")
