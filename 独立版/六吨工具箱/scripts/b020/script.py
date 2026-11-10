# -*- coding: utf-8 -*-
"""栏杆高度修复

针对警告：
  * “一个或多个栏杆的顶参照低于底参照。没有创建这些栏杆。”
  * “不能创建一个或多个扶手的几何图形”

三种模式：
  1. 只检查 / 定位：列出每个栏杆的底/顶标高、底部/顶部偏移、从路径偏移，标出异常；
  2. 修复高度参数：顶标高拉到与底标高一致、顶部偏移补到 1000 mm、从路径偏移归零；
  3. 导出参数清单：把选中栏杆的全部参数（名称 / 类型 / 值 / 参数 ID）打出来，
     方便排查不同版本、不同命名的参数。

每根栏杆一个事务，Ctrl+Z 可撤销。
"""

from __future__ import print_function

import traceback

from Autodesk.Revit.DB import (
    BuiltInParameter,
    ElementId,
    FilteredElementCollector,
    Level,
    StorageType,
)
from Autodesk.Revit.DB.Architecture import Railing

from pyrevit import revit, forms, script

doc = revit.doc
output = script.get_output()

FT_TO_MM = 304.8
DEFAULT_TOP_OFFSET_MM = 1000.0

BASE_LEVEL_NAMES = (u"底部标高", u"基面标高", u"基准标高")
TOP_LEVEL_NAMES = (u"顶部标高", u"顶面标高")
BASE_OFFSET_NAMES = (u"底部偏移",)
TOP_OFFSET_NAMES = (u"顶部偏移",)
PATH_OFFSET_NAMES = (u"从路径偏移", u"路径偏移")
TYPE_HEIGHT_NAMES = (u"顶部扶栏高度", u"扶手高度", u"扶手 1 高度",
                     u"栏杆高度", u"顶部扶栏")
TYPE_HEIGHT_BIPS = (
    "RAILING_SYSTEM_TOP_RAIL_HEIGHT_PARAM",
    "HANDRAIL_HEIGHT_PARAM",
    "STAIRS_RAILING_HEIGHT",
)


