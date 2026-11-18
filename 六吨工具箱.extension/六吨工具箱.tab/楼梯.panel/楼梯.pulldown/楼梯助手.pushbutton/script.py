# -*- coding: utf-8 -*-
"""楼梯助手（参数楼梯）

按「底/顶标高 + 梯段宽/梯井/跑数/踏步宽高/平台宽/板厚」一次生成直跑楼梯：
梯段（StairsEditScope + StairsRun）+ 自动休息平台 + 可选梯梁梯柱，
并把整套参数以 ExtensibleStorage 存在楼梯图元上，下次可读回复现。

单位：界面全部 mm，内部换算英尺。
"""
import os
import sys
import math
import traceback

import clr
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")
clr.AddReference("System")
clr.AddReference("WindowsBase")
clr.AddReference("PresentationCore")
clr.AddReference("PresentationFramework")

from System.Collections.Generic import List as DotNetList

from Autodesk.Revit.DB import (
    BuiltInCategory, BuiltInParameter, Element, ElementId, FilteredElementCollector,
    IFailuresPreprocessor, FailureProcessingResult, FailureSeverity,
    Level, Line, Transaction, XYZ, StorageType, StairsEditScope,
)
from Autodesk.Revit.DB.Architecture import (
    Stairs, StairsLanding, StairsRun, StairsRunJustification,
    StairsRunStyle, StairsType,
)
from Autodesk.Revit.DB.ExtensibleStorage import (
    AccessLevel, Entity, Schema, SchemaBuilder,
)
from Autodesk.Revit.DB.Structure import StructuralType
from Autodesk.Revit.Exceptions import OperationCanceledException
from Autodesk.Revit.UI.Selection import ObjectType

from pyrevit import forms, revit, script

doc = revit.doc
uidoc = revit.uidoc
output = script.get_output()

MM_TO_FT = 1.0 / 304.8
FT_TO_MM = 304.8
DEG = math.pi / 180.0

LOG_PATH = r"C:\Users\cyl\Documents\deepseek-harness\default-workspace\_楼梯助手日志.txt"
SCHEMA_GUID = "8f2c1d64-7b3a-4f52-9a1e-5d0c6b7e4a21"


def log_line(text):
    try:
        with open(LOG_PATH, "ab") as fp:
            fp.write((u"%s\n" % text).encode("utf-8"))
    except Exception:
        pass


class QuietPreprocessor(IFailuresPreprocessor):
    """吞掉警告，避免批量生成被弹窗打断。"""

    def PreprocessFailures(self, accessor):
        for message in accessor.GetFailureMessages():
            if message.GetSeverity() == FailureSeverity.Warning:
                accessor.DeleteWarning(message)
        return FailureProcessingResult.Continue


# ---------------------------------------------------------------- 参数与计算
def defaults():
    return {
        "base_offset": 0.0, "top_offset": 0.0,
        "run_width": 1000.0, "well_width": 90.0,
        "landing_thick": 120.0, "run_thick": 120.0,
        "tread_thick": 50.0, "riser_thick": 50.0,
        "max_riser": 165.0, "runs": 2,
        "tread": 280.0, "stair_length": 7800.0, "landing_width": 1200.0,
        "floor_thick": 50.0, "rotation": 0.0, "up_side": 0,
        "with_beams": True, "delete_rail": True,
    }


def all_levels():
    out = []
    for el in FilteredElementCollector(doc).OfClass(Level):
        try:
            out.append(el)
        except Exception:
            pass
    out.sort(key=lambda l: l.Elevation)
    return out


def level_label(level):
    try:
        return u"%s (%+.3f)" % (level.Name, level.Elevation)
    except Exception:
        return u"<标高>"


