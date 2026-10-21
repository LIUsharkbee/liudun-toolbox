# -*- coding: utf-8 -*-
"""平台深度检查

针对警告：“平台深度小于梯段宽度。”

做法：
  1. 遍历项目里所有楼梯（Stairs）；
  2. 取每条梯段的实际宽度 ActualRunWidth，以及梯段的行进方向；
  3. 把每个平台的轮廓（GetFootprintBoundary）投影到行进方向上，得到平台深度；
  4. 深度 < 梯段宽度就报出来，并给出“差多少 mm”；
  5. 选中结果里的 ID 可以一键定位，改的时候在“编辑楼梯”里把平台外边缘往外拖对应距离即可。

说明：自动平台（IsAutomaticLanding）无法通过 API 改边界，工具只做测量和定位。
"""

from __future__ import print_function

import traceback

from Autodesk.Revit.DB import ElementId, FilteredElementCollector
from Autodesk.Revit.DB.Architecture import Stairs, StairsLanding

from pyrevit import revit, forms, script

doc = revit.doc
output = script.get_output()

FT_TO_MM = 304.8
TOL_MM = 1.0


def safe_call(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def safe_get(obj, name, default=None):
    try:
        return getattr(obj, name)
    except Exception:
        return default


def to_list(collection):
    if collection is None:
        return []
    out = []
    try:
        for item in collection:
            out.append(item)
    except Exception:
        return out
    return out


def as_elements(items):
    """GetStairsRuns / GetStairsLandings 返回的是 ElementId 集合，这里统一成元素。"""
    out = []
    for item in to_list(items):
        if isinstance(item, ElementId):
            elem = safe_call(lambda i=item: doc.GetElement(i), None)
            if elem is not None:
                out.append(elem)
        elif item is not None:
            out.append(item)
    return out


def run_direction(run):
    location = safe_get(run, "Location", None)
    curve = safe_get(location, "Curve", None) if location is not None else None
    if curve is None:
        return None
    p0 = safe_call(lambda: curve.GetEndPoint(0), None)
    p1 = safe_call(lambda: curve.GetEndPoint(1), None)
    if p0 is None or p1 is None:
        return None
    dx = p1.X - p0.X
    dy = p1.Y - p0.Y
    length = (dx * dx + dy * dy) ** 0.5
    if length < 1e-9:
        return None
    return (dx / length, dy / length)


def footprint_points(landing):
    loop = safe_call(lambda: landing.GetFootprintBoundary(), None)
    if loop is None:
        return []
    points = []
    iterator = safe_call(lambda: loop.GetCurveLoopIterator(), None)
    if iterator is not None:
        while safe_call(lambda: iterator.MoveNext(), False):
            curve = safe_get(iterator, "Current", None)
            if curve is None:
                continue
            p0 = safe_call(lambda: curve.GetEndPoint(0), None)
            p1 = safe_call(lambda: curve.GetEndPoint(1), None)
            if p0 is not None:
                points.append(p0)
            if p1 is not None:
                points.append(p1)
    return points


def depth_along(points, direction):
    if not points or direction is None:
        return None
    dx, dy = direction
    values = [p.X * dx + p.Y * dy for p in points]
    if not values:
        return None
    return (max(values) - min(values)) * FT_TO_MM


def bbox_min_side(landing):
    box = safe_call(lambda: landing.get_BoundingBox(None), None)
    if box is None:
        return None
    return min(box.Max.X - box.Min.X, box.Max.Y - box.Min.Y) * FT_TO_MM


def inspect_stairs(stairs):
    runs = as_elements(safe_call(lambda: stairs.GetStairsRuns(), []))
    landings = as_elements(safe_call(lambda: stairs.GetStairsLandings(), []))

    run_rows = []
    for run in runs:
        width = safe_call(lambda r=run: r.ActualRunWidth, 0.0) * FT_TO_MM
        run_rows.append({
            "run": run,
            "eid": safe_call(lambda r=run: r.Id.IntegerValue, -1),
            "width": width,
            "dir": run_direction(run),
        })

    if not run_rows:
        return []

    widest = max(r["width"] for r in run_rows)
    width_desc = u"、".join(u"{0:.0f}".format(float(r["width"])) for r in run_rows)
    results = []

    for landing in landings:
        points = footprint_points(landing)
        projections = []
        for run in run_rows:
            if run["dir"] is None or not points:
                continue
            depth = depth_along(points, run["dir"])
            if depth is not None:
                projections.append((depth, run))

        best = None
        for depth, run in projections:
            if best is None or depth < best["depth"]:
                best = {"depth": depth, "run": run}

        box = safe_call(lambda l=landing: l.get_BoundingBox(None), None)
        bbox_x = (box.Max.X - box.Min.X) * FT_TO_MM if box is not None else None
        bbox_y = (box.Max.Y - box.Min.Y) * FT_TO_MM if box is not None else None

        if best is None:
            depth = bbox_min_side(landing)
            best = {"depth": depth if depth is not None else 0.0, "run": None}

        width = widest
        if best["run"] is not None:
            width = best["run"]["width"]

        shortfall = width - best["depth"]
        results.append({
            "stairs": stairs,
            "landing": landing,
            "eid": safe_call(lambda l=landing: l.Id.IntegerValue, -1),
            "depth": best["depth"],
            "width": width,
            "shortfall": shortfall,
            "auto": bool(safe_call(lambda l=landing: l.IsAutomaticLanding, False)),
            "flag": shortfall > TOL_MM,
            "bbox": (bbox_x, bbox_y),
            "width_desc": width_desc,
            "proj": u" / ".join(u"{0:.0f}".format(float(d)) for d, _ in projections) or u"-",
        })
    return results


def main():
    if doc is None:
        forms.alert(u"请先打开一个项目。", exitscript=True)

    stairs_list = list(FilteredElementCollector(doc).OfClass(Stairs))
    if not stairs_list:
        forms.alert(u"当前项目里没有楼梯。")
        return

    rows = []
    data = []
    for stairs in stairs_list:
        for item in inspect_stairs(stairs):
            data.append(item)
            bbox = item.get("bbox") or (None, None)
            bbox_text = u"{0:.0f}×{1:.0f}".format(float(bbox[0] or 0), float(bbox[1] or 0))
            rows.append([
                output.linkify(item["landing"].Id),
                output.linkify(stairs.Id),
                bbox_text,
                item["width_desc"],
                item["proj"],
                u"深度不足" if item["flag"] else u"正常",
                u"自动" if item["auto"] else u"草图",
            ])

    output.print_md(u"## 平台深度检查")
    bad = [x for x in data if x["flag"]]
    output.print_md(u"共 {0} 个平台，深度不足 {1} 个。".format(len(data), len(bad)))

    if rows:
        output.print_table(
            rows[:200],
            title=u"全部平台（外框为该平台的平面包围盒；投影深为沿各梯段方向的长度）",
            columns=[u"平台 ID", u"楼梯 ID", u"外框(mm)", u"梯段宽(mm)",
                     u"投影深(mm)", u"判定", u"类型"])

    if bad:
        output.print_md(
            u"\n**怎么改**：选中楼梯 → 修改 → 编辑楼梯 → 选中平台 → 把外边缘沿行进方向"
            u"往外拖「还差」那么多（草图平台可改）；自动平台要到楼梯类型里调整，"
            u"或把平台改成草图平台。")
    else:
        output.print_md(u"\n没有发现判定为深度不足的平台；如果 Revit 仍报警，"
                        u"把上表里对应平台那一行发我，按外框和投影深重新对口径。")

    if bad:
        picked = forms.SelectFromList.show(
            [u"{0}  差 {1:.0f} mm".format(x["eid"], float(x["shortfall"])) for x in bad],
            title=u"选中并定位", multiselect=False, button_name=u"定位")
        if picked:
            index = [u"{0}  差 {1:.0f} mm".format(x["eid"], float(x["shortfall"]))
                     for x in bad].index(picked)
            target = bad[index]
            try:
                revit.get_selection().set_to([target["landing"]])
                revit.uidoc.ShowElements(target["landing"].Id)
            except Exception as ex:
                output.print_md(u"定位失败：{0}".format(ex))


try:
    main()
except Exception:
    output.print_md(u"脚本出错：\n```\n{0}\n```".format(traceback.format_exc()))
    forms.alert(u"脚本出错，详见输出窗口。", title=u"平台深度检查")
