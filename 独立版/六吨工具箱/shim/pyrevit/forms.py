# -*- coding: utf-8 -*-
"""forms 兼容层：alert / WPFWindow / SelectFromList / CommandSwitchWindow 等"""
import clr
import os

clr.AddReference("System.Windows.Forms")
clr.AddReference("System.Drawing")

from System import String
from System.Windows import (
    Window, Thickness, WindowStartupLocation, ResizeMode,
    MessageBox, MessageBoxButton, MessageBoxImage, MessageBoxResult,
    HorizontalAlignment, VerticalAlignment,
)
from System.Windows.Controls import (
    StackPanel, TextBlock, TextBox, Button, ListBox, CheckBox,
    ScrollViewer, SelectionMode,
)
from System.Windows.Markup import XamlReader
from System.IO import FileStream, FileMode, FileAccess
from System.Windows.Forms import (
    OpenFileDialog, SaveFileDialog, FolderBrowserDialog, DialogResult,
)


def _text(value):
    if isinstance(value, basestring):
        return value
    return unicode(value)


def alert(msg, title=u"六吨工具箱", yes=False, no=False, ok=True,
          warn_icon=False, exitscript=False, **kwargs):
    text = _text(msg)
    head = _text(title)
    if yes or no:
        result = MessageBox.Show(
            text, head, MessageBoxButton.YesNo,
            MessageBoxImage.Warning if warn_icon else MessageBoxImage.Question)
        return result == MessageBoxResult.Yes
    MessageBox.Show(
        text, head, MessageBoxButton.OK,
        MessageBoxImage.Warning if warn_icon else MessageBoxImage.Information)
    if exitscript:
        raise SystemExit()
    return None


class WPFWindow(Window):
    """按 pyRevit 的用法：forms.WPFWindow(xaml, literal_string=True)"""

    def __init__(self, xaml_source, literal_string=False, **kwargs):
        Window.__init__(self)
        if literal_string:
            parsed = XamlReader.Parse(_text(xaml_source))
        else:
            stream = FileStream(xaml_source, FileMode.Open, FileAccess.Read)
            try:
                parsed = XamlReader.Load(stream)
            finally:
                stream.Close()
        if isinstance(parsed, Window):
            self.Title = parsed.Title
            if parsed.Width > 0:
                self.Width = parsed.Width
            if parsed.Height > 0:
                self.Height = parsed.Height
            self.Content = parsed.Content
            self.ResizeMode = parsed.ResizeMode
            self.WindowStartupLocation = parsed.WindowStartupLocation
        else:
            self.Content = parsed


class _PickWindow(Window):
    def __init__(self, items, title, multiselect):
        Window.__init__(self)
        self.Title = _text(title)
        self.Width = 420
        self.Height = 520
        self.WindowStartupLocation = WindowStartupLocation.CenterScreen
        self.result = None

        root = StackPanel()
        root.Margin = Thickness(10)

        self.listbox = ListBox()
        self.listbox.Height = 420
        if multiselect:
            self.listbox.SelectionMode = SelectionMode.Extended
        for item in items:
            self.listbox.Items.Add(_text(item))
        root.Children.Add(self.listbox)

        buttons = StackPanel()
        buttons.Orientation = 0
        buttons.HorizontalAlignment = HorizontalAlignment.Right
        buttons.Margin = Thickness(0, 8, 0, 0)

        ok = Button()
        ok.Content = u"确定"
        ok.Width = 80
        ok.Margin = Thickness(4)
        ok.Click += self._on_ok

        cancel = Button()
        cancel.Content = u"取消"
        cancel.Width = 80
        cancel.Margin = Thickness(4)
        cancel.Click += self._on_cancel

        buttons.Children.Add(ok)
        buttons.Children.Add(cancel)
        root.Children.Add(buttons)
        self.Content = root

    def _on_ok(self, sender, args):
        picked = [item for item in self.listbox.SelectedItems]
        if not picked and self.listbox.SelectedItem is not None:
            picked = [self.listbox.SelectedItem]
        self.result = picked
        self.Close()

    def _on_cancel(self, sender, args):
        self.result = None
        self.Close()


