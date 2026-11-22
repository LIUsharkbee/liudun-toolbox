# -*- coding: utf-8 -*-
"""构造处理：墙端连接开关 / 柱断墙 / 一键扣减 / 视图批量改名"""
import os
import traceback

import clr
clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')

from System.Collections.Generic import List
from Autodesk.Revit.DB import (
    BuiltInCategory, ElementId, FilteredElementCollector, JoinGeometryUtils,
    SolidSolidCutUtils, Transaction, View, Wall, WallUtils,
)
from Autodesk.Revit.UI import TaskDialog
from pyrevit import forms, revit

LOG_PATH = os.path.join(
    os.path.expanduser('~'), 'Documents', 'deepseek-harness', 'default-workspace',
    u'_构造处理日志.txt')


def log_line(text):
    try:
        f = open(LOG_PATH, 'ab')
        try:
            f.write((text + u'\n').encode('utf-8'))
        finally:
            f.close()
    except Exception:
        pass


def uidoc_ref():
    return revit.uidoc


def active_doc():
    try:
        return revit.doc
    except Exception:
        return revit.uidoc.Document


def selected_elements(uidoc, class_name=None):
    result = []
    for eid in uidoc.Selection.GetElementIds():
        element = uidoc.Document.GetElement(eid)
        if element is None:
            continue
        if class_name and element.GetType().Name != class_name:
            continue
        result.append(element)
    return result


def selected_walls(uidoc):
    walls = []
    for element in selected_elements(uidoc):
        if isinstance(element, Wall):
            walls.append(element)
    return walls


def toggle_join(uidoc):
    doc = uidoc.Document
    walls = selected_walls(uidoc)
    if not walls:
        forms.alert(u'请先选中墙（可多选）。')
        return
    switched = 0
    failures = []
    tx = Transaction(doc, u'墙端连接开关')
    tx.Start()
    try:
        for wall in walls:
            for end in (0, 1):
                try:
                    if WallUtils.IsWallJoinAllowedAtEnd(wall, end):
                        WallUtils.DisallowWallJoinAtEnd(wall, end)
                    else:
                        WallUtils.AllowWallJoinAtEnd(wall, end)
                    switched += 1
                except Exception as ex:
                    failures.append(u'%s 端%d：%s' % (wall.Id, end, ex))
        tx.Commit()
    except Exception:
        tx.RollBack()
        failures.append(traceback.format_exc())
    log_line(u'[join] 切换 %d 个墙端，失败 %d' % (switched, len(failures)))
    message = u'已切换 %d 个墙端连接。' % switched
    if failures:
        message += u'\n失败 %d 个：\n%s' % (len(failures), u'\n'.join(failures[:5]))
    forms.alert(message)


def column_break_wall(uidoc):
    """选中柱：与之相交的墙无论是否连接，统一断开连接并在交接处保留各自边界。"""
    doc = uidoc.Document
    columns = []
    for element in selected_elements(uidoc):
        cat = element.Category
        if cat is not None and cat.Id.IntegerValue == int(BuiltInCategory.OST_StructuralColumns):
            columns.append(element)
        elif cat is not None and cat.Id.IntegerValue == int(BuiltInCategory.OST_Columns):
            columns.append(element)
    if not columns:
        forms.alert(u'请先选中柱（建筑柱或结构柱）。')
        return

    walls = [w for w in FilteredElementCollector(doc).OfClass(Wall).ToElements()]
    boxes = []
    for column in columns:
        boxes.append(column.get_BoundingBox(None))
    touched = 0
    tx = Transaction(doc, u'柱断墙')
    tx.Start()
    try:
        for wall in walls:
            if wall.WallType.Kind.ToString() != 'Basic':
                continue
            box = wall.get_BoundingBox(None)
            if box is None:
                continue
            hit = False
            for cb in boxes:
                if cb is None:
                    continue
                if (box.Min.X <= cb.Max.X and box.Max.X >= cb.Min.X and
                        box.Min.Y <= cb.Max.Y and box.Max.Y >= cb.Min.Y):
                    hit = True
                    break
            if not hit:
                continue
            try:
                JoinGeometryUtils.UnjoinGeometry(doc, wall, columns[0])
            except Exception:
                pass
            for end in (0, 1):
                try:
                    if WallUtils.IsWallJoinAllowedAtEnd(wall, end):
                        WallUtils.DisallowWallJoinAtEnd(wall, end)
                except Exception:
                    pass
            touched += 1
        tx.Commit()
    except Exception:
        tx.RollBack()
        log_line(u'[column] ' + traceback.format_exc())
    log_line(u'[column] 柱 %d 个，断开墙 %d 段' % (len(columns), touched))
    forms.alert(u'已处理 %d 个柱，断开 %d 段墙的连接。' % (len(columns), touched))


