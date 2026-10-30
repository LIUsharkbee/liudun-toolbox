# -*- coding: utf-8 -*-
"""族管理器

浏览本地 .rfa 族库：缩略图网格、搜索、一键载入项目 / 打开编辑 / 定位文件。

缩略图直接从 .rfa 的复合文档里读（RevitPreview 流里的 PNG），
不启动 Revit、不依赖任何外部库；首次扫描后缓存到本地，之后秒开。
"""

from __future__ import print_function

import hashlib
import io
import json
import os
import struct
import traceback

import clr

# 必须显式加载 WPF 程序集，否则 TextWrapping / XamlReader / BitmapImage 无法导入
clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")

from System import Uri, UriKind
from System.Windows.Markup import XamlReader

from pyrevit import forms, revit, script

doc = revit.doc
uidoc = revit.uidoc
output = script.get_output()
logger = script.get_logger()

try:
    text_type = unicode
except NameError:
    text_type = str

APP_DIR = os.path.join(os.environ.get("APPDATA", ""), "六吨工具箱")
CONFIG_PATH = os.path.join(APP_DIR, "family-manager.json")
CACHE_DIR = os.path.join(os.environ.get("LOCALAPPDATA", ""), "六吨工具箱", "family-thumbs")
THUMB_SIZE = 112

# ---------------------------------------------------------------- 缩略图解析

CFB_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
FREESECT = 0xFFFFFFFF
ENDOFCHAIN = 0xFFFFFFFE