def SelectFromList(items, title=u"请选择", multiselect=False, button_name=None,
                   width=500, height=600, **kwargs):
    items = list(items)
    if not items:
        return None
    window = _PickWindow(items, title, multiselect)
    window.ShowDialog()
    if window.result is None:
        return None
    if multiselect:
        return [str(item) for item in window.result]
    return str(window.result[0])


def CommandSwitchWindow(command_switches, title=u"选项", **kwargs):
    window = Window()
    window.Title = _text(title)
    window.Width = 380
    window.Height = 120 + 32 * len(command_switches)
    window.WindowStartupLocation = WindowStartupLocation.CenterScreen

    root = StackPanel()
    root.Margin = Thickness(12)
    checks = []
    for name, default in command_switches.items():
        box = CheckBox()
        box.Content = _text(name)
        box.IsChecked = bool(default)
        checks.append((name, box))
        root.Children.Add(box)

    result = {}

    def on_ok(sender, args):
        for name, box in checks:
            result[name] = bool(box.IsChecked)
        window.Close()

    buttons = StackPanel()
    buttons.Orientation = 0
    buttons.HorizontalAlignment = HorizontalAlignment.Right
    ok = Button()
    ok.Content = u"确定"
    ok.Width = 80
    ok.Click += on_ok
    buttons.Children.Add(ok)
    root.Children.Add(buttons)
    window.Content = root
    window.ShowDialog()
    return result


def ask_for_string(prompt=u"请输入", title=u"输入", default=None, **kwargs):
    window = Window()
    window.Title = _text(title)
    window.Width = 420
    window.Height = 180
    window.WindowStartupLocation = WindowStartupLocation.CenterScreen

    root = StackPanel()
    root.Margin = Thickness(12)

    label = TextBlock()
    label.Text = _text(prompt)
    label.TextWrapping = 2
    root.Children.Add(label)

    box = TextBox()
    box.Margin = Thickness(0, 8, 0, 8)
    if default is not None:
        box.Text = _text(default)
    root.Children.Add(box)

    state = {"value": None}

    def on_ok(sender, args):
        state["value"] = box.Text
        window.Close()

    buttons = StackPanel()
    buttons.Orientation = 0
    buttons.HorizontalAlignment = HorizontalAlignment.Right
    ok = Button()
    ok.Content = u"确定"
    ok.Width = 80
    ok.Click += on_ok
    buttons.Children.Add(ok)
    root.Children.Add(buttons)

    window.Content = root
    window.ShowDialog()
    return state["value"]


def _ext_filter(file_ext):
    if not file_ext:
        return u"所有文件 (*.*)|*.*"
    ext = file_ext if file_ext.startswith(".") else "." + file_ext
    return u"{0} 文件 (*{0})|*{0}|所有文件 (*.*)|*.*".format(ext)


def pick_file(file_ext=None, title=u"选择文件", multi_file=False, **kwargs):
    dialog = OpenFileDialog()
    dialog.Title = _text(title)
    dialog.Filter = _ext_filter(file_ext)
    dialog.Multiselect = bool(multi_file)
    if dialog.ShowDialog() == DialogResult.OK:
        if multi_file:
            return list(dialog.FileNames)
        return dialog.FileName
    return None


def pick_folder(title=u"选择文件夹", **kwargs):
    dialog = FolderBrowserDialog()
    dialog.Description = _text(title)
    if dialog.ShowDialog() == DialogResult.OK:
        return dialog.SelectedPath
    return None


def save_file(file_ext=None, title=u"保存到", default_name=None, **kwargs):
    dialog = SaveFileDialog()
    dialog.Title = _text(title)
    dialog.Filter = _ext_filter(file_ext)
    if default_name:
        dialog.FileName = _text(default_name)
    if dialog.ShowDialog() == DialogResult.OK:
        return dialog.FileName
    return None
