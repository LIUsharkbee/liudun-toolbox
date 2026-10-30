# -*- coding: utf-8 -*-
"""性能优化

三个层次：
  1. 诊断：统计拖慢模型的因素（导入 CAD、点云、链接、组、警告、未放置视图…）；
  2. 视图优化：当前视图降细节等级、隐藏重类别（按图元反查类别，不依赖枚举）；
  3. 清理：
     - 清理未使用项（PurgeUnused）；
     - 批量删视图：按视图类型分组，先预览再删，默认只删「没放在图纸上」的。
"""

from __future__ import print_function

import os
import traceback

import clr

clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")

from Autodesk.Revit.DB import (
    FilteredElementCollector,
    Group,
    ImportInstance,
    PointCloudInstance,
    RevitLinkInstance,
    View,
    ViewDetailLevel,
    ViewSheet,
)
from System.Windows.Markup import XamlReader

from pyrevit import forms, revit, script

doc = revit.doc
uidoc = revit.uidoc
output = script.get_output()
logger = script.get_logger()

SKIP_TYPES = ("ProjectBrowser", "SystemBrowser", "Internal", "DrawingSheet")


def safe_call(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def type_name_of(value):
    """枚举成员转字符串，避开 IronPython 的枚举成员访问问题。"""
    text = safe_call(lambda: value.ToString(), None)
    if text:
        return str(text)
    return str(value).split(".")[-1]


def count_of(cls):
    return len(safe_call(lambda: list(FilteredElementCollector(doc).OfClass(cls)), []) or [])


def placed_view_ids():
    ids = set()
    sheets = safe_call(lambda: list(FilteredElementCollector(doc).OfClass(ViewSheet)), []) or []
    for sheet in sheets:
        for view_id in safe_call(lambda s=sheet: s.GetAllPlacedViews(), []) or []:
            value = safe_call(lambda v=view_id: v.IntegerValue, None)
            if value is not None:
                ids.add(value)
    return ids


def all_views():
    views = safe_call(lambda: list(FilteredElementCollector(doc).OfClass(View)), []) or []
    active = safe_call(lambda: doc.ActiveView.Id.IntegerValue, -1)
    result = []
    for view in views:
        if safe_call(lambda v=view: v.IsTemplate, False):
            continue
        kind = type_name_of(safe_call(lambda v=view: v.ViewType, u""))
        if kind in SKIP_TYPES:
            continue
        vid = safe_call(lambda v=view: v.Id.IntegerValue, -1)
        if vid == active:
            continue
        result.append({
            "view": view,
            "id": vid,
            "name": safe_call(lambda v=view: v.Name, u"(无名)"),
            "type": kind,
        })
    return result


# ---------------------------------------------------------------- 诊断

def diag():
    rows = []
    advice = []

    path = safe_call(lambda: doc.PathName, u"")
    size = safe_call(lambda: os.path.getsize(path), 0) if path else 0
    if size:
        rows.append((u"文件大小", u"{0:.1f} MB".format(size / 1024.0 / 1024.0)))

    elements = safe_call(
        lambda: FilteredElementCollector(doc).WhereElementIsNotElementType().GetElementCount(), 0)
    rows.append((u"图元总数", u"{0:,}".format(elements or 0)))
    if (elements or 0) > 100000:
        advice.append(u"图元超过 10 万：建议拆分模型或链接协作，不要在单文件里继续堆。")

    imports = count_of(ImportInstance)
    rows.append((u"导入的 CAD 图元", u"{0:,}".format(imports)))
    if imports:
        advice.append(u"{0} 个导入 CAD：建议改「链接 CAD」或删除；导入体严重拖慢重绘。".format(imports))

    links = count_of(RevitLinkInstance)
    rows.append((u"链接模型实例", u"{0:,}".format(links)))
    if links:
        advice.append(u"链接了 {0} 个模型：不用的在「管理链接」里卸载。".format(links))

    groups = count_of(Group)
    rows.append((u"组", u"{0:,}".format(groups)))
    if groups > 200:
        advice.append(u"组数量偏多（{0}）：组会拖慢编辑，能炸开就炸开。".format(groups))

    clouds = count_of(PointCloudInstance)
    rows.append((u"点云", u"{0:,}".format(clouds)))
    if clouds:
        advice.append(u"有点云：默认关闭显示，需要时再开。")

    views = all_views()
    placed = placed_view_ids()
    orphan = [v for v in views if v["id"] not in placed]
    rows.append((u"视图总数", u"{0:,}".format(len(views))))
    rows.append((u"未放到图纸的视图", u"{0:,}".format(len(orphan))))
    if orphan:
        advice.append(u"有 {0} 个视图没放在图纸上：到「批量删视图」页清掉长期不看的，"
                      u"对打开速度提升最明显。".format(len(orphan)))

    warnings = safe_call(lambda: doc.GetWarnings().Count, 0)
    rows.append((u"警告数量", u"{0:,}".format(warnings or 0)))
    if (warnings or 0) > 200:
        advice.append(u"警告 {0} 条：建议定期清理，警告多会让每次操作都变慢。".format(warnings))

    return rows, advice


# ---------------------------------------------------------------- 视图优化

def _first_element(cls):
    items = safe_call(lambda: list(FilteredElementCollector(doc).OfClass(cls)), []) or []
    return items[0] if items else None


def _hide_category_of(view, cls, label):
    element = _first_element(cls)
    if element is None:
        return None
    category = safe_call(lambda: element.Category, None)
    if category is None:
        return None
    cat_id = safe_call(lambda: category.Id, None)
    if cat_id is None:
        return None
    if safe_call(lambda: view.GetCategoryHidden(cat_id), False):
        return None
    safe_call(lambda: view.SetCategoryHidden(cat_id, True))
    return label


def optimize_view(view, hide_links=False):
    done = []

    detail = safe_call(lambda: view.DetailLevel, None)
    coarse = safe_call(lambda: ViewDetailLevel.Coarse, None)
    if detail is not None and coarse is not None and detail != coarse:
        safe_call(lambda: setattr(view, "DetailLevel", coarse))
        if safe_call(lambda: view.DetailLevel == coarse, False):
            done.append(u"细节等级 → 粗略")

    for cls, label in ((ImportInstance, u"导入的 CAD"),
                       (PointCloudInstance, u"点云")):
        result = _hide_category_of(view, cls, label)
        if result:
            done.append(u"隐藏 " + result)

    if hide_links:
        result = _hide_category_of(view, RevitLinkInstance, u"链接模型")
        if result:
            done.append(u"隐藏 链接模型")

    return done


# ---------------------------------------------------------------- 界面

SHELL_XAML = u"""<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
    xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
    Title="性能优化" Height="760" Width="1000" WindowStartupLocation="CenterScreen">
  <Grid Margin="10">
    <Grid.RowDefinitions>
      <RowDefinition Height="*"/>
      <RowDefinition Height="Auto"/>
    </Grid.RowDefinitions>

    <TabControl Grid.Row="0">
      <TabItem Header="诊断 / 优化">
        <Grid Margin="8">
          <Grid.RowDefinitions>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="*"/>
          </Grid.RowDefinitions>
          <StackPanel Grid.Row="0" Orientation="Horizontal" Margin="0,0,0,8">
            <Button x:Name="btnDiag" Content="重新诊断" Height="28" Margin="0,0,6,0" Padding="12,2"/>
            <Button x:Name="btnView" Content="优化当前视图" Height="28" Margin="0,0,6,0" Padding="12,2"/>
            <Button x:Name="btnPurge" Content="清理未使用项" Height="28" Margin="0,0,6,0" Padding="12,2"/>
            <CheckBox x:Name="chkLinks" Content="同时隐藏链接" Margin="10,4,0,0"/>
          </StackPanel>
          <ScrollViewer Grid.Row="1" VerticalScrollBarVisibility="Auto">
            <TextBox x:Name="report" IsReadOnly="True" TextWrapping="Wrap"
                     FontFamily="Consolas" FontSize="12" BorderThickness="0"
                     Background="Transparent" AcceptsReturn="True"/>
          </ScrollViewer>
        </Grid>
      </TabItem>

      <TabItem Header="批量删视图">
        <Grid Margin="8">
          <Grid.RowDefinitions>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="*"/>
            <RowDefinition Height="Auto"/>
          </Grid.RowDefinitions>
          <TextBlock Grid.Row="0" Margin="0,0,0,8" TextWrapping="Wrap"
                     Text="左侧按视图类型分组（数量）。选中后在右侧预览将要删除的视图；默认只删「没有放在图纸上」的视图。"/>
          <Grid Grid.Row="1">
            <Grid.ColumnDefinitions>
              <ColumnDefinition Width="280"/>
              <ColumnDefinition Width="*"/>
            </Grid.ColumnDefinitions>
            <ListBox x:Name="typeList" Grid.Column="0" Margin="0,0,8,0"/>
            <ScrollViewer Grid.Column="1" VerticalScrollBarVisibility="Auto">
              <TextBox x:Name="preview" IsReadOnly="True" TextWrapping="Wrap"
                       FontFamily="Consolas" FontSize="12" BorderThickness="1"
                       AcceptsReturn="True"/>
            </ScrollViewer>
          </Grid>
          <StackPanel Grid.Row="2" Orientation="Horizontal" Margin="0,8,0,0">
            <CheckBox x:Name="chkSheets" Content="包含已放在图纸上的视图" Margin="0,4,12,0"/>
            <Button x:Name="btnDelete" Content="删除所选类型的视图" Height="28" Padding="12,2"/>
          </StackPanel>
        </Grid>
      </TabItem>
    </TabControl>

    <TextBlock Grid.Row="1" x:Name="status" Margin="4,8,0,0" Text="就绪"/>
  </Grid>
</Window>"""


class OptimizeWindow(forms.WPFWindow):
    def __init__(self):
        forms.WPFWindow.__init__(self, SHELL_XAML, literal_string=True)
        self.report = self.FindName("report")
        self.status = self.FindName("status")
        self.chk_links = self.FindName("chkLinks")
        self.chk_sheets = self.FindName("chkSheets")
        self.type_list = self.FindName("typeList")
        self.preview = self.FindName("preview")

        self.views = []
        self.placed = set()
        self.groups = {}
        self._targets = []

        self.FindName("btnDiag").Click += self._on_diag
        self.FindName("btnView").Click += self._on_view
        self.FindName("btnPurge").Click += self._on_purge
        self.FindName("btnDelete").Click += self._on_delete
        self.type_list.SelectionChanged += self._on_type_selected
        self.chk_sheets.Click += self._on_type_selected

        self._on_diag(None, None)
        self._refresh_types()

    # ---------- 诊断 ----------
    def _on_diag(self, sender, args):
        rows, advice = diag()
        lines = [u"【当前模型诊断】", u""]
        for label, value in rows:
            lines.append(u"  {0}：{1}".format(label, value))
        lines.append(u"")
        if advice:
            lines.append(u"【建议】")
            for item in advice:
                lines.append(u"  · " + item)
        else:
            lines.append(u"【建议】未发现明显拖慢项。")
        lines.append(u"")
        lines.append(u"【Revit 选项里还能做的】")
        lines.append(u"  · 选项 → 图形：关闭「使用抗锯齿」「显示模型轮廓线」；")
        lines.append(u"  · 视图控制栏：着色而非真实，关闭阴影与环境光遮蔽；")
        lines.append(u"  · 另存为时勾选压缩，或做一次「核查模型」。")
        self.report.Text = u"\n".join(lines)
        self.status.Text = u"诊断完成"

    # ---------- 视图优化 ----------
    def _on_view(self, sender, args):
        view = safe_call(lambda: doc.ActiveView, None)
        if view is None:
            forms.alert(u"没有活动视图。")
            return
        hide_links = bool(safe_call(lambda: self.chk_links.IsChecked, False))
        try:
            with revit.Transaction(u"视图性能优化"):
                done = optimize_view(view, hide_links)
        except Exception:
            detail = traceback.format_exc()
            logger.error(detail)
            forms.alert(u"优化失败：\n{0}".format(detail), title=u"性能优化")
            return
        self.status.Text = (u"已优化：" + u"、".join(done)) if done \
            else u"当前视图已是优化状态，无需改动"

    # ---------- 清理未使用 ----------
    def _on_purge(self, sender, args):
        answer = forms.alert(
            u"清理未使用项会删除项目里没用到的族、类型、材质、视图模板等。\n\n"
            u"通常能减小文件、加快打开；删掉的类型以后要用需重新载入。\n"
            u"建议先保存备份。\n\n确定继续吗？",
            title=u"清理未使用项", yes=True, no=True)
        if answer not in (True, u"Yes", u"yes", u"是"):
            return
        total = 0
        try:
            with revit.Transaction(u"清理未使用项"):
                removed = safe_call(lambda: doc.PurgeUnused(), None)
                total = removed if isinstance(removed, int) else len(removed or [])
        except Exception:
            detail = traceback.format_exc()
            logger.error(detail)
            forms.alert(u"清理失败：\n{0}".format(detail), title=u"性能优化")
            return
        self.status.Text = u"已清理 {0} 项未使用内容".format(total)
        self._on_diag(None, None)

    # ---------- 批量删视图 ----------
    def _refresh_types(self):
        self.views = all_views()
        self.placed = placed_view_ids()
        self.groups = {}
        for item in self.views:
            self.groups.setdefault(item["type"], []).append(item)
        self.type_list.Items.Clear()
        for kind in sorted(self.groups.keys()):
            total = len(self.groups[kind])
            orphan = len([i for i in self.groups[kind] if i["id"] not in self.placed])
            self.type_list.Items.Add(
                u"{0}（共 {1}，未上图纸 {2}）".format(kind, total, orphan))
        self._targets = []
        self.preview.Text = u"左侧选择一个视图类型，这里会列出将要删除的视图。"

    def _selected_type(self):
        index = safe_call(lambda: self.type_list.SelectedIndex, -1)
        kinds = sorted(self.groups.keys())
        if index is None or index < 0 or index >= len(kinds):
            return None
        return kinds[index]

    def _on_type_selected(self, sender, args):
        kind = self._selected_type()
        if kind is None:
            return
        include_sheet = bool(safe_call(lambda: self.chk_sheets.IsChecked, False))
        items = self.groups[kind]
        targets = [i for i in items if include_sheet or i["id"] not in self.placed]
        lines = [u"类型：{0}".format(kind),
                 u"该类型共 {0} 个，本次将删除 {1} 个".format(len(items), len(targets)),
                 u""]
        for item in targets[:300]:
            mark = u"[图纸]" if item["id"] in self.placed else u"      "
            lines.append(u"{0} {1}".format(mark, item["name"]))
        if len(targets) > 300:
            lines.append(u"… 还有 {0} 个未列出".format(len(targets) - 300))
        self.preview.Text = u"\n".join(lines)
        self._targets = targets

    def _on_delete(self, sender, args):
        kind = self._selected_type()
        if kind is None:
            forms.alert(u"先在左侧选一个视图类型。")
            return
        targets = self._targets
        if not targets:
            forms.alert(u"这个类型下没有可删的视图（可能都在图纸上，或只有当前视图）。")
            return
        include_sheet = bool(safe_call(lambda: self.chk_sheets.IsChecked, False))
        answer = forms.alert(
            u"将删除 {0} 个「{1}」视图{2}。\n\n删除视图不可撤销，建议先保存备份。\n\n确定删除吗？".format(
                len(targets), kind,
                u"（包含已放在图纸上的）" if include_sheet else u"（仅未上图纸的）"),
            title=u"批量删视图", yes=True, no=True)
        if answer not in (True, u"Yes", u"yes", u"是"):
            return

        ok = 0
        failed = 0
        first_error = None
        try:
            with revit.Transaction(u"批量删除视图"):
                for item in targets:
                    try:
                        doc.Delete(item["view"].Id)
                        ok += 1
                    except Exception:
                        failed += 1
                        if first_error is None:
                            first_error = traceback.format_exc().splitlines()[-1]
        except Exception:
            detail = traceback.format_exc()
            logger.error(detail)
            forms.alert(u"删除失败：\n{0}".format(detail), title=u"性能优化")
            return

        self.status.Text = u"已删除 {0} 个视图".format(ok)
        if failed:
            self.status.Text += u"，失败 {0}（{1}）".format(failed, (first_error or u"")[:60])
        self._refresh_types()
        self._on_diag(None, None)


def main():
    if doc is None:
        forms.alert(u"请先打开一个项目。", exitscript=True)
        return
    window = OptimizeWindow()
    window.ShowDialog()


try:
    main()
except Exception:
    output.print_md(u"脚本出错：\n```\n{0}\n```".format(traceback.format_exc()))
    forms.alert(u"脚本出错，详见输出窗口。", title=u"性能优化")
