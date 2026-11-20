# -*- coding: utf-8 -*-
"""房间与面积：统计面积、找未闭合房间、批量改名编号。"""
import traceback

import clr  # noqa
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")
clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")

from Autodesk.Revit.DB import (  # noqa: E402
    BuiltInCategory,
    BuiltInParameter,
    ElementId,
    FilteredElementCollector,
    Level,
    Transaction,
)
try:
    from Autodesk.Revit.DB.Architecture import Room  # 2020 在 DB.Architecture
except Exception:
    Room = None

from pyrevit import forms, revit, script  # noqa: E402

doc = revit.doc
uidoc = revit.uidoc
output = script.get_output()

FT_TO_MM = 304.8
SQFT_TO_M2 = 0.09290304
LOG_PATH = r"C:\Users\cyl\Documents\deepseek-harness\default-workspace\_房间面积日志.txt"


def log_line(text):
    try:
        with open(LOG_PATH, "ab") as fh:
            fh.write((u"%s\n" % text).encode("utf-8"))
    except Exception:
        pass


def safe_name(element, fallback=u""):
    if element is None:
        return fallback
    try:
        name = element.Name
        if name:
            return name
    except Exception:
        pass
    try:
        return u"%s" % element.Id.IntegerValue
    except Exception:
        return fallback


def all_rooms():
    rows = []
    for element in FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_Rooms) \
            .WhereElementIsNotElementType():
        if Room is not None and not isinstance(element, Room):
            continue
        rows.append(element)
    return rows


def room_area(room):
    try:
        return float(room.Area)
    except Exception:
        return 0.0


def room_level_name(room):
    try:
        level = room.Level
        if level is not None:
            return safe_name(level)
    except Exception:
        pass
    try:
        level_id = room.get_Parameter(BuiltInParameter.ROOM_LEVEL_ID).AsElementId()
        level = doc.GetElement(level_id)
        return safe_name(level, u"<无标高>")
    except Exception:
        return u"<无标高>"


def room_number(room):
    try:
        value = room.Number
        if value:
            return value
    except Exception:
        pass
    try:
        return room.get_Parameter(BuiltInParameter.ROOM_NUMBER).AsString() or u""
    except Exception:
        return u""


def summarize(rooms):
    total_area = 0.0
    closed = 0
    open_rooms = []
    per_level = {}
    for room in rooms:
        area = room_area(room)
        level_name = room_level_name(room)
        if area > 0:
            closed += 1
            total_area += area
            per_level[level_name] = per_level.get(level_name, 0.0) + area
        else:
            open_rooms.append(room)
    lines = [u"房间合计 %d 间：已闭合 %d 间，未闭合 %d 间" % (len(rooms), closed, len(open_rooms)),
             u"总面积 %.2f m²（%.2f ㎡ 按 1 m²=10.764 ft² 换算）" % (total_area * SQFT_TO_M2, total_area * SQFT_TO_M2)]
    if per_level:
        lines.append(u"")
        lines.append(u"按标高：")
        for level_name in sorted(per_level.keys()):
            lines.append(u"  %s：%.2f m²" % (level_name, per_level[level_name] * SQFT_TO_M2))
    if open_rooms:
        lines.append(u"")
        lines.append(u"未闭合/面积为 0 的房间（共 %d 间）：" % len(open_rooms))
        for room in open_rooms[:40]:
            lines.append(u"  Id %s  %s / %s" % (room.Id.IntegerValue, room_number(room),
                                                safe_name(room, u"<未命名>")))
        if len(open_rooms) > 40:
            lines.append(u"  … 其余 %d 间省略" % (len(open_rooms) - 40))
    return u"\n".join(lines), open_rooms, per_level, total_area


def select_rooms(rooms):
    ids = []
    for room in rooms:
        ids.append(room.Id)
    if not ids:
        return 0
    try:
        uidoc.Selection.SetElementIds(ids)
        uidoc.ShowElements(ids[0])
        return len(ids)
    except Exception:
        return 0


def rename_rooms(rooms, prefix, suffix, start_number, use_number):
    renamed = 0
    failures = []
    transaction = Transaction(doc, u"房间批量改名")
    transaction.Start()
    try:
        current = start_number
        for room in rooms:
            try:
                base = safe_name(room, u"房间")
                new_name = u"%s%s%s" % (prefix, base, suffix)
                if use_number:
                    new_name = u"%s%s%s" % (prefix, str(current).zfill(2), suffix)
                    current += 1
                room.Name = new_name
                renamed += 1
            except Exception:
                failures.append(u"%s: %s" % (room.Id, traceback.format_exc().splitlines()[-1]))
        transaction.Commit()
    except Exception:
        transaction.RollBack()
        failures.append(traceback.format_exc().splitlines()[-1])
    log_line(u"[rename] 改名 %d 间，失败 %d" % (renamed, len(failures)))
    return renamed, failures