def compute(params, base_level, top_level):
    """返回 (ok, error, plan)。plan 里所有长度单位 mm。"""
    plan = {"runs": [], "total_risers": 0, "riser": 0.0,
            "net_width": 0.0, "landing": 0.0, "height": 0.0}

    if base_level is None or top_level is None:
        return False, u"请选择底标高和顶标高", plan

    height = (top_level.Elevation - base_level.Elevation) * FT_TO_MM
    height = height - params["base_offset"] + params["top_offset"]
    if height <= 100.0:
        return False, u"顶部标高必须高于底部标高", plan

    max_riser = max(params["max_riser"], 80.0)
    total = int(math.ceil(height / max_riser))
    total = max(total, 2)
    riser = height / float(total)

    runs = int(params["runs"])
    runs = min(max(runs, 1), 4)

    # 踏步余数：必须为 0（踢面数能被跑数整除）
    if total % runs != 0:
        # 允许自动微调：找到最近的能整除的踢面数（保持踏步高在允许范围）
        fixed = None
        for cand in range(total - runs + 1, total + runs + 1):
            if cand >= 2 and cand % runs == 0:
                h = height / float(cand)
                if 100.0 <= h <= max_riser + 0.001:
                    fixed = cand
                    break
        if fixed is None:
            plan["remainder"] = total % runs
            plan["total_risers"] = total
            plan["riser"] = riser
            return False, u"踏步余数不等于 0（%d 级 / %d 跑），请调整最大梯面高度或跑数" % (total, runs), plan
        total = fixed
        riser = height / float(total)

    per_run = total // runs
    tread = max(params["tread"], 200.0)
    run_width = max(params["run_width"], 600.0)
    well = max(params["well_width"], 0.0)
    net_width = run_width * 2.0 + well

    # 每跑长度：踏步数 × 踏面（末级踏步并入平台）
    run_len = tread * per_run
    stair_len = params["stair_length"]
    landing_need = params["landing_width"]

    if stair_len > 0:
        # 楼梯间净长里，除梯段外剩下的就是平台进深
        avail = stair_len - tread * (per_run - 1)
    else:
        avail = landing_need

    if avail < run_width:
        return False, u"平台宽度小于梯段宽度（%.0f < %.0f），请加大楼梯长度或减小梯段宽" % (avail, run_width), plan

    plan["height"] = height
    plan["total_risers"] = total
    plan["per_run"] = per_run
    plan["riser"] = riser
    plan["tread"] = tread
    plan["run_len"] = run_len
    plan["run_width"] = run_width
    plan["well"] = well
    plan["net_width"] = net_width
    plan["landing"] = avail
    plan["runs_count"] = runs
    return True, u"", plan


def type_name(params):
    return u"楼梯_平台板厚=%.0f_梯段板厚=%.0f_踏步=%.0f_踢面=%.0f_跑数=%d" % (
        params["landing_thick"], params["run_thick"], params["tread"],
        params["max_riser"], params["runs"])


# ---------------------------------------------------------------- 楼梯类型
def cast_in_place_types():
    out = []
    for t in FilteredElementCollector(doc).OfClass(StairsType):
        try:
            out.append(t)
        except Exception:
            pass
    return out


def pick_stairs_type(params, name):
    types = cast_in_place_types()
    if not types:
        return None, u"当前项目不存在现浇楼梯类型，请使用系统自带样板创建"

    for t in types:
        try:
            if t.Name == name:
                return t, u""
        except Exception:
            pass

    base = types[0]
    new_type = None
    try:
        new_type = base.Duplicate(name)
    except Exception:
        new_type = None
    use = new_type if new_type is not None else base

    for attr, value in (("MinRunWidth", params["run_width"] * MM_TO_FT),
                        ("MinTreadDepth", params["tread"] * MM_TO_FT),
                        ("MaxRiserHeight", params["max_riser"] * MM_TO_FT)):
        try:
            setattr(use, attr, value)
        except Exception:
            pass
    return use, u""


# ---------------------------------------------------------------- 创建楼梯
def flat_line(x1, y1, x2, y2, z):
    return Line.CreateBound(XYZ(x1, y1, z), XYZ(x2, y2, z))


def build_runs(stairs_id, base_z, plan, params):
    """在 StairsEditScope 内建直跑与平台，返回 (run_ids, 错误)。"""
    run_ids = []
    per_run = plan["per_run"]
    run_len = plan["run_len"] * MM_TO_FT
    run_w = plan["run_width"] * MM_TO_FT
    well = plan["well"] * MM_TO_FT
    landing = plan["landing"] * MM_TO_FT
    riser = plan["riser"] * MM_TO_FT

    total = plan["runs_count"]
    for i in range(total):
        # 第 i 跑：沿 Y 方向，逐跑交替
        x0 = 0.0 if i % 2 == 0 else (run_w + well)
        y0 = 0.0 if i % 2 == 0 else run_len + landing
        y1 = y0 + (run_len if i % 2 == 0 else -run_len)
        z = base_z + per_run * i * riser
        try:
            run = StairsRun.CreateStraightRun(doc, stairs_id,
                                              flat_line(x0, y0, x0, y1, z),
                                              StairsRunJustification.Center)
            run_ids.append(run.Id)
        except Exception as ex:
            return run_ids, u"第 %d 跑创建失败：%s" % (i + 1, ex)

    for i in range(len(run_ids) - 1):
        try:
            StairsLanding.CreateAutomaticLanding(doc, run_ids[i], run_ids[i + 1])
        except Exception:
            pass
    return run_ids, u""


