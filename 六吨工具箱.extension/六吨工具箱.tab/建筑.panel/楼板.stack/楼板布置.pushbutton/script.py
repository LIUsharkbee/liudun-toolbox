# -*- coding: utf-8 -*-
"""楼板布置：按房间边界自动生成楼板（类似斯维尔的「板布置」）。

两种方式：
1. 点内部生成：在房间内部点一下，按该房间边界生成一块楼板；
2. 选中房间生成：先选中若干房间，再一次性生成。

只读判断 + 单事务写入，可 Ctrl+Z。
"""

import os
import traceback

from System.Collections.Generic import List as DotNetList

from Autodesk.Revit.DB import (
    BuiltInCategory,
    BuiltInParameter,
    CurveArray,
    ElementId,
    FilteredElementCollector,
    FloorType,
    Level,
    SpatialElementBoundaryOptions,
    Transaction,
    Transform,
    ViewDetailLevel,
    XYZ,
)

try:
    from Autodesk.Revit.DB.Architecture import Room
except Exception:
    Room = None

from pyrevit import revit, forms, script

doc = revit.doc
uidoc = revit.uidoc
output = script.get_output()

FT_TO_MM = 304.8
MM_TO_FT = 1.0 / 304.8

LOG_PATH = os.path.join(
    os.environ.get("USERPROFILE", os.path.expanduser("~")),
    "Documents", u"六吨工具箱-楼板布置日志.txt")


def write_log(lines):
    try:
        handle = open(LOG_PATH, "a")
        for line in lines:
            handle.write(line + u"\n")
        handle.close()
    except Exception:
        pass


# ---------------------------------------------------------------- 基础数据

def type_name(element_type):
    """多路兜底取类型名（IronPython 下 .Name 可能抛 AttributeError）。"""
    for attr in ("Name", "get_Name"):
        try:
            value = getattr(element_type, attr)
            if callable(value):
                value = value()
            if value:
                return value
        except Exception:
            continue
    try:
        param = element_type.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM)
        if param is not None:
            text = param.AsString()
            if text:
                return text
    except Exception:
        pass
    try:
        return u"{0}".format(element_type.Id.IntegerValue)
    except Exception:
        return u""


def is_foundation(floor_type):
    try:
        if floor_type.IsFoundationSlab:
            return True
    except Exception:
        pass
    try:
        name = type_name(floor_type)
    except Exception:
        name = u""
    for word in (u"基础", u"筏板", u"承台", u"Foundation"):
        if word in name:
            return True
    return False


def all_floor_types():
    """建筑楼板类型（排除基础板），六吨自己生成的类型排到最后。"""
    normal, ours = [], []
    collector = FilteredElementCollector(doc).OfClass(FloorType)
    for floor_type in collector:
        if floor_type is None or is_foundation(floor_type):
            continue
        try:
            name = type_name(floor_type)
        except Exception:
            name = u""
        if name.startswith(u"六吨-"):
            ours.append(floor_type)
        else:
            normal.append(floor_type)
    normal.sort(key=type_name)
    ours.sort(key=type_name)
    return normal + ours


def floor_thickness_mm(floor_type):
    try:
        structure = floor_type.GetCompoundStructure()
        if structure is None:
            return 0.0
        return round(structure.GetWidth() * FT_TO_MM, 1)
    except Exception:
        return 0.0


def all_levels():
    levels = []
    collector = FilteredElementCollector(doc).OfClass(Level)
    for level in collector:
        try:
            levels.append(level)
        except Exception:
            continue
    levels.sort(key=lambda l: l.Elevation)
    return levels


def is_room(element):
    if element is None:
        return False
    if Room is not None:
        try:
            return isinstance(element, Room)
        except Exception:
            pass
    try:
        return element.Category is not None and \
            element.Category.Id.IntegerValue == int(BuiltInCategory.OST_Rooms)
    except Exception:
        return False


def room_area(room):
    try:
        return room.Area
    except Exception:
        return 0.0


def all_rooms():
    rooms = []
    collector = FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_Rooms)
    for element in collector:
        if not is_room(element):
            continue
        if room_area(element) <= 0:
            continue
        rooms.append(element)
    return rooms


def room_at(point):
    """找包含该点的房间（只在房间所在标高附近找）。"""
    best = None
    for room in all_rooms():
        try:
            if room.IsPointInRoom(point):
                return room
        except Exception:
            continue
    return best


