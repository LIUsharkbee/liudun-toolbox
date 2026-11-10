# -*- coding: utf-8 -*-
"""清理 Revit 备份文件

扫描目标文件夹里的 Revit 备份副本并批量删除：

  * 项目备份：项目.0001.rvt、项目.0002.rvt …（Revit 保存时按“备份数量”生成）
  * 族备份：  族.0001.rfa
  * 样板备份：样板.0001.rte
  * 旧式备份：项目.rvt.bak

界面用 pyRevit 官方 WPFWindow + XAML：单窗口、多选、默认移到回收站。
不会碰正在使用的模型文件——只匹配“序号备份”和 .bak。
"""

from __future__ import print_function

import datetime
import os
import re
import traceback

from pyrevit import revit, forms, script

doc = revit.doc
output = script.get_output()

BACKUP_PATTERN = re.compile(
    r"(\.\d{4,}\.(rvt|rfa|rte|rft))$|(\.(rvt|rfa|rte)\.bak)$",
    re.IGNORECASE)

MB = 1024.0 * 1024.0

XAML = u"""
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="清理 Revit 备份文件"
        Width="780" Height="660" MinWidth="640" MinHeight="420"
        WindowStartupLocation="CenterScreen"
        ResizeMode="CanResize"
        ShowInTaskbar="False">
  <ScrollViewer VerticalScrollBarVisibility="Auto"
                HorizontalScrollBarVisibility="Disabled">
    <Grid Margin="14">
      <Grid.RowDefinitions>
        <RowDefinition Height="Auto"/>
        <RowDefinition Height="Auto"/>
        <RowDefinition Height="Auto"/>
        <RowDefinition Height="Auto"/>
        <RowDefinition Height="Auto"/>
        <RowDefinition Height="Auto"/>
        <RowDefinition Height="Auto"/>
      </Grid.RowDefinitions>

      <TextBlock Grid.Row="0" Text="扫描目录" FontWeight="SemiBold" Margin="0,0,0,6"/>

      <Grid Grid.Row="1" Margin="0,0,0,12">
        <Grid.ColumnDefinitions>
          <ColumnDefinition Width="*"/>
          <ColumnDefinition Width="Auto"/>
          <ColumnDefinition Width="Auto"/>
        </Grid.ColumnDefinitions>
        <TextBox x:Name="FolderBox" Grid.Column="0" Height="28"
                 VerticalContentAlignment="Center" Padding="4,2"/>
        <Button x:Name="BrowseBtn" Grid.Column="1" Content="浏览…"
                Width="76" Height="28" Margin="6,0,0,0"/>
        <Button x:Name="ScanBtn" Grid.Column="2" Content="扫描"
                Width="76" Height="28" Margin="6,0,0,0"/>
      </Grid>

      <TextBlock Grid.Row="2" Text="备份文件" FontWeight="SemiBold" Margin="0,0,0,6"/>

      <ListBox x:Name="FileList" Grid.Row="3" Height="320"
               SelectionMode="Extended"
               FontFamily="Consolas"
               ScrollViewer.VerticalScrollBarVisibility="Auto"/>

      <StackPanel Grid.Row="4" Orientation="Horizontal" Margin="0,8,0,0">
        <Button x:Name="SelectAllBtn" Content="全选" Width="72" Height="28" Margin="0,0,8,0"/>
        <Button x:Name="ClearBtn" Content="全不选" Width="72" Height="28" Margin="0,0,8,0"/>
        <Button x:Name="OldBtn" Content="仅选 30 天前的" Width="128" Height="28"/>
      </StackPanel>

      <CheckBox x:Name="RecycleCheck" Grid.Row="5"
                Content="删除时移到回收站（取消勾选则永久删除）"
                IsChecked="True" Margin="0,12,0,10"/>

      <Grid Grid.Row="6">
        <Grid.ColumnDefinitions>
          <ColumnDefinition Width="*"/>
          <ColumnDefinition Width="Auto"/>
          <ColumnDefinition Width="Auto"/>
        </Grid.ColumnDefinitions>
        <TextBlock x:Name="StatusText" Grid.Column="0"
                   Text="尚未扫描" VerticalAlignment="Center"/>
        <Button x:Name="DeleteBtn" Grid.Column="1" Content="删除选中"
                Width="96" Height="30" Margin="8,0,0,0"/>
        <Button x:Name="CloseBtn" Grid.Column="2" Content="关闭"
                Width="72" Height="30" Margin="8,0,0,0"/>
      </Grid>
    </Grid>
  </ScrollViewer>
</Window>
"""


