# -*- coding: utf-8 -*-
"""门窗自动编号

按「类型 / 洞口尺寸」分组，给门、窗写「标记」编号（M-01、C-01 …），
可选同时写「注释」= 类型名 + 尺寸。单事务，Ctrl+Z 可整体撤销。
"""
import os
import re
import traceback

import clr
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")
clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")

from System.Collections.Generic import List as DotNetList

from Autodesk.Revit.DB import (
    BuiltInCategory, BuiltInParameter, ElementId, FilteredElementCollector,
    LocationPoint, Transaction, XYZ,
)

from pyrevit import forms, revit, script

doc = revit.doc
uidoc = revit.uidoc
output = script.get_output()

LOG_PATH = r"C:\Users\cyl\Documents\deepseek-harness\default-workspace\_门窗编号日志.txt"


def log_line(text):
    try:
        with open(LOG_PATH, "ab") as fp:
            fp.write((u"%s\n" % text).encode("utf-8"))
    except Exception:
        pass


# ------------------------------------------------------------------ 收集
def is_door(el):
    try:
        return el.Category is not None and el.Category.Id.IntegerValue == int(BuiltInCategory.OST_Doors)
    except Exception:
        return False


def is_window(el):
    try:
        return el.Category is not None and el.Category.Id.IntegerValue == int(BuiltInCategory.OST_Windows)
    except Exception:
        return False


def type_name_of(el):
    try:
        tid = el.GetTypeId()
        if tid is not None and tid.IntegerValue > 0:
            t = doc.GetElement(tid)
            if t is not None:
                return t.Name or u""
    except Exception:
        pass
    try:
        p = el.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM)
        if p is not None:
            return p.AsString() or u""
    except Exception:
        pass
    return u"<未知类型>"


def size_of(el):
    """从类型名里抠 1500x2400，取不到就用包围盒（mm）。"""
    name = type_name_of(el)
    m = re.search(r"(\d{3,4})\s*[xX×*]\s*(\d{3,4})", name)
    if m:
        return int(m.group(1)), int(m.group(2))
    try:
        box = el.get_BoundingBox(None)
        if box is not None:
            return (int(round((box.Max.X - box.Min.X) * 304.8)),
                    int(round((box.Max.Z - box.Min.Z) * 304.8)))
    except Exception:
        pass
    return 0, 0


def point_of(el):
    try:
        loc = el.Location
        if isinstance(loc, LocationPoint):
            return loc.Point
    except Exception:
        pass
    return XYZ(0, 0, 0)


def collect(kind, scope):
    """kind: both/door/window；scope: selected/view/all"""
    if scope == "selected":
        base = [doc.GetElement(i) for i in uidoc.Selection.GetElementIds()]
    elif scope == "view":
        base = list(FilteredElementCollector(doc, uidoc.ActiveView.Id)
                    .WhereElementIsNotElementType().ToElements())
    else:
        base = list(FilteredElementCollector(doc).WhereElementIsNotElementType().ToElements())

    out = []
    for el in base:
        if el is None:
            continue
        if kind == "door" and not is_door(el):
            continue
        if kind == "window" and not is_window(el):
            continue
        if kind == "both" and not (is_door(el) or is_window(el)):
            continue
        out.append(el)
    return out


# ------------------------------------------------------------------ 编号
def group_key(el, mode):
    if mode == "size":
        w, h = size_of(el)
        return u"%dx%d" % (w, h)
    return type_name_of(el)


def prefix_of(el, params):
    if is_door(el):
        return params["door_prefix"]
    if is_window(el):
        return params["window_prefix"]
    return params["door_prefix"]


def sort_elements(elements, order):
    def key(el):
        pt = point_of(el)
        if order == "x":
            return (round(pt.X, 3), round(pt.Y, 3))
        if order == "y":
            return (round(pt.Y, 3), round(pt.X, 3))
        return (type_name_of(el), round(pt.X, 3), round(pt.Y, 3))
    return sorted(elements, key=key)