def level_name(level):
    try:
        text = level.Name
        if text:
            return text
    except Exception:
        pass
    try:
        param = level.get_Parameter(BuiltInParameter.DATUM_TEXT)
        if param is not None:
            return param.AsString() or u""
    except Exception:
        pass
    return u"标高"


def room_name(room):
    try:
        name = room.get_Parameter(BuiltInParameter.ROOM_NAME).AsString()
    except Exception:
        name = None
    if not name:
        try:
            raw = room.Name
            if raw:
                name = raw
        except Exception:
            name = u""
    try:
        number = room.Number
    except Exception:
        number = u""
    if number and name:
        return u"{0} {1}".format(number, name)
    return name or u"房间"


# ---------------------------------------------------------------- 边界

def boundary_options():
    options = SpatialElementBoundaryOptions()
    try:
        options.SpatialElementBoundaryLocation = \
            SpatialElementBoundaryOptions.GetSpatialElementBoundaryLocation(
                doc, SpatialElementBoundaryOptions.SpatialElementBoundaryLocation.Finish)
    except Exception:
        try:
            options.SpatialElementBoundaryLocation = 1
        except Exception:
            pass
    return options


def room_loops(room, base_z):
    """把房间边界转成若干水平 CurveArray（z 对齐到楼板标高）。"""
    loops = []
    try:
        segments = room.GetBoundarySegments(boundary_options())
    except Exception:
        return loops
    if not segments:
        return loops

    for segment_list in segments:
        curves = []
        for segment in segment_list:
            try:
                curve = segment.GetCurve()
            except Exception:
                curve = None
            if curve is None:
                continue
            try:
                start = curve.GetEndPoint(0)
                dz = base_z - start.Z
                moved = curve.CreateTransformed(Transform.CreateTranslation(XYZ(0, 0, dz)))
                curves.append(moved)
            except Exception:
                try:
                    curves.append(curve)
                except Exception:
                    continue
        if len(curves) < 3:
            continue
        array = CurveArray()
        for curve in curves:
            try:
                array.Append(curve)
            except Exception:
                pass
        if array.Size >= 3:
            loops.append(array)
    loops.sort(key=lambda a: -_array_length(a))
    if loops:
        return [loops[0]]
    return []


def _array_length(array):
    total = 0.0
    for curve in array:
        try:
            total += curve.Length
        except Exception:
            pass
    return total


def loop_is_closed(loop, tol=0.02):
    try:
        first = loop[0].GetEndPoint(0)
        last = loop[loop.Size - 1].GetEndPoint(1)
        return first.DistanceTo(last) < tol
    except Exception:
        return False


# ---------------------------------------------------------------- 生成

def ensure_floor_type(base_type, thickness_mm):
    """按板厚复制出「六吨-楼板-<厚>」类型；厚度一致就复用。"""
    target = round(thickness_mm, 1)
    try:
        current = floor_thickness_mm(base_type)
        if current and abs(current - target) < 1.5:
            return base_type
    except Exception:
        pass

    name = u"六吨-楼板-{0}".format(int(round(target)))
    collector = FilteredElementCollector(doc).OfClass(FloorType)
    for floor_type in collector:
        try:
            if type_name(floor_type) == name:
                return floor_type
        except Exception:
            continue

    try:
        new_type = base_type.Duplicate(name)
    except Exception:
        return base_type
    try:
        from Autodesk.Revit.DB import (
            CompoundStructure, MaterialFunctionAssignment, ElementId as EId)
        material = ElementId.InvalidElementId
        try:
            material = base_type.GetCompoundStructure().GetMaterialId(0)
        except Exception:
            pass
        structure = CompoundStructure.CreateSingleLayerCompoundStructure(
            MaterialFunctionAssignment.Structure, target * MM_TO_FT, material)
        new_type.SetCompoundStructure(structure)
        return new_type
    except Exception:
        return new_type