def find_symbol(words, category):
    hits = []
    for el in FilteredElementCollector(doc).OfCategory(category).WhereElementIsElementType():
        try:
            name = el.Name or u""
        except Exception:
            name = u""
        note = u""
        try:
            p = el.get_Parameter(BuiltInParameter.ALL_MODEL_TYPE_COMMENTS)
            if p is not None:
                note = p.AsString() or u""
        except Exception:
            pass
        blob = (name + u"|" + note).upper()
        if any(w.upper() in blob for w in words):
            hits.append(el)
    return hits[0] if hits else None


def place_beams(run_ids, base_level, plan):
    """沿每跑两侧放梯梁（结构框架），返回 (数量, 提示)。"""
    beam = find_symbol([u"TL"], BuiltInCategory.OST_StructuralFraming)
    if beam is None:
        return 0, u"当前项目不存在梯梁或梯柱（类型名称或类型注释必须等于：TL、TZ）"

    placed = 0
    per_run = plan["per_run"]
    run_len = plan["run_len"] * MM_TO_FT
    run_w = plan["run_width"] * MM_TO_FT
    well = plan["well"] * MM_TO_FT
    riser = plan["riser"] * MM_TO_FT
    landing = plan["landing"] * MM_TO_FT

    for idx, rid in enumerate(run_ids):
        run = doc.GetElement(rid)
        if run is None:
            continue
        z = run.BaseElevation
        x0 = 0.0 if idx % 2 == 0 else (run_w + well)
        y0 = 0.0 if idx % 2 == 0 else run_len + landing
        y1 = y0 + (run_len if idx % 2 == 0 else -run_len)
        if y1 < y0:
            y0, y1 = y1, y0
        for side in (-1, 1):
            xx = x0 + side * (run_w / 2.0)
            try:
                doc.Create.NewFamilyInstance(flat_line(xx, y0, xx, y1, z - 0.02),
                                             beam, base_level, StructuralType.Beam)
                placed += 1
            except Exception:
                pass
    return placed, u""


def place_columns(base_level, plan, params):
    col = find_symbol([u"TZ"], BuiltInCategory.OST_StructuralColumns)
    if col is None:
        return 0
    run_w = plan["run_width"] * MM_TO_FT
    well = plan["well"] * MM_TO_FT
    run_len = plan["run_len"] * MM_TO_FT
    landing = plan["landing"] * MM_TO_FT
    z = base_level.Elevation

    placed = 0
    for x in (0.0, run_w, run_w + well, 2 * run_w + well):
        for y in (0.0, run_len + landing):
            try:
                doc.Create.NewFamilyInstance(XYZ(x, y, z), col, base_level,
                                             StructuralType.NonStructural)
                placed += 1
            except Exception:
                pass
    return placed


# ---------------------------------------------------------------- 配置存取
def _schema():
    schema = Schema.Lookup(__import__("System").Guid(SCHEMA_GUID))
    if schema is not None:
        return schema
    builder = SchemaBuilder(__import__("System").Guid(SCHEMA_GUID))
    builder.SetSchemaName("LiuDunStairConfig")
    builder.SetVendorId("LIUDUN")
    builder.SetReadAccessLevel(AccessLevel.Public)
    builder.SetWriteAccessLevel(AccessLevel.Public)
    builder.AddSimpleField("config", clr.GetClrType(str))
    return builder.Finish()


def save_config(element, text):
    schema = _schema()
    entity = Entity(schema)
    entity.Set("config", text)
    element.SetEntity(entity)


def load_config(element):
    schema = Schema.Lookup(__import__("System").Guid(SCHEMA_GUID))
    if schema is None:
        return None
    try:
        entity = element.GetEntity(schema)
        if entity is None or entity.Schema is None:
            return None
        return entity.Get("config")
    except Exception:
        return None


def params_to_text(params, base_name, top_name):
    keys = sorted(params.keys())
    parts = []
    for k in keys:
        parts.append(u"%s=%s" % (k, params[k]))
    parts.append(u"base=%s" % base_name)
    parts.append(u"top=%s" % top_name)
    return u";".join(parts)