def build_plan(elements, params):
    """返回 [(element, mark, type_name, size_text, group)]"""
    ordered = sort_elements(elements, params["order"])
    counters = {}
    plan = []
    digits = int(params["digits"])
    start = int(params["start"])

    for el in ordered:
        key = group_key(el, params["group"])
        prefix = prefix_of(el, params)
        if params["per_type"]:
            idx = counters.get(key, start - 1) + 1
            counters[key] = idx
        else:
            idx = counters.get(u"__all__", start - 1) + 1
            counters[u"__all__"] = idx
        mark = u"%s%s%s" % (prefix, params["sep"], str(idx).zfill(digits))
        w, h = size_of(el)
        plan.append((el, mark, type_name_of(el), u"%dx%d" % (w, h), key))
    return plan


def apply_plan(plan, params):
    ok = 0
    failed = []
    tx = Transaction(doc, u"门窗编号")
    tx.Start()
    try:
        for el, mark, tname, size_text, key in plan:
            try:
                p = el.get_Parameter(BuiltInParameter.ALL_MODEL_MARK)
                if p is None or p.IsReadOnly:
                    failed.append((mark, u"没有可写的「标记」参数"))
                    continue
                p.Set(mark)
                if params["write_comment"]:
                    c = el.get_Parameter(BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
                    if c is not None and not c.IsReadOnly:
                        c.Set(u"%s %s" % (tname, size_text))
                ok += 1
            except Exception as ex:
                failed.append((mark, u"%s" % ex))
        tx.Commit()
    except Exception:
        tx.RollBack()
        log_line(traceback.format_exc())
        raise
    return ok, failed


# ------------------------------------------------------------------ 界面
XAML = u"""<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="门窗编号" Height="640" Width="720" WindowStartupLocation="CenterScreen">
  <Grid Margin="16">
    <Grid.RowDefinitions>
      <RowDefinition Height="Auto"/><RowDefinition Height="Auto"/>
      <RowDefinition Height="Auto"/><RowDefinition Height="*"/>
      <RowDefinition Height="Auto"/>
    </Grid.RowDefinitions>

    <TextBlock Grid.Row="0" Text="门窗自动编号" FontSize="19" FontWeight="Bold"/>
    <TextBlock Grid.Row="1" Text="选好范围和编号规则，先「预览」看结果，再「写入编号」。"
               Foreground="#4A5568" Margin="0,4,0,10"/>

    <Grid Grid.Row="2" Margin="0,0,0,10">
      <Grid.ColumnDefinitions>
        <ColumnDefinition Width="Auto"/><ColumnDefinition Width="*"/>
        <ColumnDefinition Width="Auto"/><ColumnDefinition Width="*"/>
      </Grid.ColumnDefinitions>
      <Grid.RowDefinitions>
        <RowDefinition/><RowDefinition/><RowDefinition/>
      </Grid.RowDefinitions>

      <TextBlock Grid.Row="0" Text="编号对象" VerticalAlignment="Center" Margin="0,0,8,6"/>
      <ComboBox x:Name="kindBox" Grid.Row="0" Grid.Column="1" Height="28" Margin="0,0,12,6"/>
      <TextBlock Grid.Row="0" Grid.Column="2" Text="范围" VerticalAlignment="Center" Margin="0,0,8,6"/>
      <ComboBox x:Name="scopeBox" Grid.Row="0" Grid.Column="3" Height="28" Margin="0,0,0,6"/>

      <TextBlock Grid.Row="1" Text="门前缀" VerticalAlignment="Center" Margin="0,0,8,6"/>
      <TextBox x:Name="doorBox" Grid.Row="1" Grid.Column="1" Height="28" Margin="0,0,12,6"/>
      <TextBlock Grid.Row="1" Grid.Column="2" Text="窗前缀" VerticalAlignment="Center" Margin="0,0,8,6"/>
      <TextBox x:Name="winBox" Grid.Row="1" Grid.Column="3" Height="28" Margin="0,0,0,6"/>

      <TextBlock Grid.Row="2" Text="分隔符" VerticalAlignment="Center" Margin="0,0,8,0"/>
      <TextBox x:Name="sepBox" Grid.Row="2" Grid.Column="1" Height="28" Margin="0,0,12,0"/>
      <TextBlock Grid.Row="2" Grid.Column="2" Text="编号位数" VerticalAlignment="Center" Margin="0,0,8,0"/>
      <ComboBox x:Name="digitsBox" Grid.Row="2" Grid.Column="3" Height="28"/>
    </Grid>

    <Grid Grid.Row="3">
      <Grid.ColumnDefinitions>
        <ColumnDefinition Width="*"/><ColumnDefinition Width="14"/><ColumnDefinition Width="*"/>
      </Grid.ColumnDefinitions>
      <StackPanel Grid.Column="0">
        <TextBlock Text="规则" FontWeight="Bold" Margin="0,0,0,6"/>
        <TextBlock Text="分组依据" Margin="0,0,0,2"/>
        <ComboBox x:Name="groupBox" Height="28" Margin="0,0,0,10"/>
        <TextBlock Text="排序方式" Margin="0,0,0,2"/>
        <ComboBox x:Name="orderBox" Height="28" Margin="0,0,0,10"/>
        <CheckBox x:Name="perTypeCheck" Content="每种类型独立编号（不勾＝全局连续编号）" Margin="0,0,0,6"/>
        <CheckBox x:Name="commentCheck" Content="同时写入「注释」＝类型 + 尺寸" Margin="0,0,0,10"/>
        <Grid>
          <Grid.ColumnDefinitions>
            <ColumnDefinition Width="Auto"/><ColumnDefinition Width="*"/>
          </Grid.ColumnDefinitions>
          <TextBlock Text="起始序号" VerticalAlignment="Center" Margin="0,0,8,0"/>
          <TextBox x:Name="startBox" Grid.Column="1" Height="28"/>
        </Grid>
      </StackPanel>

      <StackPanel Grid.Column="2">
        <TextBlock Text="预览" FontWeight="Bold" Margin="0,0,0,6"/>
        <ListBox x:Name="previewList" Height="250"/>
        <TextBlock x:Name="statText" Foreground="#1F6F54" Margin="0,6,0,0"/>
        <TextBlock x:Name="warnText" Foreground="#C0392B" TextWrapping="Wrap" Margin="0,4,0,0"/>
      </StackPanel>
    </Grid>

    <StackPanel Grid.Row="4" Orientation="Horizontal" HorizontalAlignment="Right" Margin="0,12,0,0">
      <Button x:Name="previewBtn" Content="预览" Height="34" Width="100" Margin="0,0,10,0"/>
      <Button x:Name="applyBtn" Content="写入编号" Height="34" Width="120" FontWeight="Bold" Margin="0,0,10,0"/>
      <Button x:Name="closeBtn" Content="关闭" Height="34" Width="90"/>
    </StackPanel>
  </Grid>
</Window>"""


class NumberWindow(forms.WPFWindow):
    def __init__(self):
        forms.WPFWindow.__init__(self, XAML, literal_string=True)
        self.kind_box = self.FindName("kindBox")
        self.scope_box = self.FindName("scopeBox")
        self.door_box = self.FindName("doorBox")
        self.win_box = self.FindName("winBox")
        self.sep_box = self.FindName("sepBox")
        self.digits_box = self.FindName("digitsBox")
        self.group_box = self.FindName("groupBox")
        self.order_box = self.FindName("orderBox")
        self.per_type = self.FindName("perTypeCheck")
        self.comment_check = self.FindName("commentCheck")
        self.start_box = self.FindName("startBox")
        self.preview_list = self.FindName("previewList")
        self.stat = self.FindName("statText")
        self.warn = self.FindName("warnText")
        self.preview_btn = self.FindName("previewBtn")
        self.apply_btn = self.FindName("applyBtn")
        self.close_btn = self.FindName("closeBtn")

        for item in (u"门 + 窗", u"只要门", u"只要窗"):
            self.kind_box.Items.Add(item)
        for item in (u"当前选择", u"当前视图", u"整个项目"):
            self.scope_box.Items.Add(item)
        for item in (u"按类型", u"按洞口尺寸"):
            self.group_box.Items.Add(item)
        for item in (u"从左到右", u"从下到上", u"按类型"):
            self.order_box.Items.Add(item)
        for item in (u"2 位", u"3 位"):
            self.digits_box.Items.Add(item)

        self.kind_box.SelectedIndex = 0
        self.scope_box.SelectedIndex = 1
        self.group_box.SelectedIndex = 0
        self.order_box.SelectedIndex = 0
        self.digits_box.SelectedIndex = 0
        self.door_box.Text = u"M"
        self.win_box.Text = u"C"
        self.sep_box.Text = u"-"
        self.start_box.Text = u"1"
        self.per_type.IsChecked = True
        self.comment_check.IsChecked = False

        self.preview_btn.Click += self._on_preview
        self.apply_btn.Click += self._on_apply
        self.close_btn.Click += lambda s, e: self.Close()

    def params(self):
        kind = ("both", "door", "window")[max(0, int(self.kind_box.SelectedIndex))]
        scope = ("selected", "view", "all")[max(0, int(self.scope_box.SelectedIndex))]
        group = ("type", "size")[max(0, int(self.group_box.SelectedIndex))]
        order = ("x", "y", "type")[max(0, int(self.order_box.SelectedIndex))]
        try:
            digits = int(self.digits_box.SelectedIndex) + 2
        except Exception:
            digits = 2
        try:
            start = int(self.start_box.Text.strip())
        except Exception:
            start = 1
        return {
            "kind": kind, "scope": scope, "group": group, "order": order,
            "digits": digits, "start": start,
            "door_prefix": self.door_box.Text.strip() or u"M",
            "window_prefix": self.win_box.Text.strip() or u"C",
            "sep": self.sep_box.Text,
            "per_type": bool(self.per_type.IsChecked),
            "write_comment": bool(self.comment_check.IsChecked),
        }

    def _on_preview(self, sender, args):
        p = self.params()
        elements = collect(p["kind"], p["scope"])
        self.preview_list.Items.Clear()
        if not elements:
            self.stat.Text = u""
            self.warn.Text = u"没有找到门窗（换个范围试试，或先在模型里选中门窗）。"
            return
        plan = build_plan(elements, p)
        groups = {}
        for el, mark, tname, size_text, key in plan:
            groups[key] = groups.get(key, 0) + 1
        for key in sorted(groups.keys()):
            self.preview_list.Items.Add(u"%s  × %d" % (key, groups[key]))
        for el, mark, tname, size_text, key in plan[:60]:
            self.preview_list.Items.Add(u"    %s  ← %s %s" % (mark, tname, size_text))
        if len(plan) > 60:
            self.preview_list.Items.Add(u"    … 还有 %d 个" % (len(plan) - 60))
        self.stat.Text = u"共 %d 个门窗，%d 种分组" % (len(plan), len(groups))
        self.warn.Text = u""

    def _on_apply(self, sender, args):
        p = self.params()
        elements = collect(p["kind"], p["scope"])
        if not elements:
            forms.alert(u"没有找到门窗。", title=u"门窗编号", warn_icon=True)
            return
        plan = build_plan(elements, p)
        ok, failed = apply_plan(plan, p)
        msg = u"已编号 %d 个门窗。" % ok
        if failed:
            msg += u"\n失败 %d 个，例如：%s" % (len(failed), failed[0][1])
        log_line(u"编号完成 ok=%d failed=%d" % (ok, len(failed)))
        forms.alert(msg, title=u"门窗编号")
        self._on_preview(None, None)


def main():
    if doc is None:
        forms.alert(u"请先打开一个项目。", exitscript=True)
    window = NumberWindow()
    window.ShowDialog()


try:
    main()
except Exception:
    log_line(traceback.format_exc())
    forms.alert(u"门窗编号出错：\n%s" % traceback.format_exc()[-700:],
                title=u"门窗编号", warn_icon=True)