def create_floor(room, floor_type, level, thickness_mm, failures, created):
    name = room_name(room)
    if level is None:
        level = _room_level(room)
    if level is None:
        failures.append(u"{0}：找不到房间所在的标高".format(name))
        return
    base_z = level.Elevation
    loops = room_loops(room, base_z)
    if not loops:
        failures.append(u"{0}：房间边界不闭合，没取到边界线".format(name))
        return
    loop = loops[0]
    if not loop_is_closed(loop):
        failures.append(u"{0}：房间边界没有闭合（可能缺墙或少了房间分隔线）".format(name))
        return

    use_type = floor_type
    if thickness_mm > 0:
        use_type = ensure_floor_type(floor_type, thickness_mm)

    try:
        floor = doc.Create.NewFloor(loop, use_type, level, False)
    except Exception as error:
        failures.append(u"{0}：{1}".format(name, error))
        return

    try:
        param = floor.get_Parameter(BuiltInParameter.ALL_MODEL_MARK)
        if param is not None and not param.IsReadOnly:
            param.Set(name)
    except Exception:
        pass
    try:
        param = floor.get_Parameter(BuiltInParameter.FLOOR_HEIGHTABOVELEVEL_PARAM)
        if param is not None and not param.IsReadOnly:
            param.Set(0.0)
    except Exception:
        pass
    created.append(floor.Id)


# ---------------------------------------------------------------- 主流程

def selected_rooms():
    rooms = []
    try:
        selection = uidoc.Selection.GetElementIds()
    except Exception:
        selection = []
    for element_id in selection:
        element = doc.GetElement(element_id)
        if is_room(element) and room_area(element) > 0:
            rooms.append(element)
    return rooms


def do_generate(rooms, floor_type, level, thickness_mm):
    failures = []
    created = []
    transaction = Transaction(doc, u"楼板布置")
    transaction.Start()
    try:
        for room in rooms:
            create_floor(room, floor_type, level, thickness_mm, failures, created)
        if not created:
            transaction.RollBack()
        else:
            transaction.Commit()
    except Exception:
        try:
            transaction.RollBack()
        except Exception:
            pass
        failures.append(u"整体失败：{0}".format(traceback.format_exc()))
    if created:
        try:
            uidoc.Selection.SetElementIds(DotNetList[ElementId](created))
        except Exception:
            pass
    return created, failures

# ---------------------------------------------------------------- 界面

SHELL_XAML = u"""
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="楼板布置" Height="560" Width="560"
        WindowStartupLocation="CenterScreen" ResizeMode="CanResize">
  <DockPanel Margin="12">
    <StackPanel DockPanel.Dock="Bottom" Orientation="Horizontal"
                HorizontalAlignment="Right" Margin="0,10,0,0">
      <Button x:Name="btnPick" Content="点内部生成" Width="110" Height="30" Margin="0,0,8,0"/>
      <Button x:Name="btnSelected" Content="生成到选中房间" Width="140" Height="30" Margin="0,0,8,0"/>
      <Button x:Name="btnClose" Content="关闭" Width="80" Height="30"/>
    </StackPanel>
    <ScrollViewer VerticalScrollBarVisibility="Auto">
      <StackPanel>
        <TextBlock Text="① 板类型" FontWeight="Bold" Margin="0,0,0,6"/>
        <ComboBox x:Name="typeBox" Height="26" Margin="0,0,0,4"/>
        <TextBlock x:Name="typeHint" Foreground="Gray" FontSize="11" Margin="0,0,0,10"/>

        <TextBlock Text="② 板厚与标高" FontWeight="Bold" Margin="0,0,0,6"/>
        <Grid Margin="0,0,0,10">
          <Grid.ColumnDefinitions>
            <ColumnDefinition Width="110"/>
            <ColumnDefinition Width="140"/>
            <ColumnDefinition Width="90"/>
            <ColumnDefinition Width="*"/>
          </Grid.ColumnDefinitions>
          <Grid.RowDefinitions>
            <RowDefinition Height="32"/>
            <RowDefinition Height="32"/>
          </Grid.RowDefinitions>
          <TextBlock Text="板厚 (mm)" VerticalAlignment="Center" Grid.Row="0" Grid.Column="0"/>
          <TextBox x:Name="thickBox" Height="26" Grid.Row="0" Grid.Column="1" Text="100"/>
          <TextBlock Text="0 = 用类型厚度" Foreground="Gray" FontSize="11"
                     VerticalAlignment="Center" Grid.Row="0" Grid.Column="2" Grid.ColumnSpan="2"/>
          <TextBlock Text="标高" VerticalAlignment="Center" Grid.Row="1" Grid.Column="0"/>
          <ComboBox x:Name="levelBox" Height="26" Grid.Row="1" Grid.Column="1"/>
          <CheckBox x:Name="autoLevel" Content="按房间标高" IsChecked="True"
                    VerticalAlignment="Center" Grid.Row="1" Grid.Column="2" Grid.ColumnSpan="2"/>
        </Grid>

        <TextBlock Text="③ 用法" FontWeight="Bold" Margin="0,0,0,6"/>
        <TextBlock TextWrapping="Wrap" Foreground="#555555" Margin="0,0,0,10"
                   Text="「点内部生成」：在房间中间点一下，按房间边界铺板，按 Esc 结束。&#10;「生成到选中房间」：先在平面里选中房间文字，再点这个按钮一次生成全部。&#10;房间必须已放置且边界闭合（面积不是“未闭合”），否则会被跳过并列出原因。"/>

        <TextBlock Text="④ 预览" FontWeight="Bold" Margin="0,0,0,6"/>
        <TextBlock x:Name="preview" TextWrapping="Wrap" FontFamily="Consolas" FontSize="12"/>
      </StackPanel>
    </ScrollViewer>
  </DockPanel>
</Window>
"""


