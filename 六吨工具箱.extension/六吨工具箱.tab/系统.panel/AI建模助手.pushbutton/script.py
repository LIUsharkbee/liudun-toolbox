# -*- coding: utf-8 -*-
"""AI 工具助手 —— 让模型用「工具」操作 Revit

内置 7 类工具（对标 MCP 思路）：
    revit_context   我在哪：版本/文档/视图/选择集/标高/单位
    revit_query     查元素：按类别、名称、标高、视图、当前选择
    revit_read      读细节：按 Id 读类别/族/类型/标高/参数
    revit_selection 读选择集（或返回当前选择摘要）
    revit_geometry  读几何：包围盒、定位线、面积体积、长度
    revit_layers    读导入 CAD 的图层清单与图元统计
    revit_execute   唯一的写工具：按结构化参数执行（事务内，可撤销）

模型只会输出 JSON 参数，不会执行任意代码；每次写操作都在一个事务里，
可以用 Ctrl+Z 整体撤销。
"""

import json
import System.Windows.Forms
import os
import traceback

from System.Collections.Generic import List as DotNetList
from System.Windows import Thickness

from Autodesk.Revit.DB import (
    BuiltInCategory,
    BuiltInParameter,
    ElementId,
    FilteredElementCollector,
    GeometryInstance,
    ImportInstance,
    Line,
    Level,
    Material,
    Options,
    OverrideGraphicSettings,
    Transaction,
    ViewDetailLevel,
    Wall,
    WallType,
    XYZ,
)
from Autodesk.Revit.UI.Selection import ObjectType

from pyrevit import forms, revit, script
from System.Windows import Clipboard, Visibility
from System.Windows.Input import Key, Keyboard, ModifierKeys

# IronPython 的 urllib2 走 .NET HttpWebRequest，默认不启用 TLS 1.2，
# 访问 api.deepseek.com 会报「基础连接已经关闭」，这里强制打开。
try:
    from System.Net import ServicePointManager, SecurityProtocolType
    ServicePointManager.SecurityProtocol = SecurityProtocolType.Tls12
except Exception:
    pass

doc = revit.doc
uidoc = revit.uidoc
logger = script.get_logger()

FT_TO_MM = 304.8
MM_TO_FT = 1.0 / 304.8

APP_DIR = os.path.join(os.environ.get("APPDATA", ""), u"六吨工具箱")
KEY_PATH = os.path.join(APP_DIR, u"ai_key.txt")
LOG_PATH = r"C:\Users\cyl\Documents\deepseek-harness\default-workspace\_AI工具日志.txt"

API_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-chat"
MAX_ROUNDS = 5


def write_log(text):
    try:
        f = open(LOG_PATH, "ab")
        try:
            f.write((u"{0}\n".format(text)).encode("utf-8"))
        finally:
            f.close()
    except Exception:
        pass


# ---------------------------------------------------------------- 通用工具


def type_name(element):
    if element is None:
        return u"?"
    for attr in ("Name", "get_Name"):
        try:
            value = getattr(element, attr)
            if not callable(value):
                return u"{0}".format(value)
            return u"{0}".format(value())
        except Exception:
            continue
    return u"<{0}>".format(element.Id.IntegerValue)


def category_name(element):
    try:
        return u"{0}".format(element.Category.Name)
    except Exception:
        return u"<无类别>"


def level_name_of(element):
    try:
        level = doc.GetElement(element.LevelId)
        if level is not None:
            return type_name(level)
    except Exception:
        pass
    try:
        parameter = element.get_Parameter(BuiltInParameter.FAMILY_LEVEL_PARAM)
        if parameter is not None:
            return parameter.AsValueString()
    except Exception:
        pass
    return u""


def param_rows(element, limit=40):
    rows = []
    try:
        parameters = element.Parameters
    except Exception:
        return rows
    for parameter in parameters:
        try:
            if parameter.IsReadOnly:
                continue
            definition = parameter.Definition
            name = u"{0}".format(definition.Name)
            value = parameter.AsValueString()
            if value is None:
                value = u""
            rows.append({u"name": name, u"value": u"{0}".format(value)})
        except Exception:
            continue
        if len(rows) >= limit:
            break
    return rows


def element_brief(element):
    return {
        u"id": element.Id.IntegerValue,
        u"category": category_name(element),
        u"type": type_name(element),
        u"level": level_name_of(element),
    }


