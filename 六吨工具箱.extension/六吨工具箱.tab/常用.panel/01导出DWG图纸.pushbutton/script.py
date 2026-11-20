# -*- coding: utf-8 -*-
"""导出 DWG 图纸

批量把项目里的图纸（以及未布图的平面/剖面/立面/详图/三维视图）导出为 DWG，
可一次性合并成一张 DWG；配置写入输出目录的 DwgExportConfig.json，下次自动载入。
纯导出，不改模型，也不写任何图元。
"""

import os
import io
import json
import traceback

import clr

from pyrevit import revit, forms, script

from Autodesk.Revit.DB import (
    FilteredElementCollector,
    View,
    ViewSheet,
    Viewport,
    ViewType,
    ElementId,
    ACADVersion,
    ExportColorMode,
    ExportUnit,
    TextTreatment,
    DWGExportOptions,
)

from System.Collections.Generic import List as DotNetList
from System.Windows import Window, Visibility
from System.Windows.Controls import ListBox, SelectionMode

LOG_PATH = r"C:\Users\cyl\Documents\deepseek-harness\default-workspace\_导出DWG日志.txt"

FT_TO_MM = 304.8
CONFIG_NAME = "DwgExportConfig.json"

VERSION_CHOICES = [
    (u"R2018（推荐）", ACADVersion.R2018),
    (u"R2013", ACADVersion.R2013),
    (u"R2010", ACADVersion.R2010),
    (u"R2007", ACADVersion.R2007),
    (u"Default（跟随导出设置）", ACADVersion.Default),
]

COLOR_CHOICES = [
    (u"索引色 (IndexColors)", ExportColorMode.IndexColors),
    (u"真彩色 (TrueColor)", ExportColorMode.TrueColor),
    (u"真彩色按视图 (TrueColorPerView)", ExportColorMode.TrueColorPerView),
]

UNIT_CHOICES = [
    (u"毫米 (Millimeter)", ExportUnit.Millimeter),
    (u"厘米 (Centimeter)", ExportUnit.Centimeter),
    (u"米 (Meter)", ExportUnit.Meter),
    (u"英寸 (Inch)", ExportUnit.Inch),
    (u"英尺 (Foot)", ExportUnit.Foot),
    (u"Default", ExportUnit.Default),
]

VIEW_TYPE_CHOICES = [
    (u"全部类型", None),
    (u"平面 (FloorPlan)", ViewType.FloorPlan),
    (u"结构平面 (EngineeringPlan)", ViewType.EngineeringPlan),
    (u"天花平面 (CeilingPlan)", ViewType.CeilingPlan),
    (u"剖面 (Section)", ViewType.Section),
    (u"立面 (Elevation)", ViewType.Elevation),
    (u"详图 (Detail)", ViewType.Detail),
    (u"三维 (ThreeD)", ViewType.ThreeD),
    (u"绘图视图 (DraftingView)", ViewType.DraftingView),
    (u"面积平面 (AreaPlan)", ViewType.AreaPlan),
]


# ---------------------------------------------------------------- 工具函数
def log(text):
    try:
        with io.open(LOG_PATH, "a", encoding="utf-8") as fp:
            fp.write(u"{0}\n".format(text))
    except Exception:
        pass


def text_of(obj):
    try:
        name = obj.Name
        if name:
            return name
    except Exception:
        pass
    try:
        return obj.get_Name()
    except Exception:
        return u"<未命名>"


def sheet_label(sheet):
    try:
        return u"{0} - {1}".format(sheet.SheetNumber, sheet.Name)
    except Exception:
        return text_of(sheet)


def sheet_view_ids(sheet):
    ids = []
    try:
        for vp_id in sheet.GetAllViewports():
            vp = sheet.Document.GetElement(vp_id)
            if vp is None:
                continue
            try:
                ids.append(vp.ViewId)
            except Exception:
                pass
    except Exception:
        pass
    return ids


def all_sheets(doc):
    sheets = []
    for element in FilteredElementCollector(doc).OfClass(ViewSheet):
        try:
            if element.IsTemplate:
                continue
            sheets.append(element)
        except Exception:
            pass
    sheets.sort(key=lambda s: (u"%s" % (s.SheetNumber or u""), u"%s" % (s.Name or u"")))
    return sheets