XAML = u"""
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="房间与面积" Height="560" Width="620" MinWidth="560" MinHeight="480"
        WindowStartupLocation="CenterScreen" ResizeMode="CanResize">
  <Grid Margin="16">
    <Grid.RowDefinitions>
      <RowDefinition Height="Auto"/>
      <RowDefinition Height="Auto"/>
      <RowDefinition Height="Auto"/>
      <RowDefinition Height="*"/>
      <RowDefinition Height="Auto"/>
    </Grid.RowDefinitions>

    <StackPanel Grid.Row="0" Orientation="Horizontal" Margin="0,0,0,10">
      <Button x:Name="btnStat" Content="统计面积" Width="110" Height="32" FontWeight="Bold" Margin="0,0,8,0"/>
      <Button x:Name="btnOpen" Content="选中未闭合" Width="120" Height="32" Margin="0,0,8,0"/>
      <Button x:Name="btnAll" Content="选中全部房间" Width="130" Height="32"/>
    </StackPanel>

    <StackPanel Grid.Row="1" Orientation="Horizontal" Margin="0,0,0,10">
      <TextBlock Text="前缀" VerticalAlignment="Center" Width="40"/>
      <TextBox x:Name="prefixBox" Text="" Width="90" Height="28"/>
      <TextBlock Text="后缀" VerticalAlignment="Center" Margin="14,0,6,0"/>
      <TextBox x:Name="suffixBox" Text="" Width="90" Height="28"/>
      <TextBlock Text="起始号" VerticalAlignment="Center" Margin="14,0,6,0"/>
      <TextBox x:Name="startBox" Text="1" Width="56" Height="28"/>
      <CheckBox x:Name="numberBox" Content="用序号命名" IsChecked="False"
                VerticalAlignment="Center" Margin="14,0,0,0"/>
    </StackPanel>

    <TextBlock Grid.Row="2" Text="「选中未闭合」会高亮面积为 0 的房间（通常是没闭合或没放房间）。"
               TextWrapping="Wrap" Foreground="#FF6B7683" Margin="0,0,0,10"/>

    <TextBox Grid.Row="3" x:Name="report" IsReadOnly="True" AcceptsReturn="True"
             VerticalScrollBarVisibility="Auto" TextWrapping="Wrap"/>

    <StackPanel Grid.Row="4" Orientation="Horizontal" HorizontalAlignment="Right" Margin="0,10,0,0">
      <Button x:Name="btnRename" Content="批量改名" Width="110" Height="34" Margin="0,0,8,0"/>
      <Button x:Name="btnClose" Content="关闭" Width="88" Height="34"/>
    </StackPanel>
  </Grid>
</Window>
"""


class RoomWindow(forms.WPFWindow):
    def __init__(self, rooms):
        forms.WPFWindow.__init__(self, XAML, literal_string=True)
        self.rooms = rooms
        self.open_rooms = []
        self.report = self.FindName("report")
        self.FindName("btnStat").Click += self._guard(self._on_stat)
        self.FindName("btnOpen").Click += self._guard(self._on_open)
        self.FindName("btnAll").Click += self._guard(self._on_all)
        self.FindName("btnRename").Click += self._guard(self._on_rename)
        self.FindName("btnClose").Click += self._on_close
        self._on_stat(None, None)

    def _guard(self, func):
        def wrapped(sender, args):
            try:
                func(sender, args)
            except Exception:
                text = traceback.format_exc()[-800:]
                self.report.Text = text
                log_line(text)
        return wrapped

    def _on_stat(self, sender, args):
        text, open_rooms, _per_level, _total = summarize(self.rooms)
        self.open_rooms = open_rooms
        self.report.Text = text

    def _on_open(self, sender, args):
        if not self.open_rooms:
            self._on_stat(None, None)
        count = select_rooms(self.open_rooms)
        if count:
            self.report.Text = u"已选中 %d 间未闭合房间。" % count
        else:
            self.report.Text = u"没有未闭合房间，或无法选中。"

    def _on_all(self, sender, args):
        count = select_rooms(self.rooms)
        self.report.Text = u"已选中 %d 间房间。" % count

    def _on_rename(self, sender, args):
        rooms = list(self.rooms)
        try:
            ids = list(uidoc.Selection.GetElementIds())
        except Exception:
            ids = []
        picked = []
        for element_id in ids:
            element = doc.GetElement(element_id)
            if element is None:
                continue
            if Room is not None and not isinstance(element, Room):
                continue
            picked.append(element)
        if picked:
            rooms = picked
        if not rooms:
            self.report.Text = u"没有可改名的房间。"
            return
        prefix = self.FindName("prefixBox").Text or u""
        suffix = self.FindName("suffixBox").Text or u""
        try:
            start = int(self.FindName("startBox").Text)
        except Exception:
            start = 1
        use_number = bool(self.FindName("numberBox").IsChecked)
        renamed, failures = rename_rooms(rooms, prefix, suffix, start, use_number)
        text = u"改名 %d 间（范围：%s）" % (renamed, u"选中的房间" if picked else u"项目全部房间")
        if failures:
            text += u"\n失败 %d 条：%s" % (len(failures), failures[0])
        self.report.Text = text
        self._on_stat(None, None)

    def _on_close(self, sender, args):
        self.Close()


def main():
    if doc is None:
        forms.alert(u"请先打开一个项目。", exitscript=True)
        return
    window = RoomWindow(all_rooms())
    window.ShowDialog()


try:
    main()
except Exception:
    output.print_md(u"脚本出错：\n```\n{0}\n```".format(traceback.format_exc()))
    forms.alert(u"脚本出错：\n%s" % traceback.format_exc()[-600:], title=u"房间与面积")