class _Compound(object):
    def __init__(self, data):
        if data[:8] != CFB_MAGIC:
            raise ValueError("not compound")
        self.data = data
        self.sector = 1 << struct.unpack_from("<H", data, 0x1E)[0]
        self.mini_sector = 1 << struct.unpack_from("<H", data, 0x20)[0]
        self.num_fat = struct.unpack_from("<I", data, 0x2C)[0]
        self.dir_start = struct.unpack_from("<I", data, 0x30)[0]
        self.cutoff = struct.unpack_from("<I", data, 0x38)[0]
        self.mini_fat_start = struct.unpack_from("<I", data, 0x3C)[0]
        self.num_mini_fat = struct.unpack_from("<I", data, 0x40)[0]
        self.difat_start = struct.unpack_from("<I", data, 0x44)[0]
        self.num_difat = struct.unpack_from("<I", data, 0x48)[0]
        self.fat = self._load_fat()
        self.entries = self._load_dir()
        self.mini_fat = self._load_mini_fat()
        root = self.entries[0] if self.entries else {"start": ENDOFCHAIN, "size": 0}
        self.mini_stream = self._read_chain(root["start"], root["size"])

    def _sector(self, index):
        offset = (index + 1) * self.sector
        return self.data[offset:offset + self.sector]

    def _load_fat(self):
        difat = list(struct.unpack_from("<109I", self.data, 0x4C))
        sector = self.difat_start
        guard = 0
        while sector not in (FREESECT, ENDOFCHAIN) and guard < self.num_difat + 4:
            raw = self._sector(sector)
            if len(raw) < self.sector:
                break
            values = struct.unpack("<%dI" % (self.sector // 4), raw)
            difat.extend(values[:-1])
            sector = values[-1]
            guard += 1
        fat = []
        for index in difat:
            if index in (FREESECT, ENDOFCHAIN):
                continue
            raw = self._sector(index)
            if len(raw) < self.sector:
                continue
            fat.extend(struct.unpack("<%dI" % (self.sector // 4), raw))
        return fat

    def _chain(self, start):
        seen = set()
        sector = start
        while sector not in (FREESECT, ENDOFCHAIN) and sector < len(self.fat):
            if sector in seen:
                break
            seen.add(sector)
            yield sector
            sector = self.fat[sector]

    def _read_chain(self, start, size=None):
        out = io.BytesIO()
        for sector in self._chain(start):
            out.write(self._sector(sector))
        data = out.getvalue()
        return data[:size] if size is not None else data

    def _load_dir(self):
        raw = self._read_chain(self.dir_start)
        entries = []
        for offset in range(0, len(raw), 128):
            block = raw[offset:offset + 128]
            if len(block) < 128:
                break
            name_len = struct.unpack_from("<H", block, 0x40)[0]
            entries.append({
                "name": block[:max(0, name_len - 2)].decode("utf-16-le", "ignore"),
                "type": struct.unpack_from("<B", block, 0x42)[0],
                "start": struct.unpack_from("<I", block, 0x74)[0],
                "size": struct.unpack_from("<Q", block, 0x78)[0],
            })
        return entries

    def _load_mini_fat(self):
        raw = self._read_chain(self.mini_fat_start)
        count = len(raw) // 4
        return list(struct.unpack("<%dI" % count, raw[:count * 4]))

    def read_stream(self, entry):
        size = entry["size"]
        if size < self.cutoff:
            out = io.BytesIO()
            sector = entry["start"]
            seen = set()
            while sector not in (FREESECT, ENDOFCHAIN) and sector < len(self.mini_fat):
                if sector in seen:
                    break
                seen.add(sector)
                offset = sector * self.mini_sector
                out.write(self.mini_stream[offset:offset + self.mini_sector])
                sector = self.mini_fat[sector]
            return out.getvalue()[:size]
        return self._read_chain(entry["start"], size)


def _find_image(data):
    start = data.find(b"\x89PNG\r\n\x1a\n")
    if start >= 0:
        end = data.find(b"IEND", start)
        if end > 0:
            return data[start:end + 8]
    start = data.find(b"\xff\xd8\xff")
    if start >= 0:
        end = data.find(b"\xff\xd9", start)
        if end > 0:
            return data[start:end + 2]
    return None


def extract_preview_bytes(rfa_path):
    try:
        with open(rfa_path, "rb") as handle:
            data = handle.read()
        compound = _Compound(data)
        for entry in compound.entries:
            if entry["type"] == 2 and entry["size"] > 0 and "preview" in entry["name"].lower():
                image = _find_image(compound.read_stream(entry))
                if image:
                    return image
        for entry in compound.entries:
            if entry["type"] == 2 and entry["size"] > 0:
                image = _find_image(compound.read_stream(entry))
                if image:
                    return image
    except Exception:
        return None
    return None


def thumb_for(path):
    try:
        stat = os.stat(path)
    except Exception:
        return None
    digest = hashlib.md5(path.encode("utf-8")).hexdigest()[:16]
    key = u"{0}_{1}_{2}".format(digest, int(stat.st_size), int(stat.st_mtime))
    if not os.path.isdir(CACHE_DIR):
        try:
            os.makedirs(CACHE_DIR)
        except Exception:
            return None
    target = os.path.join(CACHE_DIR, key + u".png")
    if os.path.isfile(target) and os.path.getsize(target) > 0:
        return target
    data = extract_preview_bytes(path)
    if not data:
        return None
    try:
        with open(target, "wb") as handle:
            handle.write(data)
        return target
    except Exception:
        return None


# ---------------------------------------------------------------- 配置

def load_config():
    if os.path.isfile(CONFIG_PATH):
        try:
            with io.open(CONFIG_PATH, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            libs = [p for p in data.get("libs", []) if os.path.isdir(p)]
            if libs:
                return libs
        except Exception:
            pass
    guesses = [
        os.path.join(os.path.expanduser("~"), "Documents", "Revit 族库"),
        os.path.join(os.path.expanduser("~"), "Documents", "族库"),
    ]
    return [p for p in guesses if os.path.isdir(p)]


def save_config(libs):
    try:
        if not os.path.isdir(APP_DIR):
            os.makedirs(APP_DIR)
        with io.open(CONFIG_PATH, "w", encoding="utf-8") as handle:
            handle.write(text_type(json.dumps({"libs": libs}, ensure_ascii=False, indent=2)))
    except Exception:
        logger.error(traceback.format_exc())


def scan_libraries(libs):
    items = []
    for lib in libs:
        for root, dirs, files in os.walk(lib):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for name in files:
                if not name.lower().endswith(".rfa"):
                    continue
                full = os.path.join(root, name)
                try:
                    stat = os.stat(full)
                except Exception:
                    continue
                items.append({
                    "path": full,
                    "name": os.path.splitext(name)[0],
                    "folder": os.path.basename(root),
                    "size": stat.st_size,
                })
    items.sort(key=lambda x: x["name"].lower())
    return items


def load_bitmap(path):
    bitmap = BitmapImage()
    bitmap.BeginInit()
    bitmap.UriSource = Uri(path.replace(u"\\", u"/"), UriKind.Absolute)
    bitmap.CacheOption = BitmapCacheOption.OnLoad
    bitmap.DecodePixelWidth = THUMB_SIZE * 2
    bitmap.EndInit()
    bitmap.Freeze()
    return bitmap


# ---------------------------------------------------------------- 界面

SHELL_XAML = u"""<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
    xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
    Title="族管理器" Height="760" Width="1020" WindowStartupLocation="CenterScreen">
  <Grid Margin="12">
    <Grid.RowDefinitions>
      <RowDefinition Height="Auto"/>
      <RowDefinition Height="*"/>
      <RowDefinition Height="Auto"/>
    </Grid.RowDefinitions>
    <StackPanel Grid.Row="0" Orientation="Horizontal" Margin="0,0,0,8">
      <TextBox x:Name="searchBox" Width="240" Height="28" Margin="0,0,8,0"/>
      <Button x:Name="btnRefresh" Content="刷新" Height="28" Margin="0,0,6,0" Padding="12,2"/>
      <Button x:Name="btnAdd" Content="添加库目录..." Height="28" Margin="0,0,6,0" Padding="12,2"/>
      <Button x:Name="btnRemove" Content="移除该目录" Height="28" Margin="0,0,6,0" Padding="12,2"/>
      <Button x:Name="btnMore" Content="加载更多" Height="28" Margin="0,0,6,0" Padding="12,2"/>
      <TextBlock x:Name="statusText" VerticalAlignment="Center" Margin="10,0,0,0" Text="准备中..."/>
    </StackPanel>
    <ScrollViewer Grid.Row="1" VerticalScrollBarVisibility="Auto" HorizontalScrollBarVisibility="Disabled">
      <WrapPanel x:Name="wrapPanel" Orientation="Horizontal"/>
    </ScrollViewer>
    <StackPanel Grid.Row="2" Orientation="Horizontal" Margin="0,8,0,0">
      <Button x:Name="btnLoad" Content="载入到项目" Height="28" Margin="0,0,6,0" Padding="12,2"/>
      <Button x:Name="btnOpen" Content="打开编辑" Height="28" Margin="0,0,6,0" Padding="12,2"/>
      <Button x:Name="btnReveal" Content="打开所在文件夹" Height="28" Padding="12,2"/>
    </StackPanel>
  </Grid>
</Window>"""

CARD_XAML = u"""<Border xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
    xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
    Width="140" Margin="6" Padding="6" CornerRadius="6"
    Background="Transparent" BorderThickness="1" BorderBrush="Transparent">
  <StackPanel>
    <Image x:Name="thumb" Width="112" Height="112" Stretch="Uniform"/>
    <TextBlock x:Name="title" TextWrapping="Wrap" FontSize="12" Margin="0,6,0,0" MaxWidth="126"/>
    <TextBlock x:Name="folder" FontSize="11" Opacity="0.55" MaxWidth="126"/>
  </StackPanel>
</Border>"""


def make_bitmap(path):
    """尽量用 BitmapImage，不行再退到 BitmapFrame。"""
    uri = Uri(path.replace(u"\\", u"/"), UriKind.Absolute)
    try:
        from System.Windows.Media.Imaging import BitmapCacheOption, BitmapImage
        bitmap = BitmapImage()
        bitmap.BeginInit()
        bitmap.UriSource = uri
        bitmap.CacheOption = BitmapCacheOption.OnLoad
        bitmap.DecodePixelWidth = THUMB_SIZE * 2
        bitmap.EndInit()
        bitmap.Freeze()
        return bitmap
    except Exception:
        pass
    try:
        from System.Windows.Media.Imaging import BitmapCacheOption, BitmapFrame
        # BitmapCreateOptions 在 IronPython 里没有 None 成员，直接用 0
        frame = BitmapFrame.Create(uri, 0, BitmapCacheOption.OnLoad)
        frame.Freeze()
        return frame
    except Exception:
        return None


class FamilyManagerWindow(forms.WPFWindow):
    PAGE = 240          # 每次显示多少个族
    BATCH = 8           # 每个时钟周期渲染几个

    def __init__(self):
        forms.WPFWindow.__init__(self, SHELL_XAML, literal_string=True)
        self.items = []
        self.cards = {}
        self.selected = None
        self.queue = []
        self.shown = 0
        self.limit = self.PAGE
        self.thumb_ok = 0
        self.thumb_none = 0
        self.thumb_err = 0
        self.first_error = None

        self.search_box = self.FindName("searchBox")
        self.status = self.FindName("statusText")
        self.wrap = self.FindName("wrapPanel")

        self.search_box.TextChanged += self._on_search
        self.FindName("btnRefresh").Click += self._on_refresh
        self.FindName("btnAdd").Click += self._on_add_lib
        self.FindName("btnRemove").Click += self._on_remove_lib
        self.FindName("btnLoad").Click += self._on_load
        self.FindName("btnOpen").Click += self._on_open
        self.FindName("btnReveal").Click += self._on_reveal
        self.FindName("btnMore").Click += self._on_more

        from System.Windows.Threading import DispatcherTimer
        self.timer = DispatcherTimer()
        self.timer.Interval = __import__("System").TimeSpan.FromMilliseconds(30)
        self.timer.Tick += self._on_tick

        self._reload()

    # ---------- 数据 ----------
    def _reload(self):
        libs = load_config()
        if not libs:
            self.status.Text = u"还没有库目录，点「添加库目录...」选一个装 .rfa 的文件夹。"
            return
        self.status.Text = u"正在扫描 {0} 个目录...".format(len(libs))
        self.items = scan_libraries(libs)
        self.limit = self.PAGE
        self._render()

    def _matches(self):
        keyword = (self.search_box.Text or u"").strip().lower()
        return [i for i in self.items if not keyword or keyword in i["name"].lower()]

    def _render(self):
        self.timer.Stop()
        self.wrap.Children.Clear()
        self.cards = {}
        self.thumb_ok = self.thumb_none = self.thumb_err = 0
        self.first_error = None
        self.shown = 0
        shown = self._matches()
        self.queue = shown[:self.limit]
        self.total = len(shown)
        self._update_status()
        self.timer.Start()

    def _update_status(self):
        text = u"显示 {0} / {1} 个族".format(self.shown, self.total)
        text += u" · 缩略图 {0}".format(self.thumb_ok)
        if self.thumb_none:
            text += u" · 无预览 {0}".format(self.thumb_none)
        if self.thumb_err:
            text += u" · 显示失败 {0}".format(self.thumb_err)
        if self.first_error:
            text += u" · {0}".format(self.first_error[:60])
        self.status.Text = text

    def _on_tick(self, sender, args):
        for _ in range(self.BATCH):
            if not self.queue:
                self.timer.Stop()
                self._update_status()
                return
            item = self.queue.pop(0)
            card = self._make_card(item)
            if card is not None:
                self.wrap.Children.Add(card)
                self.cards[item["path"]] = card
                self.shown += 1
        self._update_status()

    def _make_card(self, item):
        try:
            card = XamlReader.Parse(CARD_XAML)
        except Exception:
            self.first_error = u"XAML: " + traceback.format_exc().splitlines()[-1]
            return None

        title = card.FindName("title")
        folder = card.FindName("folder")
        thumb = card.FindName("thumb")

        # FindName 在部分 WPF 版本上拿不到子元素，兜底按结构取
        if thumb is None or title is None:
            try:
                panel = card.Child
                thumb = panel.Children[0]
                title = panel.Children[1]
                folder = panel.Children[2]
            except Exception:
                self.first_error = u"取不到卡片子元素"
                return card

        if title is not None:
            title.Text = item["name"]
        if folder is not None:
            folder.Text = item["folder"]

        card.Tag = item
        card.Opacity = 0.62
        card.MouseLeftButtonUp += self._on_card_click

        thumb_path = thumb_for(item["path"])
        if not thumb_path:
            self.thumb_none += 1
            return card

        source = make_bitmap(thumb_path)
        if source is None:
            self.thumb_err += 1
            if not self.first_error:
                self.first_error = u"BitmapImage 失败"
            return card
        try:
            thumb.Source = source
            self.thumb_ok += 1
        except Exception:
            self.thumb_err += 1
            if not self.first_error:
                self.first_error = traceback.format_exc().splitlines()[-1]
        return card

    def _on_card_click(self, sender, args):
        for card in self.cards.values():
            try:
                card.Opacity = 0.62
            except Exception:
                pass
        try:
            sender.Opacity = 1.0
        except Exception:
            pass
        self.selected = sender.Tag
        if self.selected:
            self.status.Text = u"已选：{0}".format(self.selected["name"])

    # ---------- 事件 ----------
    def _on_search(self, sender, args):
        self._render()

    def _on_refresh(self, sender, args):
        self.selected = None
        self._reload()

    def _on_more(self, sender, args):
        self.limit += self.PAGE
        shown = self._matches()
        start = len(self.cards)
        self.queue.extend(shown[start:self.limit])
        self.total = len(shown)
        if self.queue:
            self.timer.Start()
        self._update_status()

    def _on_add_lib(self, sender, args):
        path = forms.pick_folder()
        if not path:
            return
        libs = load_config()
        if path not in libs:
            libs.append(path)
            save_config(libs)
        self._reload()

    def _on_remove_lib(self, sender, args):
        if not self.selected:
            forms.alert(u"先在网格里点选一个族，再移除它所在的库目录。")
            return
        libs = load_config()
        target = self.selected["path"]
        remaining = [p for p in libs if not target.startswith(p)]
        if len(remaining) == len(libs):
            forms.alert(u"没找到该族所属的库目录。")
            return
        save_config(remaining)
        self.selected = None
        self._reload()

    def _on_load(self, sender, args):
        if not self.selected:
            forms.alert(u"先点选一个族。")
            return
        item = self.selected
        try:
            with revit.Transaction(u"载入族"):
                ok = doc.LoadFamily(item["path"])
            if ok:
                self.status.Text = u"已载入：{0}".format(item["name"])
            else:
                self.status.Text = u"未载入（可能项目里已有同名族）"
        except Exception:
            detail = traceback.format_exc()
            logger.error(detail)
            output.print_md(u"```\n{0}\n```".format(detail))
            forms.alert(u"载入失败，详见输出窗口。", title=u"族管理器")

    def _on_open(self, sender, args):
        if not self.selected:
            forms.alert(u"先点选一个族。")
            return
        try:
            uidoc.Application.OpenAndActivateDocument(self.selected["path"])
        except Exception:
            detail = traceback.format_exc()
            logger.error(detail)
            forms.alert(u"打开失败：\n{0}".format(detail), title=u"族管理器")

    def _on_reveal(self, sender, args):
        if not self.selected:
            return
        try:
            os.startfile(os.path.dirname(self.selected["path"]))
        except Exception:
            pass


def main():
    if doc is None:
        forms.alert(u"请先打开一个项目。", exitscript=True)
    window = FamilyManagerWindow()
    window.ShowDialog()


try:
    main()
except Exception:
    output.print_md(u"脚本出错：\n```\n{0}\n```".format(traceback.format_exc()))
    forms.alert(u"脚本出错，详见输出窗口。", title=u"族管理器")