def all_views(doc):
    views = []
    skip_types = set()
    for name in ("DrawingSheet", "Internal", "ProjectBrowser", "SystemBrowser",
                 "Legend", "Schedule", "ColumnSchedule", "PanelSchedule",
                 "SheetCollection", "Report"):
        try:
            skip_types.add(getattr(ViewType, name))
        except Exception:
            pass
    for element in FilteredElementCollector(doc).OfClass(View):
        try:
            if element.IsTemplate:
                continue
            if element.ViewType in skip_types:
                continue
            views.append(element)
        except Exception:
            pass
    views.sort(key=lambda v: (u"%s" % v.ViewType, u"%s" % (v.Name or u"")))
    return views


def owner_sheet_map(doc, sheets):
    mapping = {}
    for sheet in sheets:
        label = sheet_label(sheet)
        for view_id in sheet_view_ids(sheet):
            mapping[view_id.IntegerValue] = label
    return mapping


def safe_filename(name):
    bad = u'\\/:*?"<>|'
    result = name
    for ch in bad:
        result = result.replace(ch, u"_")
    return result.strip() or u"未命名"


# ---------------------------------------------------------------- 配置读写
def default_config():
    return {
        "version_index": 0,
        "color_index": 0,
        "unit_index": 0,
        "prefix": u"",
        "hide_scope_box": True,
        "hide_ref_plane": True,
        "hide_unref_tags": True,
        "shared_coord": False,
        "merge": True,
        "merge_name": u"合并图纸",
        "sheets": [],
        "views": [],
    }


def load_config(folder):
    path = os.path.join(folder, CONFIG_NAME)
    if not os.path.isfile(path):
        return default_config()
    try:
        with io.open(path, "r", encoding="utf-8") as fp:
            data = json.load(fp)
        base = default_config()
        base.update(data)
        return base
    except Exception:
        log(u"读取配置失败：{0}".format(traceback.format_exc()))
        return default_config()


def save_config(folder, config):
    path = os.path.join(folder, CONFIG_NAME)
    try:
        with io.open(path, "w", encoding="utf-8") as fp:
            fp.write(json.dumps(config, ensure_ascii=False, indent=2))
    except Exception:
        log(u"写配置失败：{0}".format(traceback.format_exc()))


def build_options(config):
    opts = DWGExportOptions()
    try:
        opts.FileVersion = VERSION_CHOICES[config["version_index"]][1]
    except Exception:
        opts.FileVersion = ACADVersion.R2018
    try:
        opts.Colors = COLOR_CHOICES[config["color_index"]][1]
    except Exception:
        pass
    try:
        opts.TargetUnit = UNIT_CHOICES[config["unit_index"]][1]
    except Exception:
        pass
    try:
        opts.TextTreatment = TextTreatment.Exact
    except Exception:
        pass
    for key, prop in (("hide_scope_box", "HideScopeBox"),
                      ("hide_ref_plane", "HideReferencePlane"),
                      ("hide_unref_tags", "HideUnreferenceViewTags"),
                      ("shared_coord", "SharedCoords")):
        try:
            setattr(opts, prop, bool(config.get(key)))
        except Exception:
            pass
    return opts


# ---------------------------------------------------------------- 导出
def unique_target(folder, name):
    candidate = os.path.join(folder, name + u".dwg")
    if not os.path.isfile(candidate):
        return candidate
    index = 2
    while True:
        candidate = os.path.join(folder, u"{0}({1}).dwg".format(name, index))
        if not os.path.isfile(candidate):
            return candidate
        index += 1


def export_one(doc, folder, base_name, ids, opts):
    """导出单个对象；返回 (ok, path, message)。"""
    name = safe_filename(base_name)
    target = unique_target(folder, name)
    try:
        collection = DotNetList[ElementId]()
        for item in ids:
            collection.Add(item)
        doc.Export(folder, os.path.basename(target)[:-4], collection, opts)
    except Exception:
        msg = traceback.format_exc()
        log(u"导出失败 {0}：{1}".format(base_name, msg))
        return False, None, msg.strip().splitlines()[-1]
    if not os.path.isfile(target):
        # Revit 可能按「前缀-图号-图名」命名，找最近文件兜底
        try:
            newest = None
            for fname in os.listdir(folder):
                if not fname.lower().endswith(".dwg"):
                    continue
                if name not in fname:
                    continue
                full = os.path.join(folder, fname)
                if newest is None or os.path.getmtime(full) > os.path.getmtime(newest):
                    newest = full
            if newest:
                return True, newest, u""
        except Exception:
            pass
        return False, None, u"没有生成 DWG 文件"
    return True, target, u""