def iter_elements(limit=200):
    collector = FilteredElementCollector(doc).WhereElementIsNotElementType()
    count = 0
    for element in collector:
        yield element
        count += 1
        if count >= limit:
            break


def find_category_ids(name):
    """按中文类别名找 BuiltInCategory（覆盖常用项）。"""
    table = {
        u"墙": BuiltInCategory.OST_Walls,
        u"楼板": BuiltInCategory.OST_Floors,
        u"门窗": BuiltInCategory.OST_Doors,
        u"门": BuiltInCategory.OST_Doors,
        u"窗": BuiltInCategory.OST_Windows,
        u"柱": BuiltInCategory.OST_StructuralColumns,
        u"结构柱": BuiltInCategory.OST_StructuralColumns,
        u"梁": BuiltInCategory.OST_StructuralFraming,
        u"屋顶": BuiltInCategory.OST_Roofs,
        u"天花板": BuiltInCategory.OST_Ceilings,
        u"楼梯": BuiltInCategory.OST_Stairs,
        u"栏杆": BuiltInCategory.OST_StairsRailing,
        u"管道": BuiltInCategory.OST_PipeCurves,
        u"风管": BuiltInCategory.OST_DuctCurves,
        u"钢筋": BuiltInCategory.OST_Rebar,
        u"房间": BuiltInCategory.OST_Rooms,
    }
    return table.get(name)


def to_ft(mm):
    return float(mm) * MM_TO_FT


def to_mm(ft):
    return round(float(ft) * FT_TO_MM, 1)


# ---------------------------------------------------------------- 读工具


def tool_context(args):
    levels = []
    try:
        for level in FilteredElementCollector(doc).OfClass(Level):
            levels.append({u"id": level.Id.IntegerValue,
                           u"name": type_name(level),
                           u"elevation_mm": to_mm(level.Elevation)})
    except Exception:
        pass
    levels.sort(key=lambda x: x[u"elevation_mm"])
    try:
        view_name = doc.ActiveView.Name
        view_id = doc.ActiveView.Id.IntegerValue
    except Exception:
        view_name, view_id = u"?", -1
    return {
        u"revit_version": u"{0}".format(doc.Application.VersionName),
        u"document": doc.Title,
        u"active_view": view_name,
        u"active_view_id": view_id,
        u"selection_count": uidoc.Selection.GetElementIds().Count,
        u"levels": levels[:30],
        u"units_note": u"长度单位 mm，面积 m2，体积 m3；模型内部为英尺",
    }


def tool_query(args):
    limit = int(args.get("limit") or 40)
    category = args.get("category") or u""
    name_like = args.get("name_like") or u""
    result = []
    bics = find_category_ids(category) if category else None
    if category and bics is None:
        return {u"error": u"不认识的类别：{0}".format(category)}
    if bics is not None:
        collector = FilteredElementCollector(doc).OfCategory(bics).WhereElementIsNotElementType()
        source = collector
    else:
        source = iter_elements(3000)
    for element in source:
        try:
            if name_like and (name_like not in type_name(element)):
                continue
        except Exception:
            continue
        result.append(element_brief(element))
        if len(result) >= limit:
            break
    return {u"count": len(result), u"items": result}


def tool_read(args):
    ids = args.get("ids") or []
    result = []
    for value in ids:
        try:
            element = doc.GetElement(ElementId(int(value)))
        except Exception:
            element = None
        if element is None:
            result.append({u"id": value, u"error": u"找不到"})
            continue
        item = element_brief(element)
        item[u"parameters"] = param_rows(element)
        result.append(item)
    return {u"items": result}


def tool_selection(args):
    ids = list(uidoc.Selection.GetElementIds())
    result = []
    for element_id in ids[:60]:
        element = doc.GetElement(element_id)
        if element is not None:
            result.append(element_brief(element))
    return {u"count": len(ids), u"items": result}