def _text(value):
    try:
        return unicode(value)
    except Exception:
        return u"{0}".format(value)


class FloorWindow(forms.WPFWindow):
    def __init__(self, floor_types, levels, point=None):
        forms.WPFWindow.__init__(self, SHELL_XAML, literal_string=True)
        self.floor_types = floor_types
        self.levels = levels
        self.point = point
        self.action = None

        self.typeBox.Items.Clear()
        for floor_type in floor_types:
            thickness = floor_thickness_mm(floor_type)
            label = u"{0}  ({1} mm)".format(type_name(floor_type), int(round(thickness)))
            if type_name(floor_type).startswith(u"六吨-"):
                label += u"  · 工具生成"
            self.typeBox.Items.Add(label)
        if self.typeBox.Items.Count:
            self.typeBox.SelectedIndex = 0

        self.levelBox.Items.Clear()
        for level in levels:
            self.levelBox.Items.Add(u"{0}   ({1:+.3f} m)".format(
                level_name(level), level.Elevation * FT_TO_MM / 1000.0))
        if self.levelBox.Items.Count:
            self.levelBox.SelectedIndex = 0

        self.btnPick.Click += self._on_pick
        self.btnSelected.Click += self._on_selected
        self.btnClose.Click += self._on_close
        self.levelBox.SelectionChanged += self._on_level_changed

        self.typeHint.Text = u"共 {0} 个建筑楼板类型（已排除基础板）；「工具生成」是之前按板厚自动复制出来的类型。".format(
            len(floor_types))

        self._refresh_preview()
        self._update_buttons()

    # ---------- 状态 ----------

    def _update_buttons(self):
        rooms = all_rooms()
        has_rooms = len(rooms) > 0
        self.btnPick.IsEnabled = has_rooms
        self.btnSelected.IsEnabled = has_rooms

    def _chosen_type(self):
        index = self.typeBox.SelectedIndex
        if index < 0 or index >= len(self.floor_types):
            return None
        return self.floor_types[index]

    def _chosen_level(self):
        if self.autoLevel.IsChecked:
            return None
        index = self.levelBox.SelectedIndex
        if index < 0 or index >= len(self.levels):
            return None
        return self.levels[index]

    def _on_level_changed(self, sender, args):
        self._refresh_preview()

    def _thickness(self):
        try:
            return float(self.thickBox.Text.strip())
        except Exception:
            return 0.0

    # ---------- 预览 ----------

    def _refresh_preview(self):
        rooms = all_rooms()
        lines = []
        lines.append(u"可用房间：{0} 个（已放置且面积有效）".format(len(rooms)))
        floor_type = self._chosen_type()
        if floor_type is None:
            lines.append(u"板类型：未选择")
        else:
            lines.append(u"板类型：{0}（类型厚 {1} mm）".format(
                type_name(floor_type), floor_thickness_mm(floor_type)))
        thickness = self._thickness()
        if thickness > 0:
            lines.append(u"生成厚度：{0} mm（会复制出「六吨-楼板-{1}」类型）".format(
                int(round(thickness)), int(round(thickness))))
        else:
            lines.append(u"生成厚度：跟随类型")
        level = self._chosen_level()
        if self.autoLevel.IsChecked:
            lines.append(u"标高：按每个房间自己的标高")
        elif level is not None:
            lines.append(u"标高：{0}  {1:+.3f} m".format(
                level_name(level), level.Elevation * FT_TO_MM / 1000.0))
        if not rooms:
            lines.append(u"")
            lines.append(u"当前项目没有房间图元：请先「建筑 → 房间和面积 → 房间」放置房间。")
        self.preview.Text = u"\n".join(lines)

    # ---------- 按钮 ----------

    def _on_pick(self, sender, args):
        self.action = "pick"
        self.Close()

    def _on_selected(self, sender, args):
        self.action = "selected"
        self.Close()

    def _on_close(self, sender, args):
        self.action = None
        self.Close()


