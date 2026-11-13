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
    ViewPlan,
    ViewSheet,
    Transaction,
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


def rescue_plan_views():
    """把所有平面视图统一降到「粗略」并隐藏导入 CAD / 点云，返回改动统计。"""
    views = safe_call(lambda: list(FilteredElementCollector(doc).OfClass(ViewPlan)), []) or []
    total = 0
    changed = 0
    for view in views:
        if safe_call(lambda v=view: v.IsTemplate, False):
            continue
        total += 1
        done = optimize_view(view)
        if done:
            changed += 1
    return total, changed


def purge_unused():
    """清理未使用项（族/类型/材质/视图样板等）。Revit 2020 用无参 PurgeUnused()。"""
    transaction = Transaction(doc, u"清理未使用项")
    removed = 0
    try:
        transaction.Start()
        ids = doc.PurgeUnused()
        removed = len(list(ids)) if ids is not None else 0
        transaction.Commit()
    except Exception:
        safe_call(lambda: transaction.RollBack())
        removed = 0
    return removed


# ---------------------------------------------------------------- 插件管理

ADDIN_DIRS = [
    os.path.join(os.environ.get("APPDATA", ""), "Autodesk", "Revit", "Addins", "2020"),
    r"C:\ProgramData\Autodesk\Revit\Addins\2020",
]

KEEP_IN_MATCH = ("liudun", "六吨", "ths", "斯维尔", "tangent", "sware")


def addin_title(path):
    try:
        text = open(path, "rb").read().decode("utf-8", "ignore")
    except Exception:
        return u"(读不到)"
    import re as _re
    for tag in ("Name", "Assembly", "FullClassName"):
        match = _re.search(r"<" + tag + r">\s*([^<]+)", text, _re.I)
        if match:
            value = match.group(1).strip()
            if tag == "Assembly":
                value = os.path.basename(value)
            if tag == "FullClassName":
                continue
            return value
    return os.path.basename(path)


def addin_files():
    items = []
    for index, folder in enumerate(ADDIN_DIRS):
        if not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder)):
            lower = name.lower()
            if lower.endswith(".addin"):
                enabled = True
            elif lower.endswith(".addin.disabled"):
                enabled = False
            else:
                continue
            path = os.path.join(folder, name)
            items.append({
                "path": path,
                "file": name,
                "folder": folder,
                "enabled": enabled,
                "machine": (index == 1),
                "title": addin_title(path),
            })
    items.sort(key=lambda i: (not i["enabled"], i["machine"], i["file"].lower()))
    return items


