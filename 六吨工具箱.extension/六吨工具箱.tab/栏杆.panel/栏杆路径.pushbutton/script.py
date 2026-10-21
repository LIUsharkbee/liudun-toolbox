# -*- coding: utf-8 -*-
"""栏杆路径检查 / 修复

针对警告：“高亮显示的线重叠。这些线不能形成闭合的环。”
（栏杆扶手的路径草图里存在重复 / 重叠 / 折返的线段）

原理：
  1. Railing.GetPath() 取出路径（返回 IList<Curve>）；
  2. 把路径还原成折线顶点，去掉重合点、折返尖刺和共线多余点；
  3. 用 Railing.SetPath(CurveLoop) 把清理后的路径写回去（不删除重建，类型/主体保留）。

安全：
  * “只检查”模式完全只读；
  * “检查并修复”会修改模型，但每一步都是一个事务，Ctrl+Z 可撤销；
  * 含弧线 / 样条、或者清理后路径退化的栏杆会自动跳过。
"""

from __future__ import print_function

import codecs
import os
import traceback

from System import Environment
from System.Collections.Generic import List

from Autodesk.Revit.DB import (
    Curve,
    CurveLoop,
    ElementId,
    FilteredElementCollector,
    Line,
    TextNote,
    TextNoteOptions,
    TextNoteType,
    ViewPlan,
    XYZ,
)
from Autodesk.Revit.DB.Architecture import (
    Railing,
    StairsRun,
)

from pyrevit import revit, forms, script

doc = revit.doc
output = script.get_output()

TOL = 0.001          # 点重合容差（英尺，约 0.3 mm）
SIN_TOL = 1e-3       # 共线判断的 sin 阈值
MAX_ITER = 500


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


def dist(a, b):
    return a.DistanceTo(b)


def nearly_collinear(a, b, c):
    """a-b-c 三点是否共线（不要求 b 在中间，折返也算，才能合并重叠段）。"""
    ab = b - a
    ac = c - a
    lab = ab.GetLength()
    lac = ac.GetLength()
    if lab < 1e-9 or lac < 1e-9:
        return True
    return ab.CrossProduct(ac).GetLength() / (lab * lac) <= SIN_TOL


def dedupe(pts):
    out = []
    for p in pts:
        if not out or dist(out[-1], p) > TOL:
            out.append(p)
    return out


def clean_points(pts):
    """去掉重合点、折返尖刺、共线多余点。"""
    out = dedupe(pts)
    guard = 0
    while guard < MAX_ITER:
        guard += 1
        changed = False
        for i in range(1, len(out) - 1):
            # A-B-A 折返：B 和第二个 A 都能删
            if dist(out[i - 1], out[i + 1]) <= TOL:
                del out[i]
                changed = True
                break
            if nearly_collinear(out[i - 1], out[i], out[i + 1]):
                del out[i]
                changed = True
                break
        if not changed:
            break
    return dedupe(out)


def curves_of(path):
    """Railing.GetPath() 返回 IList<Curve>；CurveLoop 也兼容。"""
    if path is None:
        return []
    if safe_has(path, "GetCurveLoopIterator"):
        out = []
        try:
            it = path.GetCurveLoopIterator()
            while it.MoveNext():
                out.append(it.Current)
        except Exception:
            out = []
        if out:
            return out
    out = []
    try:
        for c in path:
            out.append(c)
        return out
    except Exception:
        pass
    count = safe_count(path)
    for i in range(count):
        try:
            out.append(path[i])
        except Exception:
            break
    return out


def safe_has(obj, name):
    try:
        getattr(obj, name)
        return True
    except Exception:
        return False


def safe_count(obj):
    try:
        return int(obj.Count)
    except Exception:
        return 0


def is_closed(curves):
    if len(curves) < 2:
        return False
    try:
        return dist(curves[0].GetEndPoint(0), curves[-1].GetEndPoint(1)) <= TOL
    except Exception:
        return False


def vertices_of(curves):
    pts = []
    for c in curves:
        p0 = c.GetEndPoint(0)
        p1 = c.GetEndPoint(1)
        if not pts:
            pts.append(p0)
            pts.append(p1)
            continue
        if dist(pts[-1], p0) <= TOL:
            pts.append(p1)
        else:
            pts.append(p0)
    return dedupe(pts)