def tool_geometry(args):
    ids = args.get("ids") or []
    result = []
    for value in ids:
        try:
            element = doc.GetElement(ElementId(int(value)))
        except Exception:
            element = None
        if element is None:
            result.append({u"id": value, u"error": u"找不到"})
            continue
        item = {u"id": element.Id.IntegerValue}
        try:
            box = element.get_BoundingBox(None)
            if box is not None:
                item[u"bbox_mm"] = {
                    u"min": [to_mm(box.Min.X), to_mm(box.Min.Y), to_mm(box.Min.Z)],
                    u"max": [to_mm(box.Max.X), to_mm(box.Max.Y), to_mm(box.Max.Z)],
                }
                item[u"size_mm"] = [to_mm(box.Max.X - box.Min.X),
                                    to_mm(box.Max.Y - box.Min.Y),
                                    to_mm(box.Max.Z - box.Min.Z)]
        except Exception:
            pass
        try:
            location = element.Location
            if location is not None and hasattr(location, "Curve"):
                curve = location.Curve
                item[u"length_mm"] = to_mm(curve.Length)
                item[u"start_mm"] = [to_mm(curve.GetEndPoint(0).X),
                                     to_mm(curve.GetEndPoint(0).Y),
                                     to_mm(curve.GetEndPoint(0).Z)]
                item[u"end_mm"] = [to_mm(curve.GetEndPoint(1).X),
                                   to_mm(curve.GetEndPoint(1).Y),
                                   to_mm(curve.GetEndPoint(1).Z)]
        except Exception:
            pass
        for label, param in ((u"area_m2", BuiltInParameter.HOST_AREA_COMPUTED),
                             (u"volume_m3", BuiltInParameter.HOST_VOLUME_COMPUTED)):
            try:
                parameter = element.get_Parameter(param)
                if parameter is not None:
                    value = parameter.AsDouble()
                    if label == u"area_m2":
                        item[label] = round(value * FT_TO_MM * FT_TO_MM / 1e6, 2)
                    else:
                        item[label] = round(value * (FT_TO_MM ** 3) / 1e9, 3)
            except Exception:
                pass
        result.append(item)
    return {u"items": result}


def tool_layers(args):
    """列出导入 CAD 的图层与图元数。"""
    layers = {}
    result = []
    try:
        imports = FilteredElementCollector(doc).OfClass(ImportInstance)
    except Exception:
        imports = []
    count = 0
    for import_instance in imports:
        count += 1
        opts = Options()
        opts.DetailLevel = ViewDetailLevel.Fine
        try:
            geom = import_instance.get_Geometry(opts)
        except Exception:
            continue
        stack = [geom]
        while stack:
            container = stack.pop()
            for obj in container:
                try:
                    instance = obj
                    if hasattr(instance, "GetInstanceGeometry"):
                        stack.append(instance.GetInstanceGeometry())
                        continue
                    style_id = instance.GraphicsStyleId
                    if style_id is None or style_id.IntegerValue < 0:
                        continue
                    style = doc.GetElement(style_id)
                    name = u"<默认>"
                    try:
                        name = u"{0}".format(style.GraphicsStyleCategory.Name)
                    except Exception:
                        pass
                    layers[name] = layers.get(name, 0) + 1
                except Exception:
                    continue
    for name, value in sorted(layers.items(), key=lambda x: -x[1]):
        result.append({u"layer": name, u"count": value})
    return {u"import_count": count, u"layers": result[:40]}


# ---------------------------------------------------------------- 写工具


def _apply_color(element, color):
    view = doc.ActiveView
    settings = OverrideGraphicSettings()
    from System.Drawing import Color
    settings.SetProjectionLineColor(Color.FromRgb(color[0], color[1], color[2]))
    settings.SetSurfaceForegroundPatternColor(Color.FromRgb(color[0], color[1], color[2]))
    view.SetElementOverrides(element.Id, settings)