def safe_call(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def human_size(size):
    if size >= MB * 1024:
        return u"{0:.2f} GB".format(size / (MB * 1024))
    if size >= MB:
        return u"{0:.1f} MB".format(size / MB)
    return u"{0:.0f} KB".format(size / 1024.0)


def scan_backups(folder):
    """返回 [(path, size, mtime)]，按大小降序。"""
    found = []
    if not folder or not os.path.isdir(folder):
        return found
    for name in safe_call(lambda: os.listdir(folder), []) or []:
        if not BACKUP_PATTERN.search(name):
            continue
        path = os.path.join(folder, name)
        if not os.path.isfile(path):
            continue
        size = safe_call(lambda: os.path.getsize(path), 0) or 0
        mtime = safe_call(lambda: os.path.getmtime(path), 0) or 0
        found.append((path, size, mtime))
    found.sort(key=lambda item: -item[1])
    return found


def delete_file(path, to_recycle=True):
    """优先移到回收站，失败则永久删除。返回 (ok, method)。"""
    if to_recycle:
        try:
            import clr
            clr.AddReference("Microsoft.VisualBasic")
            from Microsoft.VisualBasic.FileIO import (
                FileSystem,
                RecycleOption,
                UIOption,
            )
            FileSystem.DeleteFile(path, UIOption.OnlyErrorDialogs,
                                  RecycleOption.SendToRecycleBin)
            return True, u"回收站"
        except Exception:
            pass
    try:
        os.remove(path)
        return True, u"永久删除"
    except Exception as ex:
        return False, u"{0}".format(ex)


def default_folder():
    path = safe_call(lambda: doc.PathName, u"") if doc is not None else u""
    if path and os.path.isfile(path):
        return os.path.dirname(path)
    return safe_call(lambda: os.path.expanduser(u"~"), u"")


def display_text(size, mtime, name):
    when = datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")
    return u"{0:>9}   {1}   {2}".format(human_size(size), when, name)


class CleanupWindow(forms.WPFWindow):
    def __init__(self):
        forms.WPFWindow.__init__(self, XAML, literal_string=True)
        self.files = []
        self.deleted = []

        self.FolderBox.Text = default_folder()
        self.BrowseBtn.Click += self.on_browse
        self.ScanBtn.Click += self.on_scan
        self.SelectAllBtn.Click += self.on_select_all
        self.ClearBtn.Click += self.on_clear
        self.OldBtn.Click += self.on_select_old
        self.DeleteBtn.Click += self.on_delete
        self.CloseBtn.Click += self.on_close

        safe_call(lambda: self.on_scan(None, None))

    # ---------- 逻辑 ----------
    def fill(self, entries):
        self.files = entries
        self.FileList.Items.Clear()
        for path, size, mtime in entries:
            self.FileList.Items.Add(display_text(size, mtime, os.path.basename(path)))
        total = sum(size for _, size, _ in entries)
        if entries:
            self.StatusText.Text = u"{0} 个备份，共 {1}".format(
                len(entries), human_size(total))
        else:
            self.StatusText.Text = u"没有找到备份文件"

    def on_browse(self, sender, args):
        picker = safe_call(lambda: forms.pick_folder, None)
        if picker is None:
            forms.alert(u"当前 pyRevit 不支持选文件夹。")
            return
        folder = picker()
        if folder:
            self.FolderBox.Text = folder
            self.on_scan(None, None)

    def on_scan(self, sender, args):
        folder = (self.FolderBox.Text or u"").strip()
        if not folder or not os.path.isdir(folder):
            self.StatusText.Text = u"目录不存在"
            self.fill([])
            return
        self.fill(scan_backups(folder))

    def on_select_all(self, sender, args):
        self.FileList.SelectAll()

    def on_clear(self, sender, args):
        self.FileList.UnselectAll()

    def on_select_old(self, sender, args):
        self.FileList.UnselectAll()
        limit = datetime.datetime.now() - datetime.timedelta(days=30)
        for index, (path, size, mtime) in enumerate(self.files):
            if datetime.datetime.fromtimestamp(mtime) < limit:
                self.FileList.SelectedItems.Add(self.FileList.Items[index])

    def selected_paths(self):
        chosen = []
        for item in list(self.FileList.SelectedItems):
            text = u"{0}".format(item)
            for path, size, mtime in self.files:
                if text.endswith(os.path.basename(path)):
                    chosen.append((path, size))
                    break
        return chosen

    def on_delete(self, sender, args):
        chosen = self.selected_paths()
        if not chosen:
            forms.alert(u"请先在列表里选择要删除的文件。")
            return
        total = sum(size for _, size in chosen)
        to_recycle = bool(self.RecycleCheck.IsChecked)
        message = u"确定要删除 {0} 个文件、释放 {1} 吗？\n\n{2}".format(
            len(chosen), human_size(total),
            u"删除后可在回收站还原。" if to_recycle else u"将永久删除，无法恢复！")
        answer = forms.alert(message, title=u"确认删除", yes=True, no=True)
        if answer not in (True, u"Yes", u"yes", u"是"):
            return

        ok_count = 0
        freed = 0
        failed = []
        methods = set()
        for path, size in chosen:
            ok, method = delete_file(path, to_recycle)
            if ok:
                ok_count += 1
                freed += size
                methods.add(method)
            else:
                failed.append(u"{0}：{1}".format(os.path.basename(path), method))

        self.deleted = [p for p, _ in chosen]
        self.on_scan(None, None)
        self.StatusText.Text = u"已删除 {0} 个，释放 {1}".format(
            ok_count, human_size(freed))

        output.print_md(u"## 清理 Revit 备份文件")
        output.print_md(u"目录：`{0}`".format(self.FolderBox.Text))
        output.print_md(u"删除 **{0}** 个文件，释放 **{1}**（{2}）".format(
            ok_count, human_size(freed), u"、".join(methods) or u"-"))
        if failed:
            output.print_md(u"失败 {0} 个：\n".format(len(failed)) +
                            u"\n".join(u"- " + f for f in failed[:10]))

    def on_close(self, sender, args):
        self.Close()


def main():
    window = CleanupWindow()
    window.ShowDialog()


try:
    main()
except Exception:
    output.print_md(u"脚本出错：\n```\n{0}\n```".format(traceback.format_exc()))
    forms.alert(u"脚本出错，详见输出窗口。", title=u"清理 Revit 备份文件")