def polyline_length(pts):
    total = 0.0
    for a, b in zip(pts, pts[1:]):
        total += dist(a, b)
    return total


def build_loop(pts, closed):
    seq = list(pts)
    if closed and len(seq) > 2 and dist(seq[0], seq[-1]) > TOL:
        seq.append(seq[0])
    curves = List[Curve]()
    for a, b in zip(seq, seq[1:]):
        if dist(a, b) <= TOL:
            continue
        curves.Add(Line.CreateBound(a, b))
    if curves.Count < 1:
        return None
    return CurveLoop.Create(curves)


def level_name(elem):
    def _get():
        level = doc.GetElement(elem.LevelId)
        return level.Name if level is not None else u""
    try:
        return _get()
    except Exception:
        return u""


def element_label(elem):
    if elem is None:
        return u""
    cat = safe_call(lambda: elem.Category.Name, u"") or u""
    name = safe_call(lambda: elem.Name, u"") or u""
    text = u"{0} {1}".format(cat, name).strip()
    return text


def resolve_host(host_id):
    """栏杆的 HostId 可能是梯段(StairsRun)，需要换成它所属的楼梯。"""
    if host_id is None:
        return None
    host = safe_call(lambda: doc.GetElement(host_id), None)
    if host is None:
        return None
    if isinstance(host, StairsRun):
        parent = safe_call(lambda: host.GetStairs(), None)
        if parent is not None:
            return parent
    return host


def analyze(railing, warned=False):
    result = {
        "railing": railing,
        "eid": railing.Id.IntegerValue,
        "type": u"",
        "level": level_name(railing),
        "segments": 0,
        "clean": 0,
        "drop_mm": 0.0,
        "closed": False,
        "fixable": False,
        "warned": warned,
        "horizontal": False,
        "host": u"",
        "problem": u"",
    }
    try:
        result["type"] = railing.Name
    except Exception:
        result["type"] = u""

    try:
        path = railing.GetPath()
    except Exception as ex:
        result["problem"] = u"读不到路径：{0}".format(ex)
        return result
    if path is None:
        result["problem"] = u"没有路径（可能是主体栏杆）"
        return result

    curves = curves_of(path)
    if not curves:
        result["problem"] = u"路径为空（{0} 段）".format(safe_count(path))
        return result

    result["segments"] = len(curves)
    result["closed"] = is_closed(curves)

    zs = []
    for c in curves:
        p0 = safe_call(lambda c=c: c.GetEndPoint(0), None)
        p1 = safe_call(lambda c=c: c.GetEndPoint(1), None)
        if p0 is not None:
            zs.append(p0.Z)
        if p1 is not None:
            zs.append(p1.Z)
    if zs:
        result["horizontal"] = (max(zs) - min(zs)) <= TOL

    host_id = safe_get(railing, "HostId", None) if safe_get(railing, "HasHost", False) else None
    if host_id is not None:
        host = resolve_host(host_id)
        result["host"] = element_label(host) or u"有主体"

    if any(not isinstance(c, Line) for c in curves):
        result["problem"] = u"路径含弧线/样条，不自动处理"
        return result

    pts = vertices_of(curves)
    if len(pts) < 2:
        result["problem"] = u"路径顶点不足"
        return result

    cleaned = clean_points(pts)
    result["clean"] = max(len(cleaned) - 1, 0)
    orig_len = sum(c.Length for c in curves)
    new_len = polyline_length(cleaned)
    result["drop_mm"] = (orig_len - new_len) * 304.8
    result["fixable"] = len(cleaned) >= 2

    zero = sum(1 for a, b in zip(pts, pts[1:]) if dist(a, b) <= TOL)
    if result["drop_mm"] > 0.5 or zero > 0:
        bits = []
        if zero:
            bits.append(u"{0} 段零长度".format(zero))
        if result["drop_mm"] > 0.5:
            bits.append(u"重叠 {0:.1f} mm".format(result["drop_mm"]))
        result["problem"] = u"，".join(bits)
    elif result["clean"] < result["segments"]:
        result["problem"] = u"有共线可合并段（几何未变）"
    return result


