# -*- coding: utf-8 -*-
"""标高批量创建

按「起始楼层号 + 起始高程 + 层高 + 个数 + 后缀」一次生成一串标高，
每个新标高自动补一个楼层平面视图。名称规则：1F_0.000 / 2F_3.300 …
同名标高跳过；同高程已有标高时复用并改名；整批一条事务，可一次撤销。
"""

import io
import traceback

from pyrevit import revit, forms, script

from Autodesk.Revit.DB import (
    FilteredElementCollector,
    Level,
    ViewPlan,
    ViewFamily,
    ViewFamilyType,
    Transaction,
    ElementId,
)

LOG_PATH = r"C:\Users\cyl\Documents\deepseek-harness\default-workspace\_标高批量日志.txt"

FT_TO_MM = 304.8
MM_TO_FT = 1.0 / 304.8


def log(text):
    try:
        with io.open(LOG_PATH, "a", encoding="utf-8") as fp:
            fp.write(u"{0}\n".format(text))
    except Exception:
        pass


def label_for(number, elevation_mm, suffix):
    name = u"{0}F_{1:.3f}".format(number, elevation_mm / 1000.0)
    if suffix:
        name = name + suffix
    return name


def existing_levels(doc):
    levels = []
    for element in FilteredElementCollector(doc).OfClass(Level):
        try:
            levels.append(element)
        except Exception:
            pass
    return levels


def floor_plan_type_id(doc):
    for element in FilteredElementCollector(doc).OfClass(ViewFamilyType):
        try:
            if element.ViewFamily == ViewFamily.FloorPlan:
                return element.Id
        except Exception:
            pass
    return ElementId.InvalidElementId


def level_has_view(doc, level_id):
    for element in FilteredElementCollector(doc).OfClass(ViewPlan):
        try:
            if element.IsTemplate:
                continue
            if element.GenLevel is not None and element.GenLevel.Id.IntegerValue == level_id.IntegerValue:
                return True
        except Exception:
            pass
    return False


def build_rows(start_number, start_mm, step_mm, count, suffix):
    rows = []
    for index in range(count):
        elevation = start_mm + step_mm * index
        number = start_number + index
        rows.append({
            "number": number,
            "name": label_for(number, elevation, suffix),
            "elevation_mm": elevation,
            "rise_mm": step_mm,
        })
    return rows


def apply_rows(doc, rows, create_views):
    """返回 (新建标高数, 复用改名数, 跳过数, 新建视图数, 失败列表)。"""
    created = 0
    renamed = 0
    skipped = 0
    views = 0
    failures = []

    levels = existing_levels(doc)
    by_name = {}
    by_elev = {}
    for level in levels:
        try:
            by_name[level.Name] = level
        except Exception:
            pass
        try:
            by_elev[round(level.Elevation, 4)] = level
        except Exception:
            pass

    view_type_id = floor_plan_type_id(doc) if create_views else ElementId.InvalidElementId

    for row in rows:
        name = row["name"]
        elevation = row["elevation_mm"] * MM_TO_FT
        try:
            if name in by_name:
                skipped += 1
                log(u"跳过（同名已存在）：{0}".format(name))
                continue

            level = by_elev.get(round(elevation, 4))
            if level is not None:
                try:
                    level.Name = name
                except Exception:
                    pass
                renamed += 1
                by_name[name] = level
                log(u"复用并改名：{0} (标高 {1})".format(name, row["elevation_mm"]))
            else:
                level = Level.Create(doc, elevation)
                if level is None:
                    failures.append((name, u"标高创建返回空"))
                    continue
                level.Name = name
                created += 1
                by_name[name] = level
                by_elev[round(elevation, 4)] = level
                log(u"新建标高：{0} = {1} mm".format(name, row["elevation_mm"]))

            if create_views and view_type_id != ElementId.InvalidElementId:
                if not level_has_view(doc, level.Id):
                    try:
                        ViewPlan.Create(doc, view_type_id, level.Id)
                        views += 1
                    except Exception:
                        log(u"视图创建失败 {0}：{1}".format(name, traceback.format_exc()))
        except Exception:
            detail = traceback.format_exc()
            log(u"失败 {0}：{1}".format(name, detail))
            failures.append((name, detail.strip().splitlines()[-1]))

    return created, renamed, skipped, views, failures