def one_click_cut(uidoc):
    """选中多个实体构件：两两相交就加扣减（可撤销）。"""
    doc = uidoc.Document
    elements = selected_elements(uidoc)
    if len(elements) < 2:
        forms.alert(u'请至少选中两个实体构件（墙/板/柱/梁）。')
        return
    added = 0
    failures = []
    tx = Transaction(doc, u'一键扣减')
    tx.Start()
    try:
        for i in range(len(elements)):
            for j in range(i + 1, len(elements)):
                try:
                    if SolidSolidCutUtils.CutExistsBetweenElements(doc, elements[i].Id, elements[j].Id)[0] if False else False:
                        continue
                except Exception:
                    pass
                try:
                    added += 1
                    SolidSolidCutUtils.AddCutBetweenElements(doc, elements[i].Id, elements[j].Id, False)
                except Exception as ex:
                    failures.append(u'%s x %s：%s' % (elements[i].Id, elements[j].Id, ex))
        tx.Commit()
    except Exception:
        tx.RollBack()
        failures.append(traceback.format_exc())
    log_line(u'[cut] 新增 %d 组，失败 %d' % (added, len(failures)))
    forms.alert(u'已添加 %d 组扣减，失败 %d 组。' % (added, len(failures)))


def rename_views(uidoc, prefix, replace_from, replace_to):
    doc = uidoc.Document
    views = [v for v in FilteredElementCollector(doc).OfClass(View).ToElements()
             if not v.IsTemplate and v.ViewType.ToString() in
             ('FloorPlan', 'CeilingPlan', 'ThreeD', 'Section', 'Elevation', 'EngineeringPlan')]
    renamed = 0
    failures = []
    tx = Transaction(doc, u'视图批量改名')
    tx.Start()
    try:
        for view in views:
            old = view.Name
            new = old
            if replace_from:
                new = new.replace(replace_from, replace_to)
            if prefix and not new.startswith(prefix):
                new = prefix + new
            if new == old:
                continue
            try:
                view.Name = new
                renamed += 1
            except Exception as ex:
                failures.append(u'%s：%s' % (old, ex))
        tx.Commit()
    except Exception:
        tx.RollBack()
        failures.append(traceback.format_exc())
    log_line(u'[rename] 改名 %d，失败 %d' % (renamed, len(failures)))
    forms.alert(u'已改名 %d 个视图。%s' % (
        renamed, (u'\n失败 %d 个' % len(failures)) if failures else u''))



# ---------------- 扣减关系切换 ----------------

def _cut_state(first, second):
    """返回 (是否存在实体扣减, 是否 first 扣 second)。"""
    try:
        result = SolidSolidCutUtils.CutExistsBetweenElements(first, second)
    except Exception:
        return (False, False)
    if isinstance(result, tuple):
        if len(result) >= 2:
            return (bool(result[0]), bool(result[1]))
        if len(result) == 1:
            return (bool(result[0]), True)
        return (False, False)
    return (bool(result), True)


def _join_state(doc, first, second):
    try:
        return JoinGeometryUtils.AreElementsJoined(doc, first, second)
    except Exception:
        return False