def fix(railing, closed):
    curves = curves_of(railing.GetPath())
    pts = vertices_of(curves)
    cleaned = clean_points(pts)
    if len(cleaned) < 2:
        return False, u"清理后路径退化"

    new_loop = build_loop(cleaned, closed)
    if new_loop is None:
        return False, u"新路径为空"

    # IsValidPathForRailing 比实际 SetPath 更严，只作提示，不拦截
    precheck = safe_call(lambda: Railing.IsValidPathForRailing(new_loop), None)
    try:
        with revit.Transaction(u"修复栏杆路径 {0}".format(railing.Id.IntegerValue)):
            railing.SetPath(new_loop)
    except Exception as ex:
        zs = []
        for c in curves:
            for idx in (0, 1):
                p = safe_call(lambda c=c, idx=idx: c.GetEndPoint(idx), None)
                if p is not None:
                    zs.append(p.Z)
        zr = (max(zs) - min(zs)) * 304.8 if zs else -1.0
        return False, (u"SetPath 被拒绝（段数 {0}，闭合 {1}，高差 {2:.1f} mm，"
                       u"预检={3}）：{4}").format(
            len(cleaned) - 1, closed, zr, precheck, ex)

    note = u"已重设路径（{0} 段 → {1} 段）".format(len(curves), max(len(cleaned) - 1, 0))
    if precheck is False:
        note += u"；注意：预检曾判为不合法，但写入成功"
    return True, note


def as_list(items):
    if items is None:
        return []
    out = []
    try:
        for it in items:
            out.append(it)
        return out
    except Exception:
        pass
    for i in range(safe_count(items)):
        try:
            out.append(items[i])
        except Exception:
            break
    return out


def warned_railings():
    """从当前警告里收集“线重叠”相关的图元 ID（含栏杆本身）。"""
    ids = set()
    try:
        warnings = as_list(doc.GetWarnings())
    except Exception:
        return ids
    for fm in warnings:
        desc = safe_call(lambda fm=fm: fm.GetDescriptionText(), u"") or u""
        if (u"重叠" not in desc) and ("overlap" not in desc.lower()):
            continue
        for eid in as_list(safe_call(lambda fm=fm: fm.GetFailingElements(), None)):
            value = safe_call(lambda eid=eid: eid.IntegerValue, None)
            if value is not None:
                ids.add(value)
    return ids


def parse_ids(text):
    """把 “2170577; 2170581, 2170722” 之类的输入拆成 ID 列表。"""
    if not text:
        return []
    cleaned = text
    for sep in (u"，", u"、", u";", u",", u"\t", u"\r", u"\n"):
        cleaned = cleaned.replace(sep, u" ")
    out = []
    for token in cleaned.split():
        try:
            value = int(token)
        except ValueError:
            continue
        if value not in out:
            out.append(value)
    return out


def delete_by_id():
    asker = safe_get(forms, "ask_for_string", None)
    if asker is None:
        forms.alert(u"当前 pyRevit 不支持输入框，无法使用该功能。")
        return
    text = asker(default=u"2170577; 2170581",
                 prompt=u"输入要删除的栏杆 ID，分号 / 逗号 / 空格分隔：",
                 title=u"删除指定 ID 的栏杆")
    if not text:
        return

    ids = parse_ids(text)
    if not ids:
        forms.alert(u"没有解析出有效的 ID。")
        return

    targets = []
    skipped = []
    for value in ids:
        elem = safe_call(lambda value=value: doc.GetElement(ElementId(value)), None)
        if elem is None:
            skipped.append(u"{0}（不存在）".format(value))
            continue
        if not isinstance(elem, Railing):
            cat = safe_call(lambda e=elem: e.Category.Name, u"")
            skipped.append(u"{0}（不是栏杆{1}）".format(
                value, u"：" + cat if cat else u""))
            continue
        host = element_label(resolve_host(safe_get(elem, "HostId", None)))
        targets.append((value, safe_call(lambda e=elem: e.Name, u""), host))

    if not targets:
        text = u"\n".join(skipped[:10]) if skipped else u"没有匹配的栏杆。"
        forms.alert(text)
        return

    detail = u"\n".join(u"- {0}  {1}  主体：{2}".format(*t) for t in targets[:20])
    choice = forms.SelectFromList.show(
        [u"确认删除这 {0} 根".format(len(targets)), u"取消"],
        title=u"将删除：\n" + detail,
        multiselect=False)
    if not choice or not choice.startswith(u"确认"):
        return

    with revit.Transaction(u"删除 {0} 根栏杆".format(len(targets))):
        for value, _, _ in targets:
            doc.Delete(ElementId(value))

    left = warned_railings()
    output.print_md(u"## 删除指定栏杆")
    output.print_md(u"已删除 {0} 根：{1}".format(
        len(targets), u"、".join(str(t[0]) for t in targets)))
    if skipped:
        output.print_md(u"跳过：\n" + u"\n".join(u"- " + s for s in skipped))
    output.print_md(u"当前“线重叠”警告还涉及 {0} 个图元。".format(len(left)))
    output.print_md(u"删错了可以 Ctrl+Z 撤销。")