def tool_execute(args):
    """唯一的写工具。op 决定动作，全部在事务内。"""
    op = args.get("op")
    if not op:
        return {u"error": u"缺少 op"}

    transaction = Transaction(doc, u"AI 工具：{0}".format(op))
    transaction.Start()
    done = 0
    notes = []
    try:
        if op == u"set_param":
            for item in args.get("items") or []:
                element = doc.GetElement(ElementId(int(item["id"])))
                name = item.get("param")
                value = item.get("value")
                if element is None or not name:
                    continue
                for parameter in element.Parameters:
                    try:
                        if parameter.Definition.Name != name:
                            continue
                        if parameter.StorageType.ToString() == "Double":
                            parameter.Set(float(value) * MM_TO_FT)
                        elif parameter.StorageType.ToString() == "Integer":
                            parameter.Set(int(value))
                        else:
                            parameter.Set(u"{0}".format(value))
                        done += 1
                        break
                    except Exception:
                        continue
            notes.append(u"改了 {0} 个参数".format(done))

        elif op == u"create_walls":
            level = doc.GetElement(ElementId(int(args["level_id"])))
            height = to_ft(args.get("height_mm") or 3000)
            for segment in args.get("segments") or []:
                p1 = segment[0]
                p2 = segment[1]
                line = Line.CreateBound(
                    XYZ(to_ft(p1[0]), to_ft(p1[1]), level.Elevation),
                    XYZ(to_ft(p2[0]), to_ft(p2[1]), level.Elevation))
                wall_type_id = None
                try:
                    wall_type_id = FilteredElementCollector(doc).OfClass(
                        WallType).FirstElementId()
                except Exception:
                    wall_type_id = None
                if wall_type_id is None:
                    notes.append(u"没有可用的墙类型")
                    continue
                Wall.Create(doc, line, wall_type_id, level.Id, height, 0.0, False, False)
                done += 1
            notes.append(u"建了 {0} 面墙".format(done))

        elif op == u"color":
            color = args.get("rgb") or [255, 120, 160]
            for value in args.get("ids") or []:
                element = doc.GetElement(ElementId(int(value)))
                if element is not None:
                    _apply_color(element, color)
                    done += 1
            notes.append(u"着色 {0} 个".format(done))

        elif op == u"hide":
            view = doc.ActiveView
            ids = DotNetList[ElementId]()
            for value in args.get("ids") or []:
                ids.Add(ElementId(int(value)))
            if ids.Count:
                view.HideElements(ids)
                done = ids.Count
            notes.append(u"隐藏 {0} 个".format(done))

        elif op == u"isolate":
            view = doc.ActiveView
            ids = DotNetList[ElementId]()
            for value in args.get("ids") or []:
                ids.Add(ElementId(int(value)))
            if ids.Count:
                view.IsolateElementsTemporary(ids)
                done = ids.Count
            notes.append(u"隔离 {0} 个".format(done))

        elif op == u"create_pipes":
            from Autodesk.Revit.DB.Plumbing import Pipe
            from Autodesk.Revit.DB.Plumbing import PipingSystemType
            level = doc.GetElement(ElementId(int(args["level_id"])))
            system_id = FilteredElementCollector(doc).OfClass(PipingSystemType).FirstElementId()
            pipe_type_id = FilteredElementCollector(doc).OfClass(
                __import__("Autodesk.Revit.DB.Plumbing", fromlist=["PipeType"]).PipeType).FirstElementId()
            for segment in args.get("segments") or []:
                p1, p2 = segment[0], segment[1]
                z1 = level.Elevation + to_ft(p1[2] if len(p1) > 2 else 0)
                z2 = level.Elevation + to_ft(p2[2] if len(p2) > 2 else 0)
                start = XYZ(to_ft(p1[0]), to_ft(p1[1]), z1)
                end = XYZ(to_ft(p2[0]), to_ft(p2[1]), z2)
                if start.DistanceTo(end) < 0.01:
                    continue
                pipe = Pipe.Create(doc, system_id, pipe_type_id, level.Id, start, end)
                diameter = args.get("diameter_mm")
                if diameter and pipe is not None:
                    try:
                        parameter = pipe.get_Parameter(BuiltInParameter.RBS_PIPE_DIAMETER_PARAM)
                        if parameter is not None:
                            parameter.Set(to_ft(diameter))
                    except Exception:
                        pass
                done += 1
            notes.append(u"建了 {0} 段管道".format(done))

        elif op == u"create_ducts":
            from Autodesk.Revit.DB.Mechanical import Duct
            from Autodesk.Revit.DB.Mechanical import DuctType
            from Autodesk.Revit.DB.Mechanical import MechanicalSystemType
            level = doc.GetElement(ElementId(int(args["level_id"])))
            system_id = FilteredElementCollector(doc).OfClass(MechanicalSystemType).FirstElementId()
            duct_type_id = FilteredElementCollector(doc).OfClass(DuctType).FirstElementId()
            width = args.get("width_mm") or 400
            for segment in args.get("segments") or []:
                p1, p2 = segment[0], segment[1]
                z1 = level.Elevation + to_ft(p1[2] if len(p1) > 2 else 0)
                z2 = level.Elevation + to_ft(p2[2] if len(p2) > 2 else 0)
                start = XYZ(to_ft(p1[0]), to_ft(p1[1]), z1)
                end = XYZ(to_ft(p2[0]), to_ft(p2[1]), z2)
                if start.DistanceTo(end) < 0.01:
                    continue
                duct = Duct.Create(doc, system_id, duct_type_id, level.Id, start, end)
                if duct is not None:
                    try:
                        parameter = duct.get_Parameter(BuiltInParameter.RBS_CURVE_WIDTH_PARAM)
                        if parameter is not None:
                            parameter.Set(to_ft(width))
                    except Exception:
                        pass
                done += 1
            notes.append(u"建了 {0} 段风管".format(done))

        elif op == u"load_family":
            path = args.get("path")
            if path and os.path.exists(path):
                loaded = doc.LoadFamily(path)
                notes.append(u"载入族：{0}".format(loaded))
                done = 1
            else:
                notes.append(u"文件不存在：{0}".format(path))

        else:
            transaction.RollBack()
            return {u"error": u"不支持的 op：{0}".format(op)}

        transaction.Commit()
        return {u"ok": True, u"done": done, u"notes": notes}
    except Exception as error:
        try:
            transaction.RollBack()
        except Exception:
            pass
        write_log(u"execute {0} 失败：{1}\n{2}".format(op, error, traceback.format_exc()))
        return {u"error": u"{0}".format(error)}


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "revit_context",
            "description": "读当前 Revit 环境：版本、文档名、活动视图、选择集数量、标高列表（mm）。开始任何操作前先调它。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "revit_query",
            "description": "按类别/名称查元素，返回 Id、类别、类型、标高。类别用中文：墙 楼板 门 窗 柱 梁 屋顶 天花板 楼梯 管道 风管 钢筋 房间。",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {"type": "string"},
                    "name_like": {"type": "string"},
                    "limit": {"type": "integer"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "revit_read",
            "description": "按 Id 列表读元素细节与可写参数（参数名、当前值）。",
            "parameters": {
                "type": "object",
                "properties": {"ids": {"type": "array", "items": {"type": "integer"}}},
                "required": ["ids"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "revit_selection",
            "description": "读当前选择集里的元素摘要。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "revit_geometry",
            "description": "读几何：包围盒尺寸、定位线起终点与长度、面积（m2）、体积（m3）。",
            "parameters": {
                "type": "object",
                "properties": {"ids": {"type": "array", "items": {"type": "integer"}}},
                "required": ["ids"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "revit_layers",
            "description": "列出当前项目里导入 CAD 的图层名与图元数量（用于判断哪层是墙/门窗）。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "revit_execute",
            "description": "唯一的写工具，事务内执行。op 可选：set_param（items: [{id,param,value}]）、create_walls（level_id,height_mm,segments:[[[x1,y1],[x2,y2]]]）、color（ids,rgb）、hide（ids）、isolate（ids）、load_family（path）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "op": {"type": "string"},
                    "items": {"type": "array", "items": {"type": "object"}},
                    "ids": {"type": "array", "items": {"type": "integer"}},
                    "level_id": {"type": "integer"},
                    "height_mm": {"type": "number"},
                    "segments": {"type": "array", "items": {"type": "array"}},
                    "rgb": {"type": "array", "items": {"type": "integer"}},
                    "path": {"type": "string"},
                    "diameter_mm": {"type": "number"},
                    "width_mm": {"type": "number"},
                },
                "required": ["op"],
            },
        },
    },
]

