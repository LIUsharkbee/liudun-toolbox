# -*- coding: utf-8 -*-
"""钢筋可见性 —— 按视图批量显示 / 隐藏钢筋

五个动作（选择后执行）：
  1. 在视图中显示钢筋      —— 当前视图里所有钢筋取消隐藏
  2. 在视图中隐藏钢筋      —— 当前视图里所有钢筋隐藏
  3. 在宿主中显示钢筋      —— 只显示「选中宿主」上的钢筋
  4. 在宿主中隐藏钢筋      —— 隐藏「选中宿主」上的钢筋
  5. 隐藏非宿主钢筋        —— 保留选中宿主上的钢筋，其余全隐藏

只改视图可见性（View.HideElements / UnhideElements），不动模型、不进事务。
"""

from __future__ import print_function

import time
import traceback

from Autodesk.Revit.DB import (
    BuiltInCategory,
    ElementId,
    FilteredElementCollector,
    Transaction,
    View,
)
from Autodesk.Revit.DB.Structure import (
    AreaReinforcement,
    PathReinforcement,
    Rebar,
    RebarInSystem,
)
from System.Collections.Generic import List as DotNetList

from pyrevit import revit, forms, script

doc = revit.doc
uidoc = revit.uidoc
output = script.get_output()

LOG_PATH = r"C:\Users\cyl\Documents\deepseek-harness\default-workspace\_钢筋可见性日志.txt"


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


def active_view():
    view = doc.ActiveView
    if view is None:
        return None
    try:
        if view.IsTemplate:
            return None
    except Exception:
        pass
    return view


def all_rebars():
    """整份文档里的钢筋（视图级收集器会漏掉已隐藏的钢筋）。"""
    result = []
    seen = set()
    for cls in (Rebar, RebarInSystem, AreaReinforcement, PathReinforcement):
        try:
            for element in FilteredElementCollector(doc).OfClass(cls):
                if element.Id.IntegerValue not in seen:
                    seen.add(element.Id.IntegerValue)
                    result.append(element)
        except Exception:
            pass
    return result


def diagnose(view):
    """统计各来源的钢筋数量，用于排查「图里有但工具找不到」。"""
    lines = []
    for cls in (Rebar, RebarInSystem, AreaReinforcement, PathReinforcement):
        name = u"%s" % cls
        count = 0
        try:
            count = len([e for e in FilteredElementCollector(doc).OfClass(cls)])
        except Exception:
            count = -1
        lines.append(u"%s: %d" % (name, count))

    all_reb = all_rebars()
    visible = 0
    for element in all_reb:
        try:
            if not view.IsHidden(element.Id):
                visible += 1
        except Exception:
            visible += 1
    lines.append(u"全文档合计: %d，其中本视图可见: %d" % (len(all_reb), visible))
    lines.append(u"本视图隐藏的图元数: %d" % len(view.GetIsHidden()) if hasattr(view, "GetIsHidden") else u"")
    return [item for item in lines if item]


def visible_rebars(view):
    result = []
    for rebar in all_rebars():
        try:
            if not view.IsHidden(rebar.Id):
                result.append(rebar)
        except Exception:
            result.append(rebar)
    return result


def rebars_in_view(view):
    """视图里存在的钢筋：优先取全部，视图过滤交给 IsHidden 判断。"""
    return visible_rebars(view)


def rebar_host_id(rebar):
    try:
        host_id = rebar.GetHostId()
        if host_id is not None and host_id.IntegerValue > 0:
            return host_id.IntegerValue
    except Exception:
        pass
    try:
        return rebar.GetHostId().IntegerValue
    except Exception:
        return -1


def selected_host_ids():
    ids = []
    for element_id in uidoc.Selection.GetElementIds():
        ids.append(element_id.IntegerValue)
    return ids


def id_list(elements):
    result = DotNetList[ElementId]()
    for element in elements:
        result.Add(element.Id)
    return result


ACTIONS = [
    u"诊断：数一数有多少钢筋",
    u"在视图中显示钢筋",
    u"在视图中隐藏钢筋",
    u"在宿主中显示钢筋",
    u"在宿主中隐藏钢筋",
    u"隐藏非宿主钢筋",
]


def do_action(view, action):
    if action == ACTIONS[0]:
        return 0, diagnose(view)

    rebars = all_rebars()
    if not rebars:
        return 0, [u"整份文档里没有找到钢筋（Rebar / 钢筋网 / 面积钢筋 / 路径钢筋都是 0）"]

    host_ids = selected_host_ids()

    if action == ACTIONS[1]:
        view.UnhideElements(id_list(rebars))
        return len(rebars), []

    if action == ACTIONS[2]:
        view.HideElements(id_list(rebars))
        return len(rebars), []

    if not host_ids:
        return 0, [u"这个动作需要先选中宿主（梁 / 柱 / 板 / 墙），再点按钮"]

    matched = []
    others = []
    for rebar in rebars:
        if rebar_host_id(rebar) in host_ids:
            matched.append(rebar)
        else:
            others.append(rebar)

    if action == ACTIONS[3]:
        if not matched:
            return 0, [u"选中的宿主上没有钢筋"]
        view.UnhideElements(id_list(matched))
        return len(matched), []

    if action == ACTIONS[4]:
        if not matched:
            return 0, [u"选中的宿主上没有钢筋"]
        view.HideElements(id_list(matched))
        return len(matched), []

    # 隐藏非宿主钢筋
    if not others:
        return 0, [u"除了选中宿主的钢筋，视图里没有别的钢筋"]
    view.HideElements(id_list(others))
    return len(others), []


def main():
    log_line(u"== 钢筋可见性 启动 ==")
    if doc is None:
        forms.alert(u"请先打开一个项目。", exitscript=True)

    view = active_view()
    if view is None:
        forms.alert(u"当前视图不能用（视图模板或没有打开视图）。", exitscript=True)

    rebars = all_rebars()
    visible = visible_rebars(view)
    selected_count = uidoc.Selection.GetElementIds().Count
    title = u"钢筋可见性（视图：%s｜钢筋 %d 根，当前可见 %d 根｜选中 %d 个）" % (
        text_of(view.Name), len(rebars), len(visible), selected_count)

    action = forms.SelectFromList.show(
        ACTIONS,
        title=title,
        button_name=u"执行",
        multiselect=False)
    if not action:
        return

    try:
        count, failures = do_action(view, action)
    except Exception:
        tb = traceback.format_exc()
        log_line(tb)
        forms.alert(u"执行失败：\n{0}".format(tb[-700:]), title=u"钢筋可见性")
        return

    lines = [u"%s：%d 根" % (action, count)]
    if failures:
        lines.extend(failures)
    log_line(u" | ".join(lines))
    forms.alert(u"\n".join(lines), title=u"钢筋可见性")


try:
    main()
except Exception:
    tb = traceback.format_exc()
    log_line(tb)
    forms.alert(u"脚本出错：\n{0}".format(tb[-700:]), title=u"钢筋可见性")