CROSS_HALF = 0.5     # 标记十字半径（英尺，约 150 mm）


def marks_file():
    def _desktop():
        p = Environment.GetFolderPath(Environment.SpecialFolder.Desktop)
        return p if p and os.path.isdir(p) else u""
    base = safe_call(_desktop, u"") or safe_call(lambda: os.path.expanduser(u"~"), u".")
    title = safe_call(lambda: doc.Title, u"model") or u"model"
    for ch in u'\\/:*?"<>|':
        title = title.replace(ch, u"_")
    return os.path.join(base, u"六吨_重合标记_{0}.txt".format(title))


def load_marks():
    path = marks_file()
    if not os.path.isfile(path):
        return []
    try:
        with codecs.open(path, "r", "utf-8") as handle:
            text = handle.read()
    except Exception:
        return []
    out = []
    for token in text.replace(u";", u",").replace(u" ", u",").split(u","):
        token = token.strip()
        if token.isdigit():
            out.append(int(token))
    return out


def save_marks(ids):
    try:
        with codecs.open(marks_file(), "w", "utf-8") as handle:
            handle.write(u",".join(str(i) for i in ids))
        return True
    except Exception:
        return False


def suspicious_points(curves, tol=TOL):
    """找路径上可疑的重合 / 自触位置，返回 [(XYZ, 说明)]。"""
    found = []
    if not curves:
        return found

    ends = []
    for i, c in enumerate(curves):
        ends.append((i, c.GetEndPoint(0)))
        ends.append((i, c.GetEndPoint(1)))

    groups = []
    for idx, point in ends:
        for grp in groups:
            if dist(grp["p"], point) <= tol:
                grp["items"].append(idx)
                break
        else:
            groups.append({"p": point, "items": [idx]})

    for grp in groups:
        if len(grp["items"]) > 2:
            idxs = sorted(set(grp["items"]))
            found.append((grp["p"], u"{0} 段交于一点（第 {1} 段）".format(
                len(grp["items"]), u"、".join(str(i + 1) for i in idxs))))

    for i in range(len(curves)):
        a0, a1 = curves[i].GetEndPoint(0), curves[i].GetEndPoint(1)
        for j in range(i + 1, len(curves)):
            b0, b1 = curves[j].GetEndPoint(0), curves[j].GetEndPoint(1)
            same = ((dist(a0, b0) <= tol and dist(a1, b1) <= tol) or
                    (dist(a0, b1) <= tol and dist(a1, b0) <= tol))
            if same:
                mid = XYZ((a0.X + a1.X) / 2.0, (a0.Y + a1.Y) / 2.0,
                          (a0.Z + a1.Z) / 2.0)
                found.append((mid, u"第 {0} 段与第 {1} 段完全重合".format(i + 1, j + 1)))
    return found


def text_type_id():
    """找一个可用的文字类型，TextNoteOptions 必须带有效 TypeId。"""
    try:
        types = list(FilteredElementCollector(doc).OfClass(TextNoteType))
    except Exception:
        return None
    if not types:
        return None
    for item in types:
        name = safe_call(lambda item=item: item.Name, u"") or u""
        if u"2.5" in name:
            return item.Id
    return types[0].Id