XAML = u"""<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="标高批量创建" Height="700" Width="940"
        MinWidth="880" MinHeight="560" ResizeMode="CanResize"
        WindowStartupLocation="CenterScreen">
  <ScrollViewer VerticalScrollBarVisibility="Auto" HorizontalScrollBarVisibility="Auto">
    <StackPanel Margin="16">
      <TextBlock Text="① 批量参数" FontWeight="Bold"/>
      <Grid Margin="0,8,0,0">
        <Grid.ColumnDefinitions>
          <ColumnDefinition Width="Auto"/>
          <ColumnDefinition Width="86"/>
          <ColumnDefinition Width="10"/>
          <ColumnDefinition Width="Auto"/>
          <ColumnDefinition Width="92"/>
          <ColumnDefinition Width="10"/>
          <ColumnDefinition Width="Auto"/>
          <ColumnDefinition Width="80"/>
          <ColumnDefinition Width="10"/>
          <ColumnDefinition Width="Auto"/>
          <ColumnDefinition Width="60"/>
          <ColumnDefinition Width="10"/>
          <ColumnDefinition Width="Auto"/>
          <ColumnDefinition Width="70"/>
          <ColumnDefinition Width="10"/>
          <ColumnDefinition Width="Auto"/>
        </Grid.ColumnDefinitions>

        <TextBlock Grid.Column="0" Text="起始楼层号" VerticalAlignment="Center"/>
        <TextBox Grid.Column="1" x:Name="startNoBox" Height="26" Margin="6,0,0,0"/>
        <TextBlock Grid.Column="3" Text="起始高程(mm)" VerticalAlignment="Center"/>
        <TextBox Grid.Column="4" x:Name="startZBox" Height="26" Margin="6,0,0,0"/>
        <TextBlock Grid.Column="6" Text="层高(mm)" VerticalAlignment="Center"/>
        <TextBox Grid.Column="7" x:Name="stepBox" Height="26" Margin="6,0,0,0"/>
        <TextBlock Grid.Column="9" Text="个数" VerticalAlignment="Center"/>
        <TextBox Grid.Column="10" x:Name="countBox" Height="26" Margin="6,0,0,0"/>
        <TextBlock Grid.Column="12" Text="后缀" VerticalAlignment="Center"/>
        <TextBox Grid.Column="13" x:Name="suffixBox" Height="26" Margin="6,0,0,0"/>
        <Button Grid.Column="15" x:Name="addBtn" Content="添加任务" Width="80" Height="28"/>
      </Grid>

      <TextBlock Text="② 待生成列表（选中一行可在下面改名称/高程/层高）" FontWeight="Bold" Margin="0,16,0,6"/>
      <ListBox x:Name="rowList" Height="240"/>
      <StackPanel Orientation="Horizontal" Margin="0,8,0,0">
        <TextBlock Text="名称" VerticalAlignment="Center"/>
        <TextBox x:Name="editName" Width="130" Height="26" Margin="6,0,10,0"/>
        <TextBlock Text="高程(mm)" VerticalAlignment="Center"/>
        <TextBox x:Name="editZ" Width="90" Height="26" Margin="6,0,10,0"/>
        <TextBlock Text="层高(mm)" VerticalAlignment="Center"/>
        <TextBox x:Name="editRise" Width="80" Height="26" Margin="6,0,10,0"/>
        <Button x:Name="applyEdit" Content="应用修改" Width="90" Height="28" Margin="0,0,8,0"/>
        <Button x:Name="removeRow" Content="删除该行" Width="90" Height="28" Margin="0,0,8,0"/>
        <Button x:Name="clearRows" Content="清空" Width="70" Height="28"/>
      </StackPanel>

      <CheckBox x:Name="viewBox" Content="同时生成楼层平面视图" IsChecked="True" Margin="0,12,0,0"/>

      <TextBlock x:Name="statusText" Margin="0,10,0,0" TextWrapping="Wrap"
                 Foreground="#FF7D8CA9" Text="参数填好后点「添加任务」，可反复添加（例如地下室、裙房、塔楼分段）。"/>

      <StackPanel Orientation="Horizontal" Margin="0,12,0,0">
        <Button x:Name="okBtn" Content="批量生成标高及视图" Width="180" Height="34" FontWeight="Bold"/>
        <Button x:Name="closeBtn" Content="关闭" Width="90" Height="34" Margin="10,0,0,0"/>
      </StackPanel>
    </StackPanel>
  </ScrollViewer>
</Window>"""