def export_all(doc, folder, sheets, views, opts, config):
    """返回 (已导出列表, 失败列表)。"""
    done = []
    failed = []
    prefix = u"%s" % (config.get("prefix") or u"")

    if config.get("merge"):
        merge_name = config.get("merge_name") or u"合并图纸"
        opts.MergedViews = True
        # 图纸与视图分成两批合并：类型混在一起 Revit 会拒绝合并
        if sheets:
            ids = [sheet.Id for sheet in sheets]
            ok, path, msg = export_one(doc, folder, prefix + merge_name, ids, opts)
            if ok:
                done.append((merge_name + u"（图纸）", path))
            else:
                failed.append((merge_name + u"（图纸）", msg))
        if views:
            ids = [view.Id for view in views]
            view_name = merge_name + u"_视图"
            ok, path, msg = export_one(doc, folder, prefix + view_name, ids, opts)
            if ok:
                done.append((view_name, path))
            else:
                failed.append((view_name, msg))
        return done, failed

    opts.MergedViews = False
    for sheet in sheets:
        label = sheet_label(sheet)
        if not sheet_view_ids(sheet):
            failed.append((label, u"图纸上没有放置视图"))
            continue
        ok, path, msg = export_one(doc, folder, prefix + label, [sheet.Id], opts)
        if ok:
            done.append((label, path))
        else:
            failed.append((label, msg))
    for view in views:
        label = text_of(view)
        ok, path, msg = export_one(doc, folder, prefix + label, [view.Id], opts)
        if ok:
            done.append((label, path))
        else:
            failed.append((label, msg))
    return done, failed