def mark_overlaps():
    view = doc.ActiveView
    if not isinstance(view, ViewPlan):
        forms.alert(u"请在平面视图里运行：标记会画到当前视图。")
        return
    z = safe_call(lambda: view.GenLevel.Elevation, 0.0)

    railings = list(FilteredElementCollector(doc).OfClass(Railing))
    warned = warned_railings()
    targets = [r for r in railings if r.Id.IntegerValue in warned]
    if not targets:
        forms.alert(u"当前没有“线重叠”警告，没什么可标记的。")
        return

    created = []
    rows = []
    errors = []
    limit_hit = False
    marker_no = 0
    note_type = text_type_id()
    text_failed = 0
    with revit.Transaction(u"标记栏杆重合位置"):
        for railing in targets:
            curves = curves_of(railing.GetPath())
            hits = suspicious_points(curves)
            if not hits:
                rows.append([u"", railing.Id.IntegerValue,
                             u"未发现重合 / 自触", u"", u""])
                continue
            for point, reason in hits:
                marker_no += 1
                rows.append([
                    marker_no,
                    railing.Id.IntegerValue,
                    reason,
                    u"{0:.0f}".format(point.X * 304.8),
                    u"{0:.0f}".format(point.Y * 304.8),
                ])
                if note_type is not None:
                    try:
                        note = TextNote.Create(
                            doc, view.Id,
                            XYZ(point.X - CROSS_HALF, point.Y + CROSS_HALF + 0.1, z),
                            str(marker_no), TextNoteOptions(note_type))
                        created.append(note.Id.IntegerValue)
                    except Exception:
                        text_failed += 1
                for a, b in (
                    (XYZ(point.X - CROSS_HALF, point.Y, z),
                     XYZ(point.X + CROSS_HALF, point.Y, z)),
                    (XYZ(point.X, point.Y - CROSS_HALF, z),
                     XYZ(point.X, point.Y + CROSS_HALF, z)),
                ):
                    try:
                        dc = doc.Create.NewDetailCurve(view, Line.CreateBound(a, b))
                        created.append(dc.Id.IntegerValue)
                    except Exception as ex:
                        errors.append(u"{0}：{1}".format(railing.Id.IntegerValue, ex))
            if len(created) > 800:
                limit_hit = True
                break

    saved = save_marks(created)
    output.print_md(u"## 标记重合位置")
    if rows:
        output.print_table(
            rows[:300], title=u"可疑位置（共 {0} 处，编号与图中文字对应）".format(len(rows)),
            columns=[u"编号", u"栏杆 ID", u"说明", u"X(mm)", u"Y(mm)"])
    output.print_md(u"已在当前视图画出 **{0}** 个十字标记 + 编号文字。".format(
        len([r for r in rows if r[0] != u""])))
    if note_type is None:
        output.print_md(u"没找到可用的文字类型，编号文字未生成（十字标记不受影响）。")
    elif text_failed:
        output.print_md(u"{0} 个编号文字创建失败（十字标记不受影响）。".format(text_failed))
    if limit_hit:
        output.print_md(u"标记数量过多，已截断。")
    if not saved and created:
        output.print_md(u"标记 ID 没能写入文件，之后可能无法一键清除。")
    if errors:
        output.print_md(u"部分标记创建失败：\n" + u"\n".join(
            u"- `{0}`".format(e) for e in errors[:5]))
    output.print_md(u"\n看完用 **清除重合标记** 一键删掉；手工改路径时也可以先留着当参考。")


def clear_marks():
    ids = load_marks()
    if not ids:
        forms.alert(u"没有找到标记记录（可能已经清除过）。")
        return
    deleted = 0
    with revit.Transaction(u"清除重合标记"):
        for value in ids:
            try:
                doc.Delete(ElementId(value))
                deleted += 1
            except Exception:
                pass
    try:
        os.remove(marks_file())
    except Exception:
        pass
    output.print_md(u"## 清除重合标记")
    output.print_md(u"已删除 {0} 个标记（记录里共 {1} 个）。".format(deleted, len(ids)))


