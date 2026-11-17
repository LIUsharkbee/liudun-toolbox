# -*- coding: utf-8 -*-
"""导入 DWG

多选 DWG → 按固定参数（毫米、原点定位、保留颜色、仅可见图层、近似水平垂直纠正）
导入到当前活动视图，并自动加视图级图形覆盖（半调 + 黑色投影/剖切线）作为底图。
整个批量记为一步「导入DWG」，可一次撤销。
"""

import os
import io
import traceback

import clr

from pyrevit import revit, forms, script

from Autodesk.Revit.DB import (
    FilteredElementCollector,
    ImportInstance,
    DWGImportOptions,
    ImportColorMode,
    ImportPlacement,
    ImportUnit,
    OverrideGraphicSettings,
    ViewType,
    ElementId,
    Transaction,
    Color,
    BuiltInParameter,
    ModelPathUtils,
)

from Autodesk.Revit.UI import FileOpenDialog, ItemSelectionDialogResult

LOG_PATH = r"C:\Users\cyl\Documents\deepseek-harness\default-workspace\_导入DWG日志.txt"

PLAN_TYPES = set()
for _name in ("FloorPlan", "CeilingPlan", "EngineeringPlan", "AreaPlan"):
    try:
        PLAN_TYPES.add(getattr(ViewType, _name))
    except Exception:
        pass


def desktop_path():
    try:
        from System import Environment
        return Environment.GetFolderPath(Environment.SpecialFolder.Desktop)
    except Exception:
        return u""


def log(text):
    try:
        with io.open(LOG_PATH, "a", encoding="utf-8") as fp:
            fp.write(u"{0}\n".format(text))
    except Exception:
        pass


def is_yes(value):
    return value in (True, u"Yes", u"yes", u"是", 6)


def instance_name(element):
    for getter in ("Name", "get_Name"):
        try:
            value = getattr(element, getter)
            value = value() if callable(value) else value
            if value:
                return u"%s" % value
        except Exception:
            pass
    try:
        param = element.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM)
        if param is not None and param.HasValue:
            return param.AsString() or u""
    except Exception:
        pass
    return u""


def base_name(path):
    name = os.path.basename(path)
    if name.lower().endswith(".dwg"):
        name = name[:-4]
    return name.lower()


def imports_in_view(doc, view):
    result = []
    try:
        collector = FilteredElementCollector(doc, view.Id).OfClass(ImportInstance)
        for element in collector:
            try:
                if element.IsLinked:
                    continue
                result.append(element)
            except Exception:
                pass
    except Exception:
        log(traceback.format_exc())
    return result


def find_existing(doc, view, path):
    target = base_name(path)
    for element in imports_in_view(doc, view):
        name = instance_name(element)
        if not name:
            continue
        if base_name(name) == target:
            return element
    return None


def make_options(view):
    options = DWGImportOptions()
    options.ColorMode = ImportColorMode.Preserved
    options.Placement = ImportPlacement.Origin
    options.Unit = ImportUnit.Millimeter
    options.VisibleLayersOnly = True
    options.AutoCorrectAlmostVHLines = True
    options.OrientToView = False
    try:
        is_plan = view.ViewType in PLAN_TYPES
        options.ThisViewOnly = bool(is_plan)
    except Exception:
        pass
    return options


def apply_override(view, element_id):
    settings = OverrideGraphicSettings()
    settings.Halftone = True
    settings.ProjectionLineColor = Color(0, 0, 0)
    settings.CutLineColor = Color(0, 0, 0)
    view.SetElementOverrides(element_id, settings)