HANDLERS = {
    "revit_context": tool_context,
    "revit_query": tool_query,
    "revit_read": tool_read,
    "revit_selection": tool_selection,
    "revit_geometry": tool_geometry,
    "revit_layers": tool_layers,
    "revit_execute": tool_execute,
}


# ---------------------------------------------------------------- 网络


def read_key():
    try:
        if os.path.exists(KEY_PATH):
            return open(KEY_PATH, "r").read().strip()
    except Exception:
        pass
    return u""


def save_key(value):
    try:
        if not os.path.isdir(APP_DIR):
            os.makedirs(APP_DIR)
        f = open(KEY_PATH, "w")
        try:
            f.write(value)
        finally:
            f.close()
    except Exception:
        pass


def chat(payload, api_key):
    """调用 DeepSeek（兼容 OpenAI tools 协议）。"""
    import urllib2

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib2.Request(API_URL, body)
    request.add_header("Content-Type", "application/json")
    request.add_header("Authorization", "Bearer {0}".format(api_key))
    response = urllib2.urlopen(request, timeout=120)
    try:
        text = response.read()
    finally:
        response.close()
    return json.loads(text.decode("utf-8"))


SYSTEM_PROMPT = (
    u"你是「六吨工具箱」里的 Revit 助手，运行在用户的 Revit 2020 里。\n"
    u"规则：\n"
    u"1. 先调 revit_context 了解环境，再查元素，最后才写。\n"
    u"2. 写操作只能用 revit_execute，按参数结构给 JSON，不要编造不存在的 op。\n"
    u"3. 长度单位一律 mm；不确定时先问用户，不要猜。\n"
    u"4. 回答用简短中文，说明你做了什么、影响了几个元素。\n"
    u"5. 危险操作（删除、大批量修改）先说明影响范围。\n"
    u"6. 建模知识：\n"
    u"   · 墙：create_walls(level_id, height_mm, segments=[[[x1,y1],[x2,y2]], ...])，"
    u"坐标单位 mm，Z 由标高决定；改墙高/偏移用 set_param，参数名「无连接高度」「底部偏移」「顶部偏移」。\n"
    u"   · 管道：create_pipes(level_id, segments, diameter_mm)，走水平/立管都按起终点给。\n"
    u"   · 风管：create_ducts(level_id, segments, width_mm)。\n"
    u"   · 批量改：先 revit_query 拿 id 列表，再 revit_execute set_param items=[{id,param,value}]。\n"
    u"   · 过滤技巧：类别名用中文；要「墙高>3000」这类条件，先 revit_read 看参数名再筛选。"
)