def set_addin_enabled(item, enable):
    path = item["path"]
    lower = path.lower()
    if enable:
        if lower.endswith(".addin.disabled"):
            target = path[:-len(".disabled")]
            os.rename(path, target)
            return target
        return path
    if lower.endswith(".addin"):
        target = path + ".disabled"
        os.rename(path, target)
        return target
    return path


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
            <Button x:Name="btnRescue" Content="一键急救（全部平面视图）" Height="28" Margin="0,0,6,0" Padding="12,2" FontWeight="Bold"/>
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

      <TabItem Header="插件管理">
        <Grid Margin="8">
          <Grid.RowDefinitions>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="*"/>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="Auto"/>
          </Grid.RowDefinitions>
          <TextBlock Grid.Row="0" Margin="0,0,0,8" TextWrapping="Wrap"
                     Text="勾选 = 下次启动 Revit 时加载；取消勾选 = 不加载（改的是 .addin 文件后缀，不删文件）。改完必须重启 Revit 才生效。"/>
          <ListBox x:Name="addinList" Grid.Row="1" SelectionMode="Extended"/>
          <StackPanel Grid.Row="2" Orientation="Horizontal" Margin="0,8,0,0">
            <Button x:Name="btnAddinEnable" Content="启用所选" Height="28" Margin="0,0,6,0" Padding="12,2"/>
            <Button x:Name="btnAddinDisable" Content="停用所选" Height="28" Margin="0,0,6,0" Padding="12,2"/>
            <Button x:Name="btnAddinAll" Content="全部启用" Height="28" Margin="0,0,6,0" Padding="12,2"/>
            <Button x:Name="btnAddinMatch" Content="比赛模式（只留六吨+斯维尔）" Height="28" Margin="0,0,6,0" Padding="12,2"/>
            <Button x:Name="btnAddinRefresh" Content="刷新" Height="28" Padding="12,2"/>
          </StackPanel>
          <TextBlock Grid.Row="3" x:Name="addinNote" Margin="0,8,0,0" TextWrapping="Wrap"
                     Foreground="Gray" Text=""/>
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
        self.FindName("btnRescue").Click += self._on_rescue
        self.FindName("btnView").Click += self._on_view
        self.FindName("btnPurge").Click += self._on_purge
        self.FindName("btnDelete").Click += self._on_delete
        self.type_list.SelectionChanged += self._on_type_selected
        self.chk_sheets.Click += self._on_type_selected

        self.addin_list = self.FindName("addinList")
        self.addin_note = self.FindName("addinNote")
        self.FindName("btnAddinEnable").Click += self._on_addin_enable
        self.FindName("btnAddinDisable").Click += self._on_addin_disable
        self.FindName("btnAddinAll").Click += self._on_addin_all
        self.FindName("btnAddinMatch").Click += self._on_addin_match
        self.FindName("btnAddinRefresh").Click += self._on_addin_refresh

        self._on_diag(None, None)
        self._refresh_types()
        self._refresh_addins()

    # ---------- 插件管理 ----------
    def _refresh_addins(self):
        self._addins = addin_files()
        self.addin_list.Items.Clear()
        for item in self._addins:
            mark = u"☑" if item["enabled"] else u"☐"
            where = u"公共" if item["machine"] else u"用户"
            self.addin_list.Items.Add(u"{0} {1}  [{2}]  {3}".format(
                mark, item["title"], where, item["file"]))
        enabled = len([i for i in self._addins if i["enabled"]])
        self.addin_note.Text = u"共 {0} 个插件，已启用 {1} 个。勾选状态在下次启动 Revit 时生效。".format(
            len(self._addins), enabled)

    def _selected_addins(self):
        picked = []
        for index in self.addin_list.SelectedIndices:
            if 0 <= index < len(self._addins):
                picked.append(self._addins[index])
        return picked

    def _apply_addin(self, items, enable):
        done = []
        skipped = []
        for item in items:
            if (not enable) and ("liudun" in item["path"].lower()):
                skipped.append(item["file"])
                continue
            try:
                set_addin_enabled(item, enable)
                done.append(item["file"])
            except Exception as error:
                skipped.append(u"{0}（{1}）".format(item["file"], error))
        self._refresh_addins()
        text = u"已{0} {1} 个，重启 Revit 生效。".format(
            u"启用" if enable else u"停用", len(done))
        if skipped:
            text += u"\n跳过 {0} 个：{1}".format(len(skipped), u"、".join(skipped[:6]))
        self.addin_note.Text = text
        self.status.Text = text.split(u"\n")[0]

    def _on_addin_enable(self, sender, args):
        items = self._selected_addins()
        if not items:
            self.addin_note.Text = u"先在列表里选中要启用的插件（可按住 Ctrl 多选）。"
            return
        self._apply_addin(items, True)

    def _on_addin_disable(self, sender, args):
        items = self._selected_addins()
        if not items:
            self.addin_note.Text = u"先在列表里选中要停用的插件（可按住 Ctrl 多选）。"
            return
        self._apply_addin(items, False)

    def _on_addin_all(self, sender, args):
        self._apply_addin(list(self._addins), True)

    def _on_addin_match(self, sender, args):
        answer = forms.alert(
            u"比赛模式：只保留「六吨工具箱」和「斯维尔/THS」相关插件，"
            u"其他全部停用（改 .addin 后缀，不删文件）。\n\n继续吗？",
            title=u"插件管理", yes=True, no=True)
        if answer not in (True, u"Yes", u"yes", u"是"):
            return
        keep, drop = [], []
        for item in self._addins:
            text = item["path"].lower()
            if any(word in text for word in KEEP_IN_MATCH):
                keep.append(item)
            else:
                drop.append(item)
        for item in keep:
            try:
                set_addin_enabled(item, True)
            except Exception:
                pass
        for item in drop:
            try:
                set_addin_enabled(item, False)
            except Exception:
                pass
        self._refresh_addins()
        self.addin_note.Text = u"比赛模式已设置：启用 {0} 个，停用 {1} 个。重启 Revit 生效。".format(
            len(keep), len(drop))
        self.status.Text = u"比赛模式已应用，重启 Revit 生效"

    def _on_addin_refresh(self, sender, args):
        self._refresh_addins()
        self.status.Text = u"插件列表已刷新"

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

    # ---------- 一键急救 ----------
    def _on_rescue(self, sender, args):
        answer = forms.alert(
            u"一键急救会做三件事：\n\n"
            u"1. 把所有平面视图的详细程度改成「粗略」；\n"
            u"2. 在所有这些视图里隐藏「导入的 CAD」和「点云」；\n"
            u"3. 清理未使用项（族/类型/材质）。\n\n"
            u"这些只影响显示，不会改模型几何；视图设置可用 Ctrl+Z 撤销。\n"
            u"建议先保存一次。\n\n确定继续吗？",
            title=u"一键急救", yes=True, no=True)
        if answer not in (True, u"Yes", u"yes", u"是"):
            return

        total, changed = rescue_plan_views()

        removed = 0
        try:
            with revit.Transaction(u"清理未使用项"):
                result = safe_call(lambda: doc.PurgeUnused(), None)
                removed = len(list(result)) if result is not None else 0
        except Exception:
            logger.error(traceback.format_exc())

        lines = [
            u"【一键急救完成】",
            u"",
            u"平面视图：检查 {0} 个，改动 {1} 个（粗略 + 隐藏 CAD/点云）".format(total, changed),
            u"未使用项：清理 {0} 个".format(removed),
            u"",
            u"接下来手动做这几件（效果更大）：",
            u"  · 视图控制栏：关阴影、关「显示边缘」、视觉样式改「隐藏线」或「一致的颜色」；",
            u"  · VV → 填充图案 → 取消「表面填充图案」；",
            u"  · 管理 → 管理链接：卸载不用的模型；",
            u"  · 关掉没用的 3D 视图 / 图纸（用「批量删视图」页）；",
            u"  · 另存为副本并勾选「压缩」。",
        ]
        self.report.Text = u"\n".join(lines)
        self.status.Text = u"急救完成：改 {0} 个视图，清 {1} 项".format(changed, removed)

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