def main():
    if doc is None:
        forms.alert(u"请先打开一个项目。", exitscript=True)

    railings = list(FilteredElementCollector(doc).OfClass(Railing))
    if not railings:
        forms.alert(u"当前项目里没有栏杆扶手。", exitscript=True)

    mode = forms.CommandSwitchWindow.show(
        [u"只检查 / 定位",
         u"检查并修复（只重设水平路径）",
         u"标记重合位置",
         u"清除重合标记",
         u"删除指定 ID 的栏杆"],
        message=u"栏杆路径检查：选择要做什么")
    if not mode:
        return

    if mode == u"标记重合位置":
        mark_overlaps()
        return
    if mode == u"清除重合标记":
        clear_marks()
        return
    if mode == u"删除指定 ID 的栏杆":
        delete_by_id()
        return

    warned_ids = warned_railings()
    results = []
    for r in railings:
        x = analyze(r, r.Id.IntegerValue in warned_ids)
        x["flag"] = x["drop_mm"] > 0.5 or x["warned"]
        results.append(x)

    flagged = [x for x in results if x["flag"]]
    benign = [x for x in results if x["problem"] and not x["flag"]]
    fixable = [x for x in flagged if x["fixable"]]

    horizontal_fix = [x for x in fixable if x["horizontal"]]
    manual_fix = [x for x in fixable if not x["horizontal"]]

    output.print_md(u"## 栏杆路径检查")
    output.print_md(
        u"共 {0} 个栏杆 | 命中“线重叠”警告 {1} 个 | 水平可重设 {2} 个 | "
        u"斜向需手工 {3} 个 | 仅共线（几何未变）{4} 个".format(
            len(results), len([x for x in results if x["warned"]]),
            len(horizontal_fix), len(manual_fix), len(benign)))

    if flagged:
        rows = []
        for x in flagged[:200]:
            rows.append([
                output.linkify(x["railing"].Id),
                x["type"],
                u"水平" if x["horizontal"] else u"斜向",
                x["host"] or u"无",
                x["segments"],
                x["clean"],
                u"{0:.1f}".format(x["drop_mm"]),
                u"是" if x["warned"] else u"",
                x["problem"] or u"几何正常，但被警告点名",
            ])
        output.print_table(
            rows, title=u"需要处理的栏杆",
            columns=[u"ID", u"类型", u"路径", u"主体", u"原段数", u"清理后",
                     u"重叠(mm)", u"命中警告", u"说明"])
    else:
        output.print_md(u"当前没有命中“线重叠”警告的栏杆。")

    if mode == u"只检查 / 定位":
        if flagged:
            output.print_md(u"点上面的 ID 链接可以直接定位。")
        return

    if not fixable:
        forms.alert(u"没有可处理的栏杆：被点名的栏杆可能含弧线，或路径已退化。")
        return

    labels = [u"{0}  {1}  {2}".format(
        x["eid"], x["type"], u"水平" if x["horizontal"] else u"斜向")
        for x in fixable]
    picked = forms.SelectFromList.show(
        labels, title=u"选择要重设路径的栏杆（只重设水平路径，Ctrl+Z 可撤销）",
        multiselect=True, button_name=u"重设")
    if not picked:
        return

    targets = [fixable[labels.index(p)] for p in picked]
    horizontal_targets = [x for x in targets if x["horizontal"]]
    manual_targets = [x for x in targets if not x["horizontal"]]

    ok_count = 0
    ok_ids = []
    failures = []
    for x in horizontal_targets:
        try:
            ok, msg = fix(x["railing"], x["closed"])
        except Exception as ex:
            ok, msg = False, u"{0}".format(ex)
        if ok:
            ok_count += 1
            ok_ids.append(x["eid"])
        else:
            failures.append(u"- {0}：{1} → **需手工处理**".format(x["eid"], msg))

    for x in manual_targets:
        failures.append(u"- {0}：斜向路径，SetPath 不支持 → **需手工处理**".format(x["eid"]))

    still = set(ok_ids) & warned_railings()

    output.print_md(u"### 结果")
    output.print_md(u"重设成功 {0} 个，需手工处理 {1} 个。".format(
        ok_count, len(failures)))
    if still:
        output.print_md(u"其中 **{0} 个重设后仍然报警告**：{1}".format(
            len(still), u"、".join(str(i) for i in sorted(still))))
        output.print_md(u"重复线在草图里、不在路径上，这几个也只能手工处理。")
    if failures:
        output.print_md(u"\n".join(failures))
    if failures or still:
        output.print_md(
            u"\n**手工处理步骤**：管理 → 警告 → 选中这条 → 显示(S) 高亮问题线 → "
            u"选中栏杆 → 编辑路径 → 从右往左框选重合处 → 删掉多余那条 → "
            u"修剪/延伸接上 → 完成编辑模式。")


try:
    main()
except Exception:
    output.print_md(u"脚本出错：\n```\n{0}\n```".format(traceback.format_exc()))
    forms.alert(u"脚本出错，详见输出窗口。", title=u"栏杆路径检查")