def run_agent(history, api_key, window):
    """跑一轮对话，处理工具调用循环。"""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history
    for _ in range(MAX_ROUNDS):
        payload = {
            "model": MODEL,
            "messages": messages,
            "tools": TOOLS,
            "tool_choice": "auto",
            "temperature": 0.2,
        }
        data = chat(payload, api_key)
        message = data["choices"][0]["message"]
        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            return message.get("content") or u"", messages
        messages.append({
            "role": "assistant",
            "content": message.get("content") or "",
            "tool_calls": tool_calls,
        })
        for call in tool_calls:
            name = call["function"]["name"]
            try:
                arguments = json.loads(call["function"].get("arguments") or "{}")
            except Exception:
                arguments = {}
            window.append(u"🔧 {0} {1}".format(name, json.dumps(arguments, ensure_ascii=False)[:120]))
            handler = HANDLERS.get(name)
            if handler is None:
                result = {u"error": u"未知工具 {0}".format(name)}
            else:
                try:
                    result = handler(arguments)
                except Exception as error:
                    write_log(u"{0} 失败：{1}".format(name, traceback.format_exc()))
                    result = {u"error": u"{0}".format(error)}
            messages.append({
                "role": "tool",
                "tool_call_id": call.get("id"),
                "content": json.dumps(result, ensure_ascii=False)[:6000],
            })
    return u"（工具调用轮数超限，先做到这里）", messages