def text_to_params(text):
    out = {}
    for chunk in (text or u"").split(u";"):
        if u"=" not in chunk:
            continue
        k, v = chunk.split(u"=", 1)
        out[k.strip()] = v.strip()
    return out


# ---------------------------------------------------------------- 主流程
def create_stairs(params, base_level, top_level):
    ok, error, plan = compute(params, base_level, top_level)
    if not ok:
        return False, error

    staircase_type, terr = pick_stairs_type(params, type_name(params))
    if staircase_type is None:
        return False, terr

    scope = StairsEditScope(doc, u"参数楼梯")
    stairs_id = ElementId.InvalidElementId
    try:
        stairs_id = scope.Start(base_level.Id, top_level.Id)
    except Exception as ex:
        return False, u"无法进入楼梯编辑模式：%s" % ex

    runs_ok = False
    run_ids = []
    try:
        tx = Transaction(doc, u"生成梯段")
        tx.Start()
        run_ids, rerr = build_runs(stairs_id, base_level.Elevation + params["base_offset"] * MM_TO_FT,
                                   plan, params)
        if rerr:
            tx.RollBack()
            scope.Cancel()
            return False, rerr
        tx.Commit()
        runs_ok = True
    except Exception as ex:
        try:
            scope.Cancel()
        except Exception:
            pass
        return False, u"生成梯段失败：%s" % ex

    if not runs_ok:
        scope.Cancel()
        return False, u"生成梯段失败"

    try:
        scope.Commit(QuietPreprocessor())
    except Exception as ex:
        return False, u"提交楼梯失败：%s" % ex

    stairs = doc.GetElement(stairs_id)
    if stairs is None:
        return False, u"楼梯创建后找不到图元"

    # 旋转
    rot = params.get("rotation", 0.0)
    if abs(rot) > 0.001:
        try:
            tx = Transaction(doc, u"旋转楼梯")
            tx.Start()
            axis = Line.CreateBound(XYZ(0, 0, base_level.Elevation),
                                    XYZ(0, 0, base_level.Elevation + 1.0))
            from Autodesk.Revit.DB import ElementTransformUtils
            ElementTransformUtils.RotateElement(doc, stairs_id, axis, rot * DEG)
            tx.Commit()
        except Exception as ex:
            log_line(u"旋转失败：%s" % ex)

    beams = cols = 0
    if params.get("with_beams"):
        try:
            tx = Transaction(doc, u"梯梁梯柱")
            tx.Start()
            beams, berr = place_beams(run_ids, base_level, plan)
            cols = place_columns(base_level, plan, params)
            tx.Commit()
        except Exception:
            pass
        if berr:
            log_line(berr)

    if params.get("delete_rail"):
        try:
            tx = Transaction(doc, u"清理栏杆")
            tx.Start()
            for c in FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_StairsRailing):
                if c.HostId == stairs_id:
                    doc.Delete(c.Id)
            tx.Commit()
        except Exception:
            pass

    try:
        tx = Transaction(doc, u"记录参数")
        tx.Start()
        text = params_to_text(params, base_level.Name, top_level.Name)
        save_config(stairs, text)
        tx.Commit()
    except Exception as ex:
        log_line(u"写配置失败：%s" % ex)

    log_line(u"创建完成 stairs=%s 踏步=%d 跑=%d 梁=%d 柱=%d" % (
        stairs_id.IntegerValue, plan["total_risers"], plan["runs_count"], beams, cols))

    msg = [u"已创建楼梯：%d 级踢面 / %d 跑，踏步高 %.1f mm，踏面 %.0f mm" % (
        plan["total_risers"], plan["runs_count"], plan["riser"], plan["tread"]),
        u"梯段宽 %.0f mm，梯井 %.0f mm，平台进深 %.0f mm" % (
            plan["run_width"], plan["well"], plan["landing"])]
    if beams or cols:
        msg.append(u"梯梁 %d 根，梯柱 %d 根" % (beams, cols))
    return True, u"\n".join(msg)