def describe_deduction(uidoc):
    """诊断：列出选中构件两两之间的连接/扣减方向。"""
    doc = uidoc.Document
    elements = selected_elements(uidoc)
    if len(elements) < 2:
        forms.alert(u'请至少选中两个构件。')
        return
    lines = []
    for i in range(len(elements)):
        for j in range(i + 1, len(elements)):
            a, b = elements[i], elements[j]
            joined = _join_state(doc, a, b)
            cut, a_cuts_b = _cut_state(a, b)
            text = u'%s × %s：' % (a.Id, b.Id)
            if joined:
                text += u'已连接'
            elif cut:
                text += u'实体扣减'
            else:
                text += u'无关系'
            if cut:
                text += u'，方向 %s 扣 %s' % (a.Id if a_cuts_b else b.Id,
                                             b.Id if a_cuts_b else a.Id)
            lines.append(text)
    log_line(u'[deduction] ' + u'; '.join(lines))
    forms.alert(u'\n'.join(lines[:20]))


def switch_deduction(uidoc):
    """翻转选中构件的扣减方向：已连接的换 Join 顺序，实体扣减的删掉反向重建。"""
    doc = uidoc.Document
    elements = selected_elements(uidoc)
    if len(elements) < 2:
        forms.alert(u'请至少选中两个构件（比如墙和板、柱和板）。')
        return
    switched = 0
    skipped = 0
    failures = []
    tx = Transaction(doc, u'扣减关系切换')
    tx.Start()
    try:
        for i in range(len(elements)):
            for j in range(i + 1, len(elements)):
                a, b = elements[i], elements[j]
                try:
                    if _join_state(doc, a, b):
                        JoinGeometryUtils.SwitchJoinOrder(doc, a, b)
                        switched += 1
                        continue
                except Exception as ex:
                    failures.append(u'%s×%s Join：%s' % (a.Id, b.Id, ex))
                    continue
                cut, a_cuts_b = _cut_state(a, b)
                if not cut:
                    skipped += 1
                    continue
                try:
                    SolidSolidCutUtils.RemoveCutBetweenSolids(doc, a, b)
                    # a 扣 b → 反向重建为「b 扣 a」：solidToBeCut=a, cuttingSolid=b
                    if a_cuts_b:
                        SolidSolidCutUtils.AddCutBetweenSolids(doc, a, b)
                    else:
                        SolidSolidCutUtils.AddCutBetweenSolids(doc, b, a)
                    switched += 1
                except Exception as ex:
                    failures.append(u'%s×%s Cut：%s' % (a.Id, b.Id, ex))
        tx.Commit()
    except Exception:
        tx.RollBack()
        failures.append(traceback.format_exc())
        log_line(u'[deduction-switch] ' + traceback.format_exc())
    log_line(u'[deduction-switch] 翻转 %d，无关系跳过 %d，失败 %d'
             % (switched, skipped, len(failures)))
    message = u'已翻转 %d 组扣减/连接方向。' % switched
    if skipped:
        message += u'\n%d 组之间没有连接或扣减关系（可先用「一键扣减」）。' % skipped
    if failures:
        message += u'\n失败 %d 组：\n%s' % (len(failures), u'\n'.join(failures[:3]))
    forms.alert(message)


