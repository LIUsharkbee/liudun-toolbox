# -*- coding: utf-8 -*-
"""script 兼容层：get_output / get_logger / flush"""
import os
import traceback

from System.Windows import Window, Thickness, WindowStartupLocation
from System.Windows.Controls import StackPanel, TextBlock, TextBox, Button, ScrollViewer
from System.Windows.Media import Brushes, FontFamily

LOG_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")),
                       u"六吨工具箱", u"logs")

_buffers = []


def _ensure_log_dir():
    try:
        if not os.path.isdir(LOG_DIR):
            os.makedirs(LOG_DIR)
    except Exception:
        pass


def get_logger(name=u"六吨工具箱"):
    _ensure_log_dir()

    class Logger(object):
        def _write(self, level, message):
            line = u"[{0}] {1}".format(level, message)
            try:
                path = os.path.join(LOG_DIR, u"运行日志.txt")
                handle = open(path, "a")
                handle.write(line + u"\n")
                handle.close()
            except Exception:
                pass

        def info(self, message):
            self._write(u"INFO", message)

        def warning(self, message):
            self._write(u"WARN", message)

        def error(self, message):
            self._write(u"ERROR", message)

        def debug(self, message):
            self._write(u"DEBUG", message)

    return Logger()


class _OutputBuffer(object):
    def __init__(self):
        self.chunks = []

    def add(self, text):
        self.chunks.append(text)


def _render_markdown(text):
    return u"%s" % text


class Output(object):
    """简化版输出窗口：先把内容缓存，脚本结束后统一弹出。"""

    def __init__(self):
        self.buffer = _OutputBuffer()
        _buffers.append(self.buffer)

    def print_md(self, text, *args, **kwargs):
        self.buffer.add(_render_markdown(text))
        return self

    def print_html(self, text, *args, **kwargs):
        self.buffer.add(text)
        return self

    def print_code(self, text, *args, **kwargs):
        self.buffer.add(text)
        return self

    def print_text(self, text, *args, **kwargs):
        self.buffer.add(text)
        return self

    def print_plain(self, text, *args, **kwargs):
        self.buffer.add(text)
        return self

    def print(self, text, *args, **kwargs):
        self.buffer.add(u"%s" % (text,))
        return self

    def print_table(self, table, columns=None, title=None, **kwargs):
        lines = []
        if title:
            lines.append(u"### " + u"%s" % title)
        rows = list(table)
        if columns:
            lines.append(u" | ".join([u"%s" % item for item in columns]))
            lines.append(u" | ".join([u"---"] * len(columns)))
        for row in rows:
            lines.append(u" | ".join([u"%s" % item for item in row]))
        self.buffer.add(u"\n".join(lines))
        return self

    def linkify(self, url):
        return u"%s" % url

    def update_output(self):
        return self

    def close(self):
        return self


def get_output():
    return Output()


class _ReportWindow(Window):
    def __init__(self, text):
        Window.__init__(self)
        self.Title = u"六吨工具箱 · 结果"
        self.Width = 780
        self.Height = 620
        self.WindowStartupLocation = WindowStartupLocation.CenterScreen

        root = StackPanel()
        root.Margin = Thickness(8)

        viewer = TextBox()
        viewer.IsReadOnly = True
        viewer.AcceptsReturn = True
        viewer.TextWrapping = 2
        viewer.VerticalScrollBarVisibility = 3
        viewer.Height = 520
        viewer.FontFamily = FontFamily(u"Consolas")
        viewer.Text = text
        root.Children.Add(viewer)

        close = Button()
        close.Content = u"关闭"
        close.Width = 90
        close.Margin = Thickness(0, 8, 0, 0)
        close.HorizontalAlignment = 0
        close.Click += self._on_close
        root.Children.Add(close)

        self.Content = root

    def _on_close(self, sender, args):
        self.Close()


def flush():
    """脚本跑完后把缓存内容显示出来。"""
    if not _buffers:
        return
    chunks = []
    for buffer in _buffers:
        chunks.extend(buffer.chunks)
    del _buffers[:]
    if not chunks:
        return
    text = u"\n\n".join(chunks)
    window = _ReportWindow(text)
    window.ShowDialog()