# ---------------------------------------------------------------- 界面
XAML = u"""<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="参数楼梯" Height="720" Width="620" WindowStartupLocation="CenterScreen">
  <ScrollViewer VerticalScrollBarVisibility="Auto" HorizontalScrollBarVisibility="Disabled">
    <StackPanel Margin="16">
      <TextBlock x:Name="titleText" Text="参数楼梯" FontSize="20" FontWeight="Bold"/>
      <TextBlock x:Name="hintText" Text="按参数生成现浇直跑楼梯（梯段 + 平台 + 可选梯梁梯柱）"
                 Foreground="#4A5568" Margin="0,4,0,12"/>

      <TextBlock Text="① 高度" FontWeight="Bold"/>
      <Grid Margin="0,6,0,10">
        <Grid.ColumnDefinitions>
          <ColumnDefinition Width="Auto"/><ColumnDefinition Width="*"/>
          <ColumnDefinition Width="Auto"/><ColumnDefinition Width="*"/>
        </Grid.ColumnDefinitions>
        <Grid.RowDefinitions>
          <RowDefinition/><RowDefinition/>
        </Grid.RowDefinitions>
        <TextBlock Grid.Row="0" Grid.Column="0" Text="底标高" VerticalAlignment="Center" Margin="0,0,8,6"/>
        <ComboBox x:Name="baseBox" Grid.Row="0" Grid.Column="1" Height="28" Margin="0,0,12,6"/>
        <TextBlock Grid.Row="0" Grid.Column="2" Text="顶标高" VerticalAlignment="Center" Margin="0,0,8,6"/>
        <ComboBox x:Name="topBox" Grid.Row="0" Grid.Column="3" Height="28" Margin="0,0,0,6"/>
        <TextBlock Grid.Row="1" Grid.Column="0" Text="底部偏移" VerticalAlignment="Center" Margin="0,0,8,0"/>
        <TextBox x:Name="baseOffBox" Grid.Row="1" Grid.Column="1" Height="28" Margin="0,0,12,0"/>
        <TextBlock Grid.Row="1" Grid.Column="2" Text="顶部偏移" VerticalAlignment="Center" Margin="0,0,8,0"/>
        <TextBox x:Name="topOffBox" Grid.Row="1" Grid.Column="3" Height="28"/>
      </Grid>

      <TextBlock Text="② 楼梯间净宽" FontWeight="Bold"/>
      <Grid Margin="0,6,0,10">
        <Grid.ColumnDefinitions>
          <ColumnDefinition Width="Auto"/><ColumnDefinition Width="*"/>
          <ColumnDefinition Width="Auto"/><ColumnDefinition Width="*"/>
        </Grid.ColumnDefinitions>
        <TextBlock Text="梯段宽度" VerticalAlignment="Center" Margin="0,0,8,0"/>
        <TextBox x:Name="runWidthBox" Grid.Column="1" Height="28" Margin="0,0,12,0"/>
        <TextBlock Grid.Column="2" Text="梯井宽度" VerticalAlignment="Center" Margin="0,0,8,0"/>
        <TextBox x:Name="wellBox" Grid.Column="3" Height="28"/>
      </Grid>

      <TextBlock Text="③ 尺寸与类型" FontWeight="Bold"/>
      <Grid Margin="0,6,0,10">
        <Grid.ColumnDefinitions>
          <ColumnDefinition Width="Auto"/><ColumnDefinition Width="*"/>
          <ColumnDefinition Width="Auto"/><ColumnDefinition Width="*"/>
        </Grid.ColumnDefinitions>
        <Grid.RowDefinitions>
          <RowDefinition/><RowDefinition/><RowDefinition/><RowDefinition/><RowDefinition/>
        </Grid.RowDefinitions>
        <TextBlock Grid.Row="0" Text="平台板厚" VerticalAlignment="Center" Margin="0,0,8,6"/>
        <TextBox x:Name="landingThickBox" Grid.Row="0" Grid.Column="1" Height="28" Margin="0,0,12,6"/>
        <TextBlock Grid.Row="0" Grid.Column="2" Text="楼梯板厚" VerticalAlignment="Center" Margin="0,0,8,6"/>
        <TextBox x:Name="runThickBox" Grid.Row="0" Grid.Column="3" Height="28" Margin="0,0,0,6"/>

        <TextBlock Grid.Row="1" Text="踏板厚度" VerticalAlignment="Center" Margin="0,0,8,6"/>
        <TextBox x:Name="treadThickBox" Grid.Row="1" Grid.Column="1" Height="28" Margin="0,0,12,6"/>
        <TextBlock Grid.Row="1" Grid.Column="2" Text="踢面厚度" VerticalAlignment="Center" Margin="0,0,8,6"/>
        <TextBox x:Name="riserThickBox" Grid.Row="1" Grid.Column="3" Height="28" Margin="0,0,0,6"/>

        <TextBlock Grid.Row="2" Text="最大梯面高" VerticalAlignment="Center" Margin="0,0,8,6"/>
        <TextBox x:Name="maxRiserBox" Grid.Row="2" Grid.Column="1" Height="28" Margin="0,0,12,6"/>
        <TextBlock Grid.Row="2" Grid.Column="2" Text="楼梯跑数" VerticalAlignment="Center" Margin="0,0,8,6"/>
        <ComboBox x:Name="runsBox" Grid.Row="2" Grid.Column="3" Height="28" Margin="0,0,0,6"/>

        <TextBlock Grid.Row="3" Text="踏步宽度" VerticalAlignment="Center" Margin="0,0,8,6"/>
        <TextBox x:Name="treadBox" Grid.Row="3" Grid.Column="1" Height="28" Margin="0,0,12,6"/>
        <TextBlock Grid.Row="3" Grid.Column="2" Text="楼梯长度" VerticalAlignment="Center" Margin="0,0,8,6"/>
        <TextBox x:Name="lengthBox" Grid.Row="3" Grid.Column="3" Height="28" Margin="0,0,0,6"/>

        <TextBlock Grid.Row="4" Text="平台宽度" VerticalAlignment="Center" Margin="0,0,8,0"/>
        <TextBox x:Name="landingBox" Grid.Row="4" Grid.Column="1" Height="28" Margin="0,0,12,0"/>
        <TextBlock Grid.Row="4" Grid.Column="2" Text="建筑楼板厚" VerticalAlignment="Center" Margin="0,0,8,0"/>
        <TextBox x:Name="floorThickBox" Grid.Row="4" Grid.Column="3" Height="28"/>
      </Grid>

      <Grid Margin="0,4,0,8">
        <Grid.ColumnDefinitions>
          <ColumnDefinition Width="Auto"/><ColumnDefinition Width="*"/><ColumnDefinition Width="Auto"/>
        </Grid.ColumnDefinitions>
        <TextBlock Text="上楼侧" VerticalAlignment="Center" Margin="0,0,8,0"/>
        <ComboBox x:Name="upBox" Grid.Column="1" Height="28" Margin="0,0,12,0"/>
        <TextBlock Grid.Column="2" Text="旋转角(°)" VerticalAlignment="Center" Margin="0,0,8,0"/>
        <TextBox x:Name="rotBox" Grid.Column="3" Width="70" Height="28"/>
      </Grid>

      <CheckBox x:Name="beamCheck" Content="创建梯梁 / 梯柱（TL / TZ 族）" Margin="0,2,0,2"/>
      <CheckBox x:Name="railCheck" Content="创建后删除该楼梯的栏杆" Margin="0,2,0,8"/>

      <TextBlock x:Name="statText" Text="" Foreground="#1F6F54" Margin="0,0,0,4"/>
      <TextBlock x:Name="warnText" Text="" Foreground="#C0392B" TextWrapping="Wrap" Margin="0,0,0,8"/>

      <ListBox x:Name="runList" Height="110" Margin="0,0,0,10"/>

      <Grid>
        <Grid.ColumnDefinitions>
          <ColumnDefinition Width="Auto"/><ColumnDefinition Width="*"/>
          <ColumnDefinition Width="Auto"/><ColumnDefinition Width="Auto"/>
        </Grid.ColumnDefinitions>
        <Button x:Name="readBtn" Content="选择楼梯读取参数" Height="34" Margin="0,0,10,0"/>
        <Button x:Name="pickBtn" Grid.Column="1" Content="点选定位点…" Height="34" Margin="0,0,10,0"/>
        <Button x:Name="createBtn" Grid.Column="2" Content="创建楼梯" Height="34" Width="130" FontWeight="Bold" Margin="0,0,10,0"/>
        <Button x:Name="closeBtn" Grid.Column="3" Content="关闭" Height="34" Width="90"/>
      </Grid>
    </StackPanel>
  </ScrollViewer>
</Window>"""