def generate_for_rooms(rooms, floor_type, level, thickness):
    created, failures = do_generate(rooms, floor_type, level, thickness)
    lines = [u"成功 {0} 块，失败 {1} 块".format(len(created), len(failures))]
    for item in failures:
        lines.append(u"· " + item)
    if failures:
        write_log([u"---- 楼板布置 ----"] + lines)
    forms.alert(u"\n".join(lines), title=u"楼板布置")
    return created


def main():
    if doc is None:
        forms.alert(u"请先打开一个项目。", exitscript=True)
        return

    floor_types = all_floor_types()
    levels = all_levels()
    if not floor_types:
        forms.alert(u"项目里没有可用的建筑楼板类型。", title=u"楼板布置")
        return
    if not levels:
        forms.alert(u"项目里没有标高。", title=u"楼板布置")
        return
    if not all_rooms():
        forms.alert(u"当前项目没有房间图元。\n请先放置房间（建筑 → 房间和面积 → 房间），"
                    u"并确保房间边界闭合。", title=u"楼板布置")
        return

    window = FloorWindow(floor_types, levels)
    window.ShowDialog()
    if window.action is None:
        return

    floor_type = window._chosen_type()
    level = window._chosen_level()
    thickness = window._thickness()
    if floor_type is None:
        forms.alert(u"没有选择板类型。", title=u"楼板布置")
        return

    if window.action == "selected":
        rooms = selected_rooms()
        if not rooms:
            forms.alert(u"没有选中房间。先在平面里点选房间文字，再点「生成到选中房间」。",
                        title=u"楼板布置")
            return
        generate_for_rooms(rooms, floor_type, level, thickness)
        return

    # 点内部生成：循环拾取，Esc 结束
    count = 0
    failures_total = []
    while True:
        try:
            point = uidoc.Selection.PickPoint(u"在房间内部点一下（Esc 结束）")
        except Exception:
            break
        room = room_at(point)
        if room is None:
            forms.alert(u"这个点不在任何房间里。\n请点在房间内部（房间文字附近）。",
                        title=u"楼板布置")
            continue
        use_level = level if level is not None else _room_level(room)
        if use_level is None:
            failures_total.append(u"{0}：找不到房间标高".format(room_name(room)))
            continue
        created, failures = do_generate([room], floor_type, use_level, thickness)
        count += len(created)
        failures_total.extend(failures)

    lines = [u"点内部生成：成功 {0} 块，失败 {1} 块".format(count, len(failures_total))]
    for item in failures_total:
        lines.append(u"· " + item)
    write_log([u"---- 楼板布置（点选） ----"] + lines)
    forms.alert(u"\n".join(lines), title=u"楼板布置")


def _room_level(room):
    """房间所在标高：Level 属性 → LevelId → 包围盒最近标高。"""
    for getter in ("Level", "get_Level"):
        try:
            value = getattr(room, getter)
            if callable(value):
                value = value()
            if value is not None:
                return value
        except Exception:
            pass
    for getter in ("LevelId", "get_LevelId"):
        try:
            level_id = getattr(room, getter)
            if callable(level_id):
                level_id = level_id()
            level = doc.GetElement(level_id)
            if level is not None:
                return level
        except Exception:
            pass
    try:
        box = room.get_BoundingBox(None)
        if box is not None:
            z = box.Min.Z
            best, best_gap = None, 1.0e9
            for level in all_levels():
                gap = abs(level.Elevation - z)
                if gap < best_gap:
                    best, best_gap = level, gap
            return best
    except Exception:
        pass
    return None


try:
    main()
except Exception:
    detail = traceback.format_exc()
    write_log([u"---- 异常 ----", detail])
    output.print_md(u"脚本出错：\n```\n{0}\n```".format(detail))
    forms.alert(u"脚本出错：\n{0}".format(detail[-1200:]), title=u"楼板布置")