# ---------------------------------------------------------------- 窗口
XAML = u"""<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="导出 DWG 图纸" Height="760" Width="880"
        WindowStartupLocation="CenterScreen">
  <ScrollViewer VerticalScrollBarVisibility="Auto">
    <StackPanel Margin="16">
      <TextBlock x:Name="folderText" Text="输出目录：" FontWeight="Bold"/>
      <TextBlock x:Name="hintText" Margin="0,6,0,0" TextWrapping="Wrap"
                 Text="图纸默认全选；视图默认不选（项目里视图通常很多）。勾选后点「开始导出」。"/>

      <Grid Margin="0,12,0,0">
        <Grid.ColumnDefinitions>
          <ColumnDefinition Width="1*"/>
          <ColumnDefinition Width="12"/>
          <ColumnDefinition Width="1*"/>
        </Grid.ColumnDefinitions>

        <StackPanel Grid.Column="0">
          <TextBlock Text="图纸（先出）" FontWeight="Bold"/>
          <TextBox x:Name="sheetSearch" Margin="0,6,0,4" Height="26"/>
          <StackPanel Orientation="Horizontal">
            <Button x:Name="sheetAll" Content="全选" Height="28" Width="64" Margin="0,0,6,0"/>
            <Button x:Name="sheetNone" Content="全不选" Height="28" Width="72" Margin="0,0,6,0"/>
            <Button x:Name="sheetInvert" Content="反选" Height="28" Width="64"/>
          </StackPanel>
          <ListBox x:Name="sheetList" Height="250" Margin="0,6,0,0"
                   SelectionMode="Extended"/>
          <TextBlock x:Name="sheetCount" Margin="0,4,0,0"/>
        </StackPanel>

        <StackPanel Grid.Column="2">
          <TextBlock Text="视图（后出）" FontWeight="Bold"/>
          <ComboBox x:Name="viewType" Margin="0,6,0,4" Height="26"/>
          <TextBox x:Name="viewSearch" Height="26" Margin="0,0,0,4"/>
          <StackPanel Orientation="Horizontal">
            <Button x:Name="viewAll" Content="全选" Height="28" Width="64" Margin="0,0,6,0"/>
            <Button x:Name="viewNone" Content="全不选" Height="28" Width="72" Margin="0,0,6,0"/>
            <Button x:Name="viewInvert" Content="反选" Height="28" Width="64"/>
          </StackPanel>
          <ListBox x:Name="viewList" Height="250" Margin="0,6,0,0"
                   SelectionMode="Extended"/>
          <TextBlock x:Name="viewCount" Margin="0,4,0,0"/>
        </StackPanel>
      </Grid>

      <TextBlock Text="常规" FontWeight="Bold" Margin="0,14,0,6"/>
      <Grid>
        <Grid.ColumnDefinitions>
          <ColumnDefinition Width="Auto"/>
          <ColumnDefinition Width="220"/>
          <ColumnDefinition Width="24"/>
          <ColumnDefinition Width="Auto"/>
          <ColumnDefinition Width="220"/>
        </Grid.ColumnDefinitions>
        <Grid.RowDefinitions>
          <RowDefinition Height="30"/>
          <RowDefinition Height="30"/>
          <RowDefinition Height="30"/>
          <RowDefinition Height="30"/>
        </Grid.RowDefinitions>

        <TextBlock Grid.Row="0" Grid.Column="0" Text="DWG 版本" VerticalAlignment="Center"/>
        <ComboBox Grid.Row="0" Grid.Column="1" x:Name="versionBox" Height="26" Margin="6,0,0,0"/>
        <TextBlock Grid.Row="0" Grid.Column="3" Text="颜色模式" VerticalAlignment="Center"/>
        <ComboBox Grid.Row="0" Grid.Column="4" x:Name="colorBox" Height="26" Margin="6,0,0,0"/>

        <TextBlock Grid.Row="1" Grid.Column="0" Text="目标单位" VerticalAlignment="Center"/>
        <ComboBox Grid.Row="1" Grid.Column="1" x:Name="unitBox" Height="26" Margin="6,0,0,0"/>
        <TextBlock Grid.Row="1" Grid.Column="3" Text="文件名前缀" VerticalAlignment="Center"/>
        <TextBox Grid.Row="1" Grid.Column="4" x:Name="prefixBox" Height="26" Margin="6,0,0,0"/>

        <CheckBox Grid.Row="2" Grid.Column="0" Grid.ColumnSpan="2" x:Name="hideScope" Content="隐藏范围框"/>
        <CheckBox Grid.Row="2" Grid.Column="3" Grid.ColumnSpan="2" x:Name="hideRef" Content="隐藏参照平面"/>

        <CheckBox Grid.Row="3" Grid.Column="0" Grid.ColumnSpan="2" x:Name="hideTags" Content="隐藏未参照的视图标记"/>
        <CheckBox Grid.Row="3" Grid.Column="3" Grid.ColumnSpan="2" x:Name="sharedCoord" Content="使用共享坐标"/>
      </Grid>

      <StackPanel Orientation="Horizontal" Margin="0,10,0,0">
        <CheckBox x:Name="mergeBox" Content="导出后自动合并成一张 DWG" VerticalAlignment="Center"/>
        <TextBlock Text="合并文件名" Margin="18,0,6,0" VerticalAlignment="Center"/>
        <TextBox x:Name="mergeName" Width="220" Height="26"/>
      </StackPanel>

      <TextBlock x:Name="statusText" Margin="0,12,0,0" TextWrapping="Wrap"
                 Foreground="#FF7D8CA9" Text="就绪。"/>

      <StackPanel Orientation="Horizontal" Margin="0,14,0,0">
        <Button x:Name="okBtn" Content="开始导出" Height="34" Width="130" FontWeight="Bold"/>
        <Button x:Name="closeBtn" Content="取消" Height="34" Width="90" Margin="10,0,0,0"/>
      </StackPanel>
    </StackPanel>
  </ScrollViewer>
</Window>"""