class StairWindow(forms.WPFWindow):
    def __init__(self, levels, params, origin=None):
        forms.WPFWindow.__init__(self, XAML, literal_string=True)
        self.levels = levels
        self.origin = origin
        self.action = None
        self.params = dict(params)

        self.base_box = self.FindName("baseBox")
        self.top_box = self.FindName("topBox")
        for lv in levels:
            self.base_box.Items.Add(level_label(lv))
            self.top_box.Items.Add(level_label(lv))
        self.base_box.SelectedIndex = 0
        self.top_box.SelectedIndex = min(1, max(0, len(levels) - 1))

        self.base_off = self.FindName("baseOffBox")
        self.top_off = self.FindName("topOffBox")
        self.run_width = self.FindName("runWidthBox")
        self.well = self.FindName("wellBox")
        self.landing_thick = self.FindName("landingThickBox")
        self.run_thick = self.FindName("runThickBox")
        self.tread_thick = self.FindName("treadThickBox")
        self.riser_thick = self.FindName("riserThickBox")
        self.max_riser = self.FindName("maxRiserBox")
        self.runs_box = self.FindName("runsBox")
        self.tread = self.FindName("treadBox")
        self.length = self.FindName("lengthBox")
        self.landing = self.FindName("landingBox")
        self.floor_thick = self.FindName("floorThickBox")
        self.up_box = self.FindName("upBox")
        self.rot = self.FindName("rotBox")
        self.beam_check = self.FindName("beamCheck")
        self.rail_check = self.FindName("railCheck")
        self.stat = self.FindName("statText")
        self.warn = self.FindName("warnText")
        self.run_list = self.FindName("runList")
        self.create_btn = self.FindName("createBtn")

        for i in (1, 2, 3, 4):
            self.runs_box.Items.Add(u"%d 跑" % i)
        self.up_box.Items.Add(u"左侧上楼")
        self.up_box.Items.Add(u"右侧上楼")

        self.base_off.Text = u"0"
        self.top_off.Text = u"0"
        self.run_width.Text = u"1000"
        self.well.Text = u"90"
        self.landing_thick.Text = u"120"
        self.run_thick.Text = u"120"
        self.tread_thick.Text = u"50"
        self.riser_thick.Text = u"50"
        self.max_riser.Text = u"165"
        self.runs_box.SelectedIndex = 1
        self.tread.Text = u"280"
        self.length.Text = u"7800"
        self.landing.Text = u"1200"
        self.floor_thick.Text = u"50"
        self.up_box.SelectedIndex = 0
        self.rot.Text = u"0"
        self.beam_check.IsChecked = True
        self.rail_check.IsChecked = True

        self._load_params(self.params)

        self.base_box.SelectionChanged += lambda s, e: self._refresh()
        self.top_box.SelectionChanged += lambda s, e: self._refresh()
        self.runs_box.SelectionChanged += lambda s, e: self._refresh()
        for box in (self.base_off, self.top_off, self.run_width, self.well,
                    self.landing_thick, self.run_thick, self.tread_thick, self.riser_thick,
                    self.max_riser, self.tread, self.length, self.landing, self.floor_thick):
            box.TextChanged += lambda s, e: self._refresh()

        self.readBtn = self.FindName("readBtn")
        self.pickBtn = self.FindName("pickBtn")
        self.closeBtn = self.FindName("closeBtn")
        self.readBtn.Click += self._on_read
        self.pickBtn.Click += self._on_pick
        self.create_btn.Click += self._on_create
        self.closeBtn.Click += self._on_close

        self._refresh()

    # -------- 参数读写
    def _num(self, box, fallback=0.0):
        try:
            return float(box.Text.strip())
        except Exception:
            return fallback

    def _collect(self):
        p = defaults()
        p["base_offset"] = self._num(self.base_off, 0.0)
        p["top_offset"] = self._num(self.top_off, 0.0)
        p["run_width"] = self._num(self.run_width, 1000.0)
        p["well_width"] = self._num(self.well, 90.0)
        p["landing_thick"] = self._num(self.landing_thick, 120.0)
        p["run_thick"] = self._num(self.run_thick, 120.0)
        p["tread_thick"] = self._num(self.tread_thick, 50.0)
        p["riser_thick"] = self._num(self.riser_thick, 50.0)
        p["max_riser"] = self._num(self.max_riser, 165.0)
        p["runs"] = max(1, int(self.runs_box.SelectedIndex) + 1)
        p["tread"] = self._num(self.tread, 280.0)
        p["stair_length"] = self._num(self.length, 7800.0)
        p["landing_width"] = self._num(self.landing, 1200.0)
        p["floor_thick"] = self._num(self.floor_thick, 50.0)
        p["rotation"] = self._num(self.rot, 0.0)
        p["up_side"] = max(0, int(self.up_box.SelectedIndex))
        p["with_beams"] = bool(self.beam_check.IsChecked)
        p["delete_rail"] = bool(self.rail_check.IsChecked)
        return p

    def _load_params(self, data):
        try:
            if not data:
                return
            if "base_offset" in data:
                self.base_off.Text = u"%s" % data["base_offset"]
            if "top_offset" in data:
                self.top_off.Text = u"%s" % data["top_offset"]
            for key, box in (("run_width", self.run_width), ("well_width", self.well),
                             ("landing_thick", self.landing_thick), ("run_thick", self.run_thick),
                             ("tread_thick", self.tread_thick), ("riser_thick", self.riser_thick),
                             ("max_riser", self.max_riser), ("tread", self.tread),
                             ("stair_length", self.length), ("landing_width", self.landing),
                             ("floor_thick", self.floor_thick), ("rotation", self.rot)):
                if key in data:
                    box.Text = u"%s" % data[key]
            if "runs" in data:
                try:
                    self.runs_box.SelectedIndex = max(0, int(float(data["runs"])) - 1)
                except Exception:
                    pass
            if data.get("base"):
                for i, lv in enumerate(self.levels):
                    if lv.Name == data["base"]:
                        self.base_box.SelectedIndex = i
                        break
            if data.get("top"):
                for i, lv in enumerate(self.levels):
                    if lv.Name == data["top"]:
                        self.top_box.SelectedIndex = i
                        break
        except Exception:
            pass

    # -------- 实时校验
    def _refresh(self):
        p = self._collect()
        base_lv = self._level(self.base_box)
        top_lv = self._level(self.top_box)
        ok, error, plan = compute(p, base_lv, top_lv)

        self.run_list.Items.Clear()
        if ok:
            for i in range(plan["runs_count"]):
                text = u"第 %d 跑：%d 级踏步 · 宽 %.0f · 平台 %.0f" % (
                    i + 1, plan["per_run"], plan["run_width"], plan["landing"])
                self.run_list.Items.Add(text)
            self.stat.Text = u"高度 %.0f mm · 净宽 %.0f mm · 踏步高 %.1f mm · 类型 %s" % (
                plan["height"], plan["net_width"], plan["riser"], type_name(p))
            self.warn.Text = u""
            self.create_btn.IsEnabled = True
        else:
            self.stat.Text = u""
            self.warn.Text = error
            self.create_btn.IsEnabled = False

    def _level(self, combo):
        try:
            idx = int(combo.SelectedIndex)
            if 0 <= idx < len(self.levels):
                return self.levels[idx]
        except Exception:
            pass
        return None

    # -------- 动作
    def _on_read(self, sender, args):
        self.action = "read"
        self.Close()

    def _on_pick(self, sender, args):
        self.action = "pick"
        self.Close()

    def _on_create(self, sender, args):
        self.action = "create"
        self.Close()

    def _on_close(self, sender, args):
        self.action = None
        self.Close()