SHELL_XAML = """
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="AI 建模助手" Height="640" Width="560"
        WindowStartupLocation="CenterScreen">
  <Grid Margin="12">
    <Grid.RowDefinitions>
      <RowDefinition Height="Auto"/>
      <RowDefinition Height="Auto"/>
      <RowDefinition Height="*"/>
      <RowDefinition Height="Auto"/>
    </Grid.RowDefinitions>
    <Grid Grid.Row="0">
      <Grid.ColumnDefinitions>
        <ColumnDefinition Width="*"/>
        <ColumnDefinition Width="Auto"/>
        <ColumnDefinition Width="Auto"/>
      </Grid.ColumnDefinitions>
      <TextBlock Grid.Column="0" Text="AI 建模助手" FontSize="17" FontWeight="Bold"
                 VerticalAlignment="Center"/>
      <Button Grid.Column="1" x:Name="btnKey" Content="设置 Key" Width="96" Height="30"
              Margin="12,0,0,0"/>
      <Button Grid.Column="2" x:Name="btnClear" Content="清空对话" Width="96" Height="30"
              Margin="8,0,0,0"/>
    </Grid>
    <Grid x:Name="keyPanel" Grid.Row="1" Visibility="Collapsed" Margin="0,10,0,0">
      <Grid.ColumnDefinitions>
        <ColumnDefinition Width="Auto"/>
        <ColumnDefinition Width="*"/>
        <ColumnDefinition Width="Auto"/>
        <ColumnDefinition Width="Auto"/>
        <ColumnDefinition Width="Auto"/>
        <ColumnDefinition Width="Auto"/>
        <ColumnDefinition Width="Auto"/>
      </Grid.ColumnDefinitions>
      <TextBlock Grid.Column="0" Text="API Key" VerticalAlignment="Center"/>
      <TextBox Grid.Column="1" x:Name="keyBox" Height="28" Margin="6,0,8,0"/>
      <Button Grid.Column="2" x:Name="btnPasteKey" Content="粘贴" Width="56" Height="28"/>
      <Button Grid.Column="3" x:Name="btnSaveKey" Content="保存" Width="56" Height="28"
              Margin="6,0,0,0"/>
      <Button Grid.Column="4" x:Name="btnOpenKey" Content="打开文件" Width="72" Height="28"
              Margin="6,0,0,0"/>
      <Button Grid.Column="5" x:Name="btnReadKey" Content="读文件" Width="56" Height="28"
              Margin="6,0,0,0"/>
      <Button Grid.Column="6" x:Name="btnHideKey" Content="收起" Width="56" Height="28"
              Margin="6,0,0,0"/>
    </Grid>
    <ScrollViewer Grid.Row="2" VerticalScrollBarVisibility="Auto" Margin="0,10,0,0">
      <TextBlock x:Name="historyText" TextWrapping="Wrap" LineHeight="20"/>
    </ScrollViewer>
    <Grid Grid.Row="3" Margin="0,10,0,0">
      <Grid.ColumnDefinitions>
        <ColumnDefinition Width="*"/>
        <ColumnDefinition Width="Auto"/>
      </Grid.ColumnDefinitions>
      <TextBox x:Name="inputBox" Grid.Column="0" Height="58" AcceptsReturn="True"
               TextWrapping="Wrap" VerticalScrollBarVisibility="Auto"/>
      <Button x:Name="btnSend" Grid.Column="1" Content="发送" Width="80" Height="58" Margin="8,0,0,0"/>
    </Grid>
  </Grid>
</Window>
"""