def safe_call(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def param_map(elem):
    out = {}
    for param in elem.Parameters:
        name = safe_call(lambda p=param: p.Definition.Name, u"")
        if name:
            out[name] = param
    return out


def first_param(params, names):
    for name in names:
        if name in params:
            return params[name]
    return None


def param_mm(param):
    if param is None:
        return None
    if safe_call(lambda: param.StorageType, None) != StorageType.Double:
        return None
    value = safe_call(lambda: param.AsDouble(), None)
    return None if value is None else value * FT_TO_MM


def param_level(params, names):
    param = first_param(params, names)
    if param is None:
        return None
    if safe_call(lambda: param.StorageType, None) != StorageType.ElementId:
        return None
    return safe_call(lambda: param.AsElementId(), None)


def level_mm(level_id):
    if level_id is None or level_id == ElementId.InvalidElementId:
        return None
    level = safe_call(lambda: doc.GetElement(level_id), None)
    if level is None:
        return None
    elevation = safe_call(lambda: level.Elevation, None)
    return None if elevation is None else elevation * FT_TO_MM


def level_name(level_id):
    if level_id is None or level_id == ElementId.InvalidElementId:
        return u""
    level = safe_call(lambda: doc.GetElement(level_id), None)
    return safe_call(lambda: level.Name, u"") if level is not None else u""


def railing_type_name(railing):
    type_elem = safe_call(lambda: doc.GetElement(railing.GetTypeId()), None)
    return safe_call(lambda: type_elem.Name, u"") if type_elem is not None else u""


def inspect(railing):
    params = param_map(railing)
    info = {
        "railing": railing,
        "eid": railing.Id.IntegerValue,
        "type": railing_type_name(railing),
        "params": params,
        "base_level": param_level(params, BASE_LEVEL_NAMES),
        "top_level": param_level(params, TOP_LEVEL_NAMES),
        "base_offset": param_mm(first_param(params, BASE_OFFSET_NAMES)),
        "top_offset": param_mm(first_param(params, TOP_OFFSET_NAMES)),
        "path_offset": param_mm(first_param(params, PATH_OFFSET_NAMES)),
        "problems": [],
        "notes": [],
        "fixable": False,
    }

    base_z = level_mm(info["base_level"])
    top_z = level_mm(info["top_level"])
    info["base_z"] = base_z
    info["top_z"] = top_z

    type_elem = safe_call(lambda: doc.GetElement(railing.GetTypeId()), None)
    type_params = param_map(type_elem) if type_elem is not None else {}
    info["type_params"] = type_params
    info["type_heights"] = []
    for name in TYPE_HEIGHT_NAMES:
        param = type_params.get(name)
        if param is None:
            continue
        value = param_mm(param)
        if value is None:
            continue
        info["type_heights"].append((name, value))
        if value <= 0.5:
            info["problems"].append(u"类型「{0}」= {1:.0f} mm".format(name, value))

    if info["top_level"] is None and info["base_level"] is not None:
        info["notes"].append(u"未找到顶部标高参数")
    elif base_z is not None and top_z is not None and top_z < base_z - 0.5:
        info["problems"].append(u"顶参照低于底参照")

    if info["top_offset"] is not None and info["top_offset"] < 0:
        info["problems"].append(u"顶部偏移为负")

    if info["path_offset"] is not None and abs(info["path_offset"]) > 1.0:
        info["notes"].append(u"从路径偏移 {0:.1f} mm".format(info["path_offset"]))

    info["fixable"] = bool(info["problems"]) and info["base_level"] is not None
    return info


def set_mm(param, mm):
    if param is None:
        return False
    if safe_call(lambda: param.IsReadOnly, True):
        return False
    try:
        param.Set(mm / FT_TO_MM)
        return True
    except Exception:
        return False


def fix(info):
    params = info["params"]
    changed = []

    top_level_param = first_param(params, TOP_LEVEL_NAMES)
    base_level = info["base_level"]
    if top_level_param is not None and base_level is not None:
        if safe_call(lambda: top_level_param.IsReadOnly, True) is False:
            try:
                top_level_param.Set(base_level)
                changed.append(u"顶部标高 → 底部标高")
            except Exception as ex:
                return False, u"设置顶部标高失败：{0}".format(ex)

    top_offset_param = first_param(params, TOP_OFFSET_NAMES)
    if top_offset_param is not None:
        current = info["top_offset"] or 0.0
        target = DEFAULT_TOP_OFFSET_MM if current < 300 else current
        if set_mm(top_offset_param, target):
            changed.append(u"顶部偏移 → {0:.0f} mm".format(target))

    path_offset_param = first_param(params, PATH_OFFSET_NAMES)
    if path_offset_param is not None:
        if set_mm(path_offset_param, 0.0):
            changed.append(u"从路径偏移 → 0")

    if not changed:
        return False, u"没有可写的参数（可能被锁定或不存在）"
    return True, u"；".join(changed)


def dump_params(railing):
    params = param_map(railing)
    rows = []
    for name, param in params.items():
        storage = safe_call(lambda p=param: p.StorageType, None)
        value = u""
        if storage == StorageType.Double:
            value = u"{0:.1f} mm".format((safe_call(lambda p=param: p.AsDouble(), 0.0) or 0.0) * FT_TO_MM)
        elif storage == StorageType.Integer:
            value = str(safe_call(lambda p=param: p.AsInteger(), u""))
        elif storage == StorageType.String:
            value = safe_call(lambda p=param: p.AsString(), u"") or u""
        elif storage == StorageType.ElementId:
            value = str(safe_call(lambda p=param: p.AsElementId().IntegerValue, u""))
        pid = safe_call(lambda p=param: p.Id.IntegerValue, u"")
        rows.append([name, str(storage), value, pid])
    output.print_table(
        rows, title=u"参数清单：{0}".format(railing.Id.IntegerValue),
        columns=[u"名称", u"类型", u"值", u"参数 ID"])


def dump_all_params(railing):
    """实例 + 类型参数一起导出，方便定位高度参数到底挂在谁身上。"""
    rows = []

    def collect(params, source):
        for name, param in params.items():
            storage = safe_call(lambda p=param: p.StorageType, None)
            value = u""
            if storage == StorageType.Double:
                value = u"{0:.1f} mm".format(
                    (safe_call(lambda p=param: p.AsDouble(), 0.0) or 0.0) * FT_TO_MM)
            elif storage == StorageType.Integer:
                value = str(safe_call(lambda p=param: p.AsInteger(), u""))
            elif storage == StorageType.String:
                value = safe_call(lambda p=param: p.AsString(), u"") or u""
            elif storage == StorageType.ElementId:
                value = str(safe_call(lambda p=param: p.AsElementId().IntegerValue, u""))
            pid = safe_call(lambda p=param: p.Id.IntegerValue, u"")
            rows.append([source, name, str(storage), value, pid])

    collect(param_map(railing), u"实例")
    type_elem = safe_call(lambda: doc.GetElement(railing.GetTypeId()), None)
    if type_elem is not None:
        collect(param_map(type_elem), u"类型")

    output.print_table(
        rows, title=u"参数清单：{0}（{1} 项）".format(railing.Id.IntegerValue, len(rows)),
        columns=[u"来源", u"名称", u"类型", u"值", u"参数 ID"])


def main():
    if doc is None:
        forms.alert(u"请先打开一个项目。", exitscript=True)

    mode = forms.CommandSwitchWindow.show(
        [u"只检查 / 定位", u"修复高度参数", u"导出参数清单"],
        message=u"栏杆高度修复：选择要做什么")
    if not mode:
        return

    if mode == u"导出参数清单":
        selection = list(revit.get_selection())
        targets = [e for e in selection if isinstance(e, Railing)]
        if not targets:
            forms.alert(u"先选中一根栏杆。")
            return
        for railing in targets[:3]:
            dump_all_params(railing)
        return

    railings = list(FilteredElementCollector(doc).OfClass(Railing))
    if not railings:
        forms.alert(u"当前项目里没有栏杆。")
        return

    infos = [inspect(r) for r in railings]
    bad = [x for x in infos if x["problems"]]

    output.print_md(u"## 栏杆高度检查")
    output.print_md(u"共 {0} 根栏杆，异常 {1} 根。".format(len(infos), len(bad)))

    if bad:
        rows = []
        for x in bad[:200]:
            rows.append([
                output.linkify(x["railing"].Id),
                x["type"],
                u"{0:.0f} / {1:.0f}".format(float(x["base_z"] or 0), float(x["top_z"] or 0)),
                u"{0:.0f} / {1:.0f}".format(float(x["base_offset"] or 0), float(x["top_offset"] or 0)),
                u"{0:.1f}".format(float(x["path_offset"] or 0)),
                u"；".join(x["problems"]),
            ])
        output.print_table(
            rows, title=u"异常栏杆",
            columns=[u"ID", u"类型", u"底/顶标高(mm)", u"底/顶偏移(mm)",
                     u"路径偏移(mm)", u"问题"])
    else:
        output.print_md(u"没有发现标高参数异常。")

    noted = [x for x in infos if x["notes"]]
    if noted:
        kinds = sorted(set(u"；".join(x["notes"]) for x in noted))
        output.print_md(u"另有 {0} 根有提示：{1}".format(
            len(noted), u" ｜ ".join(kinds[:5])))

    if mode == u"只检查 / 定位":
        output.print_md(u"点 ID 链接定位；确认后再跑「修复高度参数」。")
        return

    fixable = [x for x in bad if x["fixable"]]
    if not fixable:
        forms.alert(u"没有可以自动修复的栏杆。")
        return

    labels = [u"{0}  {1}".format(x["eid"], u"；".join(x["problems"])) for x in fixable]
    picked = forms.SelectFromList.show(
        labels, title=u"选择要修复的栏杆（Ctrl+Z 可撤销）",
        multiselect=True, button_name=u"修复")
    if not picked:
        return

    ok_count = 0
    failures = []
    for label in picked:
        x = fixable[labels.index(label)]
        try:
            with revit.Transaction(u"修复栏杆高度 {0}".format(x["eid"])):
                ok, msg = fix(x)
        except Exception as ex:
            ok, msg = False, u"{0}".format(ex)
        if ok:
            ok_count += 1
            output.print_md(u"- {0}：{1}".format(x["eid"], msg))
        else:
            failures.append(u"- {0}：{1}".format(x["eid"], msg))

    output.print_md(u"### 结果")
    output.print_md(u"修复成功 {0} 个，失败 {1} 个。".format(ok_count, len(failures)))
    if failures:
        output.print_md(u"\n".join(failures))
    output.print_md(u"\n改完看「管理 → 警告」，两条应同时消失；几何没出来就再跑一次检查。")


try:
    main()
except Exception:
    output.print_md(u"脚本出错：\n```\n{0}\n```".format(traceback.format_exc()))
    forms.alert(u"脚本出错，详见输出窗口。", title=u"栏杆高度修复")