PICK_XAML = u"""<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="选择要导入的 DWG" Height="560" Width="760"
        WindowStartupLocation="CenterScreen">
  <StackPanel Margin="16">
    <TextBlock Text="文件夹" FontWeight="Bold"/>
    <Grid Margin="0,6,0,0">
      <Grid.ColumnDefinitions>
        <ColumnDefinition Width="1*"/>
        <ColumnDefinition Width="8"/>
        <ColumnDefinition Width="Auto"/>
        <ColumnDefinition Width="8"/>
        <ColumnDefinition Width="Auto"/>
      </Grid.ColumnDefinitions>
      <TextBox Grid.Column="0" x:Name="folderBox" Height="28"/>
      <Button Grid.Column="2" x:Name="browseBtn" Content="浏览…" Width="82" Height="28"/>
      <Button Grid.Column="4" x:Name="refreshBtn" Content="刷新" Width="72" Height="28"/>
    </Grid>
    <TextBlock Margin="0,10,0,4" Text="DWG 文件（Ctrl / Shift 多选）" FontWeight="Bold"/>
    <ListBox x:Name="fileList" Height="330" SelectionMode="Extended"/>
    <TextBlock x:Name="countText" Margin="0,6,0,0" Foreground="#FF7D8CA9"/>
    <TextBlock Margin="0,6,0,0" TextWrapping="Wrap" Foreground="#FF7D8CA9"
               Text="导入参数固定：毫米、原点定位、不随视图旋转、保留颜色、仅可见图层、近似水平垂直纠正；导入后自动半调+黑线。"/>
    <StackPanel Orientation="Horizontal" Margin="0,12,0,0">
      <Button x:Name="okBtn" Content="导入选中文件" Width="140" Height="34" FontWeight="Bold"/>
      <Button x:Name="cancelBtn" Content="取消" Width="90" Height="34" Margin="10,0,0,0"/>
    </StackPanel>
  </StackPanel>
</Window>"""


class PickFilesWindow(forms.WPFWindow):
    def __init__(self, folder):
        forms.WPFWindow.__init__(self, PICK_XAML, literal_string=True)
        self.folder_box = self.FindName("folderBox")
        self.file_list = self.FindName("fileList")
        self.count_text = self.FindName("countText")
        self.state = {"files": []}
        base = folder or desktop_path()
        self.folder_box.Text = base or u""
        self.FindName("browseBtn").Click += self._on_browse
        self.FindName("refreshBtn").Click += self._on_refresh
        self.FindName("okBtn").Click += self._on_ok
        self.FindName("cancelBtn").Click += lambda s, e: self.Close()
        self._refresh()

    def _browse(self):
        folder = None
        try:
            folder = forms.pick_folder(title=u"选择 DWG 所在文件夹")
        except Exception:
            log(traceback.format_exc())
            folder = None
        if folder:
            return folder
        # 系统对话框被策略拦掉时，退到 Revit 自己的对话框（只取目录名）
        try:
            dialog = FileOpenDialog(u"Select any file to locate folder")
            dialog.Filter = "All files (*.*)|*.*"
            if dialog.Show() == ItemSelectionDialogResult.Confirmed:
                path = ModelPathUtils.ConvertModelPathToUserVisiblePath(dialog.GetSelectedModelPath())
                return os.path.dirname(path)
        except Exception:
            log(traceback.format_exc())
        return None

    def _on_browse(self, sender, args):
        folder = self._browse()
        if folder:
            self.folder_box.Text = folder
            self._refresh()

    def _files(self):
        folder = u"%s" % (self.folder_box.Text or u"")
        folder = folder.strip().strip(u'"')
        if not folder or not os.path.isdir(folder):
            return []
        result = []
        try:
            for name in os.listdir(folder):
                if name.lower().endswith(".dwg"):
                    result.append(os.path.join(folder, name))
        except Exception:
            return []
        result.sort()
        return result

    def _on_refresh(self, sender, args):
        self._refresh()

    def _refresh(self):
        self.file_list.Items.Clear()
        files = self._files()
        for path in files:
            self.file_list.Items.Add(os.path.basename(path))
        self.count_text.Text = u"文件夹里 {0} 个 DWG；列表里没看到就换文件夹再刷新。".format(len(files))

    def _on_ok(self, sender, args):
        files = self._files()
        chosen = set()
        for item in self.file_list.SelectedItems:
            chosen.add(u"%s" % item)
        if not chosen:
            forms.alert(u"请先在列表里选中要导入的 DWG（可 Ctrl / Shift 多选）。", title=u"导入DWG")
            return
        self.state["files"] = [path for path in files if os.path.basename(path) in chosen]
        self.Close()