class AgentWindow(forms.WPFWindow):
    def _clipboard_text(self):
        """绕过 WPF 命令路由直接读剪贴板（非模态窗口里 Ctrl+V 常常不生效）。"""
        for getter in (
            lambda: Clipboard.GetText(),
            lambda: System.Windows.Forms.Clipboard.GetText(),
        ):
            try:
                text = getter()
                if text:
                    return text
            except Exception:
                continue
        return u""

    def _on_key_ctrl_v(self, sender, args):
        try:
            if args.Key == Key.V and (Keyboard.Modifiers & ModifierKeys.Control):
                text = self._clipboard_text().strip()
                if text:
                    self.FindName("keyBox").Text = text
                args.Handled = True
        except Exception:
            write_log(traceback.format_exc())

    def _on_input_ctrl_v(self, sender, args):
        try:
            if args.Key == Key.V and (Keyboard.Modifiers & ModifierKeys.Control):
                text = self._clipboard_text()
                box = self.FindName("inputBox")
                if text:
                    box.Text = (box.Text or u"") + text
                    box.CaretIndex = len(box.Text)
                args.Handled = True
        except Exception:
            write_log(traceback.format_exc())

    def _on_open_key_file(self, sender, args):
        """用记事本打开 Key 文件，在那儿粘贴最稳。"""
        try:
            path = KEY_PATH
            folder = os.path.dirname(path)
            if not os.path.isdir(folder):
                os.makedirs(folder)
            if not os.path.isfile(path):
                f = open(path, "wb")
                try:
                    f.write(u"")
                finally:
                    f.close()
            os.startfile(path)
            self.append(u"已用记事本打开：")
            self.append(path)
            self.append(u"把 Key 粘到记事本里 → Ctrl+S 保存 → 回来点「读文件」。")
        except Exception:
            self.append(u"打开失败：{0}".format(traceback.format_exc().splitlines()[-1]))

    def _on_read_key_file(self, sender, args):
        try:
            path = KEY_PATH
            if not os.path.isfile(path):
                self.append(u"还没有文件：")
                self.append(path)
                self.append(u"先点「打开文件」，在记事本里粘贴并保存。")
                return
            f = open(path, "rb")
            try:
                text = f.read().decode("utf-8", "ignore")
            finally:
                f.close()
            text = (text or u"").strip()
            if not text:
                self.append(u"文件还是空的，先在记事本里粘贴 Key 再保存。")
                return
            self.FindName("keyBox").Text = text
            self._on_save_key(None, None)
        except Exception:
            self.append(u"读文件失败：{0}".format(traceback.format_exc().splitlines()[-1]))

    def _on_paste_key(self, sender, args):
        try:
            text = self._clipboard_text().strip()
            if text:
                self.FindName("keyBox").Text = text
            else:
                self.append(u"⚠ 剪贴板是空的，或者读不到剪贴板内容。")
        except Exception:
            write_log(traceback.format_exc())

    def _on_loaded(self, sender, args):
        try:
            self.Activate()
            self.Focus()
            self.FindName("inputBox").Focus()
        except Exception:
            write_log(traceback.format_exc())

    def __init__(self, api_key):
        forms.WPFWindow.__init__(self, SHELL_XAML, literal_string=True)
        self.api_key = api_key
        self.FindName("btnPasteKey").Click += self._on_paste_key
        self.FindName("btnOpenKey").Click += self._on_open_key_file
        self.FindName("btnReadKey").Click += self._on_read_key_file
        self.FindName("keyBox").PreviewKeyDown += self._on_key_ctrl_v
        self.FindName("inputBox").PreviewKeyDown += self._on_input_ctrl_v
        self.Loaded += self._on_loaded
        self.history = []
        self.history_text = self.FindName("historyText")
        self.input_box = self.FindName("inputBox")
        self.FindName("btnSend").Click += self._on_send
        self.FindName("btnClear").Click += self._on_clear
        self.FindName("btnKey").Click += self._on_key
        self.FindName("btnSaveKey").Click += self._on_save_key
        self.FindName("btnHideKey").Click += self._on_hide_key
        self.append(u"说一句话，我来调工具操作 Revit。例如：")
        self.append(u"· 把当前视图里的墙标高改成 F02")
        self.append(u"· 统计一下有多少个窗，面积多大")
        self.append(u"· 在原点画一圈 6m×4m 的墙，高度 3000")

    def append(self, text):
        try:
            current = self.history_text.Text or u""
            self.history_text.Text = (current + u"\n" + text).strip()
        except Exception:
            pass

    def _show_key_row(self):
        try:
            self.FindName("keyBox").Text = self.api_key or u""
            self.FindName("keyPanel").Visibility = Visibility.Visible
            self.Activate()
            self.Focus()
            self.FindName("keyBox").Focus()
            self.FindName("keyBox").SelectAll()
        except Exception:
            write_log(traceback.format_exc())

    def _on_key(self, sender, args):
        self._show_key_row()

    def _on_save_key(self, sender, args):
        value = u"%s" % (self.FindName("keyBox").Text or u"")
        if not value.strip():
            self.append(u"⚠ Key 不能为空")
            return
        self.api_key = value.strip()
        save_key(self.api_key)
        try:
            self.FindName("keyPanel").Visibility = Visibility.Collapsed
        except Exception:
            pass
        self.append(u"✅ Key 已保存，可以开始对话了")

    def _on_hide_key(self, sender, args):
        try:
            self.FindName("keyPanel").Visibility = Visibility.Collapsed
        except Exception:
            pass

    def _on_clear(self, sender, args):
        self.history = []
        self.history_text.Text = u""
        self.append(u"已清空对话")

    def _on_send(self, sender, args):
        text = (self.input_box.Text or u"").strip()
        if not text:
            return
        if not self.api_key:
            self._show_key_row()
            self.append(u"⚠ 还没有 API Key —— 在上面输入框粘贴 Key 后点「保存」")
            return
        self.input_box.Text = u""
        self.append(u"\n🙋 " + text)
        self.history.append({"role": "user", "content": text})
        try:
            answer, messages = run_agent(self.history, self.api_key, self)
            self.history.append({"role": "assistant", "content": answer})
            self.append(u"\n🤖 " + (answer or u"（没有回答）"))
        except Exception as error:
            write_log(traceback.format_exc())
            self.append(u"\n❌ {0}".format(error))


def main():
    write_log(u"== AI 建模助手（工具版） ==")
    if doc is None:
        forms.alert(u"请先打开一个项目。", exitscript=True)
    window = AgentWindow(read_key())
    window.ShowDialog()


try:
    main()
except Exception:
    write_log(traceback.format_exc())
    forms.alert(u"AI 工具助手出错：\n{0}".format(traceback.format_exc()[-800:]), title=u"AI 建模助手")