class ExportWindow(forms.WPFWindow):
    def __init__(self, doc, sheets, views, owners, config):
        forms.WPFWindow.__init__(self, XAML, literal_string=True)
        self.doc = doc
        self.all_sheets = sheets
        self.all_views = views
        self.owners = owners
        self.config = config
        self.folder = u""
        self.state = {"ok": False}

        self.sheet_list = self.FindName("sheetList")
        self.view_list = self.FindName("viewList")
        self.sheet_search = self.FindName("sheetSearch")
        self.view_search = self.FindName("viewSearch")
        self.view_type = self.FindName("viewType")
        self.sheet_count = self.FindName("sheetCount")
        self.view_count = self.FindName("viewCount")
        self.status_text = self.FindName("statusText")
        self.folder_text = self.FindName("folderText")

        for label, _ in VERSION_CHOICES:
            self.FindName("versionBox").Items.Add(label)
        for label, _ in COLOR_CHOICES:
            self.FindName("colorBox").Items.Add(label)
        for label, _ in UNIT_CHOICES:
            self.FindName("unitBox").Items.Add(label)
        for label, _ in VIEW_TYPE_CHOICES:
            self.view_type.Items.Add(label)

        self.FindName("versionBox").SelectedIndex = config.get("version_index", 0)
        self.FindName("colorBox").SelectedIndex = config.get("color_index", 0)
        self.FindName("unitBox").SelectedIndex = config.get("unit_index", 0)
        self.view_type.SelectedIndex = 0
        self.FindName("prefixBox").Text = config.get("prefix") or u""
        self.FindName("hideScope").IsChecked = bool(config.get("hide_scope_box", True))
        self.FindName("hideRef").IsChecked = bool(config.get("hide_ref_plane", True))
        self.FindName("hideTags").IsChecked = bool(config.get("hide_unref_tags", True))
        self.FindName("sharedCoord").IsChecked = bool(config.get("shared_coord", False))
        self.FindName("mergeBox").IsChecked = bool(config.get("merge", True))
        self.FindName("mergeName").Text = config.get("merge_name") or u"合并图纸"

        self.FindName("sheetSearch").TextChanged += self._on_sheet_filter
        self.FindName("viewSearch").TextChanged += self._on_view_filter
        self.view_type.SelectionChanged += self._on_view_filter
        self.FindName("sheetAll").Click += lambda s, e: self._select_all(self.sheet_list)
        self.FindName("sheetNone").Click += lambda s, e: self.sheet_list.SelectedItems.Clear()
        self.FindName("sheetInvert").Click += lambda s, e: self._invert(self.sheet_list)
        self.FindName("viewAll").Click += lambda s, e: self._select_all(self.view_list)
        self.FindName("viewNone").Click += lambda s, e: self.view_list.SelectedItems.Clear()
        self.FindName("viewInvert").Click += lambda s, e: self._invert(self.view_list)
        self.FindName("okBtn").Click += self._on_ok
        self.FindName("closeBtn").Click += self._on_close

        self._refresh()

    # ---------------- 列表
    def _on_sheet_filter(self, sender, args):
        self._refresh_sheets()

    def _on_view_filter(self, sender, args):
        self._refresh_views()

    def _refresh(self):
        self._refresh_sheets()
        self._refresh_views()

    def _refresh_sheets(self):
        keyword = u"%s" % (self.sheet_search.Text or u"")
        self.sheet_list.Items.Clear()
        count = 0
        for sheet in self.all_sheets:
            label = sheet_label(sheet)
            if keyword and keyword not in label:
                continue
            views = sheet_view_ids(sheet)
            item = label
            if not views:
                item = label + u"    （无视图，不会出文件）"
            self.sheet_list.Items.Add(item)
            count += 1
        self.sheet_count.Text = u"图纸 N = {0}".format(count)
        for index in range(self.sheet_list.Items.Count):
            self.sheet_list.SelectedItems.Add(self.sheet_list.Items[index])

    def _refresh_views(self):
        keyword = u"%s" % (self.view_search.Text or u"")
        type_index = int(self.view_type.SelectedIndex or 0)
        wanted = VIEW_TYPE_CHOICES[type_index][1]
        self.view_list.Items.Clear()
        count = 0
        for view in self.all_views:
            try:
                if wanted is not None and view.ViewType != wanted:
                    continue
            except Exception:
                pass
            label = text_of(view)
            if keyword and keyword not in label:
                continue
            owner = self.owners.get(view.Id.IntegerValue)
            text = u"{0}    {1}".format(view.ViewType, label)
            if owner:
                text += u"    [{0}]".format(owner)
            self.view_list.Items.Add(text)
            count += 1
        self.view_count.Text = u"视图 N = {0}".format(count)

    def _select_all(self, widget):
        for index in range(widget.Items.Count):
            widget.SelectedItems.Add(widget.Items[index])

    def _invert(self, widget):
        chosen = set()
        for item in widget.SelectedItems:
            chosen.add(item)
        widget.SelectedItems.Clear()
        for index in range(widget.Items.Count):
            item = widget.Items[index]
            if item not in chosen:
                widget.SelectedItems.Add(item)

    # ---------------- 结果
    def _picked_sheets(self):
        chosen = set()
        for item in self.sheet_list.SelectedItems:
            chosen.add(u"%s" % item)
        result = []
        for sheet in self.all_sheets:
            label = sheet_label(sheet)
            if label in chosen or (label + u"    （无视图，不会出文件）") in chosen:
                result.append(sheet)
        return result

    def _picked_views(self):
        chosen = set()
        for item in self.view_list.SelectedItems:
            chosen.add(u"%s" % item)
        result = []
        for view in self.all_views:
            label = text_of(view)
            owner = self.owners.get(view.Id.IntegerValue)
            text = u"{0}    {1}".format(view.ViewType, label)
            if owner:
                text += u"    [{0}]".format(owner)
            if text in chosen:
                result.append(view)
        return result

    def _on_close(self, sender, args):
        self.Close()

    def _on_ok(self, sender, args):
        sheets = self._picked_sheets()
        views = self._picked_views()
        if not sheets and not views:
            forms.alert(u"请至少勾选一个要导出的图纸或视图。", title=u"导出 DWG")
            return
        merge = bool(self.FindName("mergeBox").IsChecked)
        merge_name = u"%s" % (self.FindName("mergeName").Text or u"")
        if merge and not merge_name.strip():
            forms.alert(u"请填写合并输出的文件名（或取消勾选「导出后自动合并」）。", title=u"导出 DWG")
            return
        self.state["ok"] = True
        self.state["sheets"] = sheets
        self.state["views"] = views
        self.state["config"] = {
            "version_index": int(self.FindName("versionBox").SelectedIndex),
            "color_index": int(self.FindName("colorBox").SelectedIndex),
            "unit_index": int(self.FindName("unitBox").SelectedIndex),
            "prefix": u"%s" % (self.FindName("prefixBox").Text or u""),
            "hide_scope_box": bool(self.FindName("hideScope").IsChecked),
            "hide_ref_plane": bool(self.FindName("hideRef").IsChecked),
            "hide_unref_tags": bool(self.FindName("hideTags").IsChecked),
            "shared_coord": bool(self.FindName("sharedCoord").IsChecked),
            "merge": merge,
            "merge_name": merge_name.strip() or u"合并图纸",
        }
        self.Close()