def main():
    doc = revit.doc
    if doc is None:
        forms.alert(u"请先打开一个项目文档。", exitscript=True)

    view = doc.ActiveView
    if view is None:
        forms.alert(u"请先打开一个图形视图（平面 / 三维 / 剖面）。", exitscript=True)
    try:
        if view.ViewType in (ViewType.ProjectBrowser, ViewType.SystemBrowser):
            forms.alert(u"当前视图不能导入 DWG，请切换到平面或三维视图。", exitscript=True)
    except Exception:
        pass

    picker = PickFilesWindow(u"")
    picker.ShowDialog()
    files = picker.state.get("files") or []
    if not files:
        return

    log(u"===== 导入 DWG：{0} 个文件，视图 {1} =====".format(len(files), view.Name))

    done = []
    skipped = []
    failed = []
    deleted = 0

    transaction = Transaction(doc, u"导入DWG")
    transaction.Start()
    try:
        for path in files:
            name = os.path.basename(path)
            try:
                existing = find_existing(doc, view, path)
                if existing is not None:
                    question = u"当前视图已导入图纸：{0}\n是否删除重新导入？".format(name)
                    if not is_yes(forms.alert(question, title=u"导入DWG", yes=True, no=True)):
                        skipped.append(name)
                        continue
                    doc.Delete(existing.Id)
                    deleted += 1

                options = make_options(view)
                reference = clr.Reference[ElementId]()
                doc.Import(path, options, view, reference)
                imported_id = reference.Value
                if imported_id is None or imported_id == ElementId.InvalidElementId:
                    failed.append((name, u"导入没有返回图元"))
                    continue
                apply_override(view, imported_id)
                instance = doc.GetElement(imported_id)
                if instance is not None:
                    try:
                        instance.Pinned = False
                    except Exception:
                        log(u"解锁失败：{0}".format(name))
                done.append(name)
                log(u"OK {0} -> {1}".format(name, imported_id.IntegerValue))
            except Exception:
                detail = traceback.format_exc()
                log(u"FAIL {0}\n{1}".format(name, detail))
                failed.append((name, detail.strip().splitlines()[-1]))
        transaction.Commit()
    except Exception:
        transaction.RollBack()
        log(traceback.format_exc())
        forms.alert(u"导入 DWG 失败，已回滚：\n{0}".format(traceback.format_exc()[-1200:]),
                    title=u"导入DWG")
        return

    lines = [u"**导入 DWG 完成**"]
    lines.append(u"")
    lines.append(u"- 视图：`{0}`".format(view.Name))
    lines.append(u"- 成功 {0} 个，跳过 {1} 个，失败 {2} 个".format(len(done), len(skipped), len(failed)))
    if deleted:
        lines.append(u"- 覆盖重导 {0} 个旧图纸".format(deleted))
    if skipped:
        lines.append(u"- 跳过：{0}".format(u"、".join(skipped)))
    if failed:
        lines.append(u"- 失败：")
        for name, msg in failed[:8]:
            lines.append(u"    - {0}：{1}".format(name, msg))
    lines.append(u"- 日志：`{0}`".format(LOG_PATH))

    output = script.get_output()
    output.print_md(u"\n".join(lines))

    forms.alert(u"导入完成：成功 {0} 个，跳过 {1} 个，失败 {2} 个。".format(
        len(done), len(skipped), len(failed)), title=u"导入DWG")


try:
    main()
except Exception:
    detail = traceback.format_exc()
    log(detail)
    forms.alert(u"导入 DWG 出错：\n{0}".format(detail[-1200:]), title=u"导入DWG")