def _pick_point():
    return uidoc.Selection.PickPoint(u"请点击楼梯放置定位点")


def run_window(levels, params, origin):
    result = {"action": None, "params": params, "origin": origin}
    while True:
        window = StairWindow(levels, result["params"], result["origin"])
        window.ShowDialog()
        action = window.action
        if action is None:
            return result
        if action == "create":
            result["params"] = window._collect()
            base_lv = window._level(window.base_box)
            top_lv = window._level(window.top_box)
            ok, msg = create_stairs(result["params"], base_lv, top_lv)
            forms.alert(msg, title=u"参数楼梯", warn_icon=(not ok))
            result["params"] = window._collect()
            continue
        if action == "read":
            result["params"] = window._collect()
            try:
                ref = uidoc.Selection.PickObject(ObjectType.Element, u"请点选一根由本功能创建的楼梯")
                element = doc.GetElement(ref.ElementId)
            except OperationCanceledException:
                continue
            except Exception:
                continue
            text = load_config(element) if element is not None else None
            if not text:
                forms.alert(u"当前选择楼梯无法获取配置属性。", title=u"参数楼梯", warn_icon=True)
                continue
            data = text_to_params(text)
            result["params"] = data
            continue
        if action == "pick":
            result["params"] = window._collect()
            try:
                point = _pick_point()
                result["origin"] = point
            except OperationCanceledException:
                pass
            except Exception:
                pass
            continue


def main():
    log_line(u"== 参数楼梯启动 ==")
    if doc is None:
        forms.alert(u"请先打开一个项目。", exitscript=True)
    levels = all_levels()
    if not levels:
        forms.alert(u"项目里没有标高。", exitscript=True)
    params = defaults()
    run_window(levels, params, None)


try:
    main()
except Exception:
    log_line(traceback.format_exc())
    forms.alert(u"参数楼梯出错：\n%s" % traceback.format_exc()[-800:], title=u"参数楼梯", warn_icon=True)