# ---------------------------------------------------------------- 主流程
def main():
    doc = revit.doc
    if doc is None:
        forms.alert(u"请先打开一个项目文档。", exitscript=True)

    log(u"===== 导出 DWG 开始 {0} =====".format(doc.Title))

    sheets = all_sheets(doc)
    views = all_views(doc)
    if not sheets and not views:
        forms.alert(u"当前文档里既没有图纸，也没有可导出的平面 / 剖面 / 立面 / 详图视图。",
                    title=u"导出 DWG", exitscript=True)

    owners = owner_sheet_map(doc, sheets)

    folder = forms.pick_folder(title=u"选择 DWG 输出目录")
    if not folder:
        return

    config = load_config(folder)
    window = ExportWindow(doc, sheets, views, owners, config)
    window.folder = folder
    window.folder_text.Text = u"输出目录：" + folder
    window.ShowDialog()

    if not window.state.get("ok"):
        return

    config = window.state["config"]
    config["sheets"] = [s.SheetNumber for s in window.state["sheets"]]
    config["views"] = [text_of(v) for v in window.state["views"]]
    save_config(folder, config)

    opts = build_options(config)
    done, failed = export_all(doc, folder, window.state["sheets"], window.state["views"], opts, config)

    lines = []
    lines.append(u"**导出完成**：成功 {0} 个，失败 {1} 个".format(len(done), len(failed)))
    lines.append(u"")
    lines.append(u"输出目录：`{0}`".format(folder))
    if done:
        lines.append(u"")
        lines.append(u"| 对象 | DWG |")
        lines.append(u"| --- | --- |")
        for label, path in done[:30]:
            lines.append(u"| {0} | {1} |".format(label, os.path.basename(path)))
        if len(done) > 30:
            lines.append(u"| … | 其余 {0} 个见日志 |".format(len(done) - 30))
    if failed:
        lines.append(u"")
        lines.append(u"**失败 {0} 个**".format(len(failed)))
        for label, msg in failed[:10]:
            lines.append(u"- {0}：{1}".format(label, msg))
    lines.append(u"")
    lines.append(u"日志：`{0}`".format(LOG_PATH))

    output = script.get_output()
    output.print_md(u"\n".join(lines))
    log(u"\n".join([line.replace("**", "") for line in lines]))

    if done:
        title = u"导出并合并完成" if config.get("merge") else u"导出完成"
        forms.alert(u"{0}：成功 {1} 个，失败 {2} 个。\n目录：{3}".format(
            title, len(done), len(failed), folder), title=u"导出 DWG")
    else:
        forms.alert(u"没有产出任何 DWG —— 请检查图纸上是否放了视图、视图在 Revit 里是否可打印。",
                    title=u"导出失败")


try:
    main()
except Exception:
    detail = traceback.format_exc()
    log(detail)
    forms.alert(u"导出 DWG 出错：\n{0}".format(detail[-1200:]), title=u"导出 DWG")