class LevelWindow(forms.WPFWindow):
    def __init__(self):
        forms.WPFWindow.__init__(self, XAML, literal_string=True)
        self.rows = []
        self.state = {"ok": False}

        self.list = self.FindName("rowList")
        self.status = self.FindName("statusText")

        self.FindName("startNoBox").Text = u"1"
        self.FindName("startZBox").Text = u"0"
        self.FindName("stepBox").Text = u"3300"
        self.FindName("countBox").Text = u"6"
        self.FindName("suffixBox").Text = u""

        self.FindName("addBtn").Click += self._on_add
        self.FindName("applyEdit").Click += self._on_apply
        self.FindName("removeRow").Click += self._on_remove
        self.FindName("clearRows").Click += self._on_clear
        self.FindName("okBtn").Click += self._on_ok
        self.FindName("closeBtn").Click += lambda s, e: self.Close()
        self.list.SelectionChanged += self._on_select

        self._redraw()

    # ---------- 数值
    def _num(self, name, default):
        try:
            text = u"%s" % (self.FindName(name).Text or u"")
            return float(text.strip())
        except Exception:
            return float(default)

    def _refresh_list(self):
        self.list.Items.Clear()
        for index, row in enumerate(self.rows, 1):
            self.list.Items.Add(u"{0}.  {1}    高程 {2:.0f} mm    层高 {3:.0f} mm".format(
                index, row["name"], row["elevation_mm"], row["rise_mm"]))

    def _redraw(self):
        self._refresh_list()
        self.status.Text = u"共 {0} 个标高。".format(len(self.rows))

    # ---------- 事件
    def _on_add(self, sender, args):
        start_no = int(self._num("startNoBox", 1))
        start_z = self._num("startZBox", 0.0)
        step = self._num("stepBox", 3300.0)
        count = int(self._num("countBox", 1))
        suffix = u"%s" % (self.FindName("suffixBox").Text or u"")
        if count < 1:
            forms.alert(u"个数必须 ≥ 1。", title=u"标高批量创建")
            return
        if step == 0:
            forms.alert(u"层高不能为 0。", title=u"标高批量创建")
            return
        self.rows.extend(build_rows(start_no, start_z, step, count, suffix))
        self.FindName("startNoBox").Text = u"%d" % (start_no + count)
        self.FindName("startZBox").Text = u"%.0f" % (start_z + step * count)
        self._redraw()

    def _on_select(self, sender, args):
        index = int(self.list.SelectedIndex)
        if index < 0 or index >= len(self.rows):
            return
        row = self.rows[index]
        self.FindName("editName").Text = row["name"]
        self.FindName("editZ").Text = u"%.0f" % row["elevation_mm"]
        self.FindName("editRise").Text = u"%.0f" % row["rise_mm"]

    def _on_apply(self, sender, args):
        index = int(self.list.SelectedIndex)
        if index < 0 or index >= len(self.rows):
            forms.alert(u"请先选中一行。", title=u"标高批量创建")
            return
        row = self.rows[index]
        name = u"%s" % (self.FindName("editName").Text or u"")
        if name.strip():
            row["name"] = name.strip()
        row["elevation_mm"] = self._num("editZ", row["elevation_mm"])
        row["rise_mm"] = self._num("editRise", row["rise_mm"])
        self._redraw()
        self.list.SelectedIndex = index

    def _on_remove(self, sender, args):
        index = int(self.list.SelectedIndex)
        if 0 <= index < len(self.rows):
            del self.rows[index]
            self._redraw()

    def _on_clear(self, sender, args):
        self.rows = []
        self._redraw()

    def _on_ok(self, sender, args):
        if not self.rows:
            forms.alert(u"列表是空的，先添加任务。", title=u"标高批量创建")
            return
        self.state["ok"] = True
        self.state["rows"] = list(self.rows)
        self.state["create_views"] = bool(self.FindName("viewBox").IsChecked)
        self.Close()


def main():
    doc = revit.doc
    if doc is None:
        forms.alert(u"请先打开一个项目文档。", exitscript=True)

    window = LevelWindow()
    window.ShowDialog()
    if not window.state.get("ok"):
        return

    rows = window.state["rows"]
    create_views = window.state["create_views"]

    transaction = Transaction(doc, u"标高批量创建")
    transaction.Start()
    try:
        created, renamed, skipped, views, failures = apply_rows(doc, rows, create_views)
        transaction.Commit()
    except Exception:
        transaction.RollBack()
        log(traceback.format_exc())
        forms.alert(u"生成失败，已回滚：\n{0}".format(traceback.format_exc()[-1200:]),
                    title=u"标高批量创建")
        return

    lines = [u"**本次批量生成标高总计：{0}**".format(created)]
    lines.append(u"")
    lines.append(u"- 新建标高 {0} 个，复用改名 {1} 个，同名跳过 {2} 个".format(created, renamed, skipped))
    if create_views:
        lines.append(u"- 新建楼层平面 {0} 个".format(views))
    if failures:
        lines.append(u"- 失败 {0} 个：".format(len(failures)))
        for name, msg in failures[:8]:
            lines.append(u"    - {0}：{1}".format(name, msg))
    lines.append(u"- 日志：`{0}`".format(LOG_PATH))

    output = script.get_output()
    output.print_md(u"\n".join(lines))
    forms.alert(u"本次批量生成标高总计：{0}\n（新建 {1}，复用改名 {2}，跳过 {3}，视图 {4}）".format(
        created, created, renamed, skipped, views), title=u"标高批量创建")


try:
    main()
except Exception:
    detail = traceback.format_exc()
    log(detail)
    forms.alert(u"标高批量创建出错：\n{0}".format(detail[-1200:]), title=u"标高批量创建")