XAML = u"""
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="构造处理" Height="560" Width="620"
        WindowStartupLocation="CenterScreen" ResizeMode="CanResize"
        Background="#FFF7FBF9">
  <ScrollViewer VerticalScrollBarVisibility="Auto" HorizontalScrollBarVisibility="Disabled">
    <StackPanel Margin="18">
      <TextBlock Text="① 连接与扣减" FontSize="15" FontWeight="Bold" Foreground="#FF263043" Margin="0,0,0,8"/>
      <TextBlock Text="选墙 → 墙端连接开关；选柱 → 柱断墙；选两个以上实体 → 一键扣减。"
                 TextWrapping="Wrap" Foreground="#FF5A6B7B" Margin="0,0,0,10"/>
      <Button x:Name="btnJoin" Content="墙端连接开关（选中的墙，两端切换）" Height="34" Margin="0,0,0,8" FontWeight="Bold"/>
      <Button x:Name="btnColumn" Content="柱断墙（选中的柱，断开穿过的墙）" Height="34" Margin="0,0,0,8" FontWeight="Bold"/>
      <Button x:Name="btnCut" Content="一键扣减（选中的实体两两扣减）" Height="34" Margin="0,0,0,18" FontWeight="Bold"/>
      <StackPanel Orientation="Horizontal" Margin="0,0,0,18">
        <Button x:Name="btnSwitch" Content="翻转扣减方向（选中两个构件）" Height="34" Width="260" Margin="0,0,8,0" FontWeight="Bold"/>
        <Button x:Name="btnDescribe" Content="查看扣减关系" Height="34" Width="160"/>
      </StackPanel>

      <TextBlock Text="② 视图批量改名" FontSize="15" FontWeight="Bold" Foreground="#FF263043" Margin="0,0,0,8"/>
      <Grid Margin="0,0,0,8">
        <Grid.ColumnDefinitions><ColumnDefinition Width="72"/><ColumnDefinition Width="*"/></Grid.ColumnDefinitions>
        <TextBlock Text="前缀" VerticalAlignment="Center" Foreground="#FF263043"/>
        <TextBox x:Name="prefixBox" Grid.Column="1" Height="28" VerticalContentAlignment="Center"/>
      </Grid>
      <Grid Margin="0,0,0,8">
        <Grid.ColumnDefinitions><ColumnDefinition Width="72"/><ColumnDefinition Width="*"/><ColumnDefinition Width="72"/><ColumnDefinition Width="*"/></Grid.ColumnDefinitions>
        <TextBlock Text="把" VerticalAlignment="Center" Foreground="#FF263043"/>
        <TextBox x:Name="fromBox" Grid.Column="1" Height="28" VerticalContentAlignment="Center"/>
        <TextBlock Text="替换为" Grid.Column="2" VerticalAlignment="Center" HorizontalAlignment="Right" Foreground="#FF263043" Margin="0,0,6,0"/>
        <TextBox x:Name="toBox" Grid.Column="3" Height="28" VerticalContentAlignment="Center"/>
      </Grid>
      <Button x:Name="btnRename" Content="按规则改当前文档所有视图名" Height="34" Margin="0,0,0,10" FontWeight="Bold"/>
      <TextBlock x:Name="statusBox" TextWrapping="Wrap" Foreground="#FF5A6B7B" Margin="0,4,0,10"/>
      <Button x:Name="btnClose" Content="关闭" Height="32" Width="110" HorizontalAlignment="Right"/>
    </StackPanel>
  </ScrollViewer>
</Window>
"""


def main():
    doc = active_doc()
    if doc is None:
        forms.alert(u'请先打开一个项目。')
        return
    uidoc = uidoc_ref()

    window = forms.WPFWindow(XAML, literal_string=True)
    window.Title = u'构造处理'

    def _guard(name, func):
        def handler(sender, args):
            try:
                func()
            except Exception:
                log_line(u'[%s] %s' % (name, traceback.format_exc()))
                forms.alert(u'%s 出错：\n%s' % (name, traceback.format_exc().splitlines()[-1]))
        return handler

    def run_join(s, a):
        toggle_join(uidoc)
        window.statusBox.Text = u'墙端连接开关完成。'

    def run_column(s, a):
        column_break_wall(uidoc)
        window.statusBox.Text = u'柱断墙完成。'

    def run_cut(s, a):
        one_click_cut(uidoc)
        window.statusBox.Text = u'一键扣减完成。'

    def run_rename(s, a):
        rename_views(uidoc, window.prefixBox.Text.Trim(),
                     window.fromBox.Text, window.toBox.Text)
        window.statusBox.Text = u'视图改名完成。'

    window.btnJoin.Click += _guard('join', lambda: toggle_join(uidoc))
    window.btnColumn.Click += _guard('column', lambda: column_break_wall(uidoc))
    window.btnCut.Click += _guard('cut', lambda: one_click_cut(uidoc))
    window.btnSwitch.Click += _guard('switch', lambda: switch_deduction(uidoc))
    window.btnDescribe.Click += _guard('describe', lambda: describe_deduction(uidoc))
    window.btnRename.Click += _guard('rename', lambda: rename_views(
        uidoc, window.prefixBox.Text.Trim(), window.fromBox.Text, window.toBox.Text))
    window.btnClose.Click += lambda s, a: window.Close()

    window.statusBox.Text = u'提示：所有改动都在事务里，可 Ctrl+Z 撤销。'
    window.ShowDialog()


try:
    main()
except Exception:
    log_line(traceback.format_exc())
    forms.alert(u'构造处理启动失败：\n' + traceback.format_exc().splitlines()[-1])
