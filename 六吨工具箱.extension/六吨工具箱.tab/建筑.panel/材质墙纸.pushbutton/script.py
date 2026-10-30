# -*- coding: utf-8 -*-
"""材质库 / 墙纸

两个标签页：
  * 材质库：列出项目里的材质（名称 + 颜色），选中后贴到所选墙；
  * 图片墙纸：列出图片文件夹里的图片，选中后由图片生成/复用材质再贴到墙。

贴墙的方式：
  默认「每个墙单独类型」——复制一份墙类型（原名-材质名），只改它的面层材质，
  再赋给选中的墙。这样不会影响同类型的其它墙。
  取消勾选则直接改原墙类型（影响该类型所有墙，会给确认提示）。
"""

from __future__ import print_function

import io
import os
import traceback

import clr

clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")

from Autodesk.Revit.DB import (
    Color,
    FilteredElementCollector,
    Material,
    Wall,
    WallType,
)
from System import Uri, UriKind
from System.Windows.Markup import XamlReader

from pyrevit import forms, revit, script

doc = revit.doc
uidoc = revit.uidoc
output = script.get_output()
logger = script.get_logger()

APP_DIR = os.path.join(os.environ.get("APPDATA", ""), "六吨工具箱")
WALLPAPER_DIR = os.path.join(os.path.expanduser("~"), "Documents", "六吨工具箱", "墙纸")
material_texture_state = [False]

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".bmp")


def safe_call(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


# ---------------------------------------------------------------- 选择与材质

def selected_walls():
    ids = safe_call(lambda: list(uidoc.Selection.GetElementIds()), []) or []
    walls = []
    for element_id in ids:
        element = safe_call(lambda i=element_id: doc.GetElement(i), None)
        if element is not None and isinstance(element, Wall):
            walls.append(element)
    return walls


def all_materials():
    items = safe_call(lambda: list(FilteredElementCollector(doc).OfClass(Material)), []) or []
    result = []
    for material in items:
        name = safe_call(lambda m=material: m.Name, u"")
        if not name:
            continue
        color = safe_call(lambda m=material: m.Color, None)
        hex_value = u"#808080"
        if color is not None:
            # 注意：IronPython 里 .NET Byte 直接用 {0:02X} 会拼出 "2182X" 这种脏串，
            # 必须先转成 Python int 再格式化。
            r = int(safe_call(lambda c=color: c.Red, 128) or 128)
            g = int(safe_call(lambda c=color: c.Green, 128) or 128)
            b = int(safe_call(lambda c=color: c.Blue, 128) or 128)
            r = max(0, min(255, r))
            g = max(0, min(255, g))
            b = max(0, min(255, b))
            hex_value = u"#{0:02X}{1:02X}{2:02X}".format(r, g, b)
        result.append({"name": name, "id": material.Id, "color": hex_value})
    result.sort(key=lambda x: x["name"])
    return result


def image_files():
    if not os.path.isdir(WALLPAPER_DIR):
        return []
    files = []
    for name in sorted(os.listdir(WALLPAPER_DIR)):
        if name.lower().endswith(IMAGE_EXTS):
            files.append(os.path.join(WALLPAPER_DIR, name))
    return files


def find_material_by_name(name):
    for material in safe_call(lambda: list(FilteredElementCollector(doc).OfClass(Material)), []) or []:
        if safe_call(lambda m=material: m.Name, u"") == name:
            return material
    return None



TEXTURE_PROP_NAMES = ("unifiedbitmap_Bitmap", "UnifiedBitmapSchema")


def try_set_texture(material, image_path):
    """尽力给材质挂上图片纹理：复制一个带 UnifiedBitmap 的外观资源再改路径。

    跨版本/跨语言不稳定，失败就返回 False，调用方退回纯色材质。
    """
    try:
        candidates = safe_call(
            lambda: list(FilteredElementCollector(doc).OfClass(Material)), []) or []
        for source in candidates:
            asset_id = safe_call(lambda m=source: m.AppearanceAssetId, None)
            if asset_id is None or asset_id.IntegerValue in (-1, 0):
                continue
            asset_element = safe_call(lambda i=asset_id: doc.GetElement(i), None)
            if asset_element is None:
                continue
            rendering = safe_call(lambda a=asset_element: a.GetRenderingAsset(), None)
            if rendering is None:
                continue

            for prop_name in TEXTURE_PROP_NAMES:
                prop = safe_call(lambda r=rendering, n=prop_name: r.FindByName(n), None)
                if prop is None:
                    continue

                base = os.path.splitext(os.path.basename(image_path))[0]
                copy = safe_call(
                    lambda a=asset_element: a.Duplicate(u"墙纸外观-" + base), None)
                if copy is None:
                    continue
                copied_asset = safe_call(lambda c=copy: c.GetRenderingAsset(), None)
                if copied_asset is None:
                    continue
                target = safe_call(
                    lambda r=copied_asset, n=prop_name: r.FindByName(n), None)
                if target is None:
                    continue
                safe_call(lambda t=target, v=image_path: setattr(t, "Value", v))
                safe_call(lambda m=material, c=copy: setattr(m, "AppearanceAssetId", c.Id))
                return True
    except Exception:
        return False
    return False


def ensure_image_material(image_path):
    """为图片准备材质：已有同名就复用；否则新建一个纯色材质（颜色取图片平均色）。

    说明：Revit 2020 API 里直接给材质贴纹理要改外观资源（Appearance Asset）的
    Bitmap 属性，跨版本不稳定，这里只做「新建带近似颜色的材质」，
    真实纹理建议在材质浏览器里手动换贴图——材质名已经建好，替换很快。
    """
    base = os.path.splitext(os.path.basename(image_path))[0]
    name = u"墙纸-" + base
    existing = find_material_by_name(name)
    if existing is not None:
        return existing

    color = (200, 200, 200)
    try:
        from System.Drawing import Bitmap
        bmp = Bitmap(image_path)
        try:
            w = max(1, bmp.Width // 8)
            h = max(1, bmp.Height // 8)
            r = g = b = 0
            count = 0
            for x in range(0, bmp.Width, w):
                for y in range(0, bmp.Height, h):
                    pixel = bmp.GetPixel(x, y)
                    r += pixel.R
                    g += pixel.G
                    b += pixel.B
                    count += 1
            if count:
                color = (r // count, g // count, b // count)
        finally:
            bmp.Dispose()
    except Exception:
        pass

    material = Material.Create(doc, name)
    safe_call(lambda: setattr(material, "Color", Color(color[0], color[1], color[2])))
    if try_set_texture(material, image_path):
        material_texture_state[0] = True
    return material


# ---------------------------------------------------------------- 贴材质

def apply_material(walls, material, per_wall_type, exterior_only):
    """把材质写到墙的面层，返回 (成功数, 失败列表)。"""
    ok = 0
    failed = []
    material_id = material.Id
    suffix = safe_call(lambda m=material: m.Name, u"材质")

    for wall in walls:
        try:
            wall_type = safe_call(lambda w=wall: w.WallType, None)
            if wall_type is None:
                failed.append(u"{0}：取不到墙类型".format(
                    safe_call(lambda w=wall: w.Name, u"?")))
                continue

            if per_wall_type:
                base_name = safe_call(lambda t=wall_type: t.Name, u"墙")
                new_name = u"{0}-{1}".format(base_name, suffix)
                existed = find_wall_type_by_name(new_name)
                target_type = existed if existed is not None \
                    else wall_type.Duplicate(new_name)
            else:
                target_type = wall_type

            structure = safe_call(lambda t=target_type: t.GetCompoundStructure(), None)
            if structure is None:
                failed.append(u"{0}：不是可编辑的复合墙".format(
                    safe_call(lambda w=wall: w.Name, u"?")))
                continue

            layers = safe_call(lambda s=structure: s.GetLayers(), []) or []
            if not layers:
                failed.append(u"{0}：没有结构层".format(
                    safe_call(lambda w=wall: w.Name, u"?")))
                continue

            if exterior_only:
                indexes = [len(layers) - 1]
            else:
                indexes = list(range(len(layers)))

            for index in indexes:
                structure.SetMaterialId(index, material_id)
            target_type.SetCompoundStructure(structure)

            if per_wall_type:
                safe_call(lambda w=wall, t=target_type: w.ChangeTypeId(t.Id))
                actual = safe_call(lambda w=wall: w.WallType.Id.IntegerValue, -1)
                if actual != safe_call(lambda t=target_type: t.Id.IntegerValue, -2):
                    failed.append(u"{0}：换类型失败".format(
                        safe_call(lambda w=wall: w.Name, u"?")))
                    continue
            ok += 1
        except Exception:
            failed.append(u"{0}：{1}".format(
                safe_call(lambda w=wall: w.Name, u"?"),
                traceback.format_exc().splitlines()[-1]))
    return ok, failed


def find_wall_type_by_name(name):
    for wall_type in safe_call(lambda: list(FilteredElementCollector(doc).OfClass(WallType)), []) or []:
        if safe_call(lambda t=wall_type: t.Name, u"") == name:
            return wall_type
    return None


# ---------------------------------------------------------------- 界面

SHELL_XAML = u"""<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
    xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
    Title="材质库 / 墙纸" Height="780" Width="1000" WindowStartupLocation="CenterScreen">
  <Grid Margin="10">
    <Grid.RowDefinitions>
      <RowDefinition Height="Auto"/>
      <RowDefinition Height="*"/>
      <RowDefinition Height="Auto"/>
    </Grid.RowDefinitions>

    <StackPanel Grid.Row="0" Orientation="Horizontal" Margin="0,0,0,8">
      <TextBox x:Name="searchBox" Width="200" Height="26" Margin="0,0,8,0"/>
      <Button x:Name="btnRefresh" Content="刷新" Height="28" Margin="0,0,10,0" Padding="14,2"/>
      <CheckBox x:Name="chkExterior" Content="只改外表面层" IsChecked="True" Margin="0,4,12,0"/>
      <CheckBox x:Name="chkPerWall" Content="每个墙单独类型" IsChecked="True" Margin="0,4,12,0"/>
      <Button x:Name="btnApply" Content="贴到所选墙" Height="28" Padding="14,2"/>
    </StackPanel>

    <Grid Grid.Row="1">
      <Grid.ColumnDefinitions>
        <ColumnDefinition Width="*"/>
        <ColumnDefinition Width="280"/>
      </Grid.ColumnDefinitions>

      <TabControl Grid.Column="0">
        <TabItem Header="材质库">
          <ListBox x:Name="materialList" Margin="4"/>
        </TabItem>
        <TabItem Header="图片墙纸">
          <Grid Margin="4">
            <Grid.RowDefinitions>
              <RowDefinition Height="Auto"/>
              <RowDefinition Height="*"/>
            </Grid.RowDefinitions>
            <TextBlock Grid.Row="0" TextWrapping="Wrap" Margin="0,0,0,6"
                       Text="图片放在 文档\六吨工具箱\墙纸，点「刷新」后会在下面列出。"/>
            <ListBox x:Name="imageList" Grid.Row="1"/>
          </Grid>
        </TabItem>
      </TabControl>

      <StackPanel Grid.Column="1" Margin="10,4,0,0">
        <TextBlock Text="预览" FontWeight="Bold" Margin="0,0,0,6"/>
        <Border x:Name="previewBorder" Width="240" Height="240"
                Background="#1D2733" CornerRadius="6" HorizontalAlignment="Left">
          <Image x:Name="previewImage" Stretch="Uniform" Width="236" Height="236"/>
        </Border>
        <TextBlock x:Name="previewText" TextWrapping="Wrap" Margin="0,8,0,0"
                   FontSize="12" Opacity="0.85"/>
        <TextBlock x:Name="previewHint" TextWrapping="Wrap" Margin="0,8,0,0"
                   FontSize="11" Opacity="0.5"
                   Text="点左侧列表里的条目才会加载预览图（避免一次解码太多图片导致卡死）。"/>
      </StackPanel>
    </Grid>

    <TextBlock Grid.Row="2" x:Name="status" Margin="2,8,0,0"
               Text="先在模型里选中墙，再选材质，然后点「贴到所选墙」。"/>
  </Grid>
</Window>"""


def make_bitmap(path, decode_width=480):
    try:
        from System.Windows.Media.Imaging import BitmapCacheOption, BitmapImage
        bitmap = BitmapImage()
        bitmap.BeginInit()
        bitmap.UriSource = safe_call(
            lambda: Uri(path.replace(u"\\", u"/"), UriKind.Absolute), None)
        bitmap.CacheOption = BitmapCacheOption.OnLoad
        bitmap.DecodePixelWidth = decode_width
        bitmap.EndInit()
        bitmap.Freeze()
        return bitmap
    except Exception:
        return None


def brush_from_hex(hex_value):
    try:
        brush = XamlReader.Parse(
            u'<SolidColorBrush xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" Color="{0}"/>'.format(hex_value))
        return brush
    except Exception:
        return None


def material_texture_path(material):
    """材质的外观资源里如果引用了图片文件，返回该路径。"""
    try:
        asset_id = safe_call(lambda: material.AppearanceAssetId, None)
        if asset_id is None or asset_id.IntegerValue in (-1, 0):
            return None
        element = safe_call(lambda: doc.GetElement(asset_id), None)
        if element is None:
            return None
        rendering = safe_call(lambda: element.GetRenderingAsset(), None)
        if rendering is None:
            return None
        for name in ("unifiedbitmap_Bitmap", "UnifiedBitmapSchema"):
            prop = safe_call(lambda r=rendering, n=name: r.FindByName(n), None)
            if prop is None:
                continue
            value = safe_call(lambda p=prop: p.Value, None)
            if value and os.path.isfile(value):
                return value
    except Exception:
        return None
    return None


def material_texture_path_by_id(material_id):
    material = safe_call(lambda: doc.GetElement(material_id), None)
    if material is None:
        return None
    return material_texture_path(material)


class MaterialWindow(forms.WPFWindow):
    def __init__(self):
        forms.WPFWindow.__init__(self, SHELL_XAML, literal_string=True)
        self.search_box = self.FindName("searchBox")
        self.status = self.FindName("status")
        self.material_list = self.FindName("materialList")
        self.image_list = self.FindName("imageList")
        self.preview_border = self.FindName("previewBorder")
        self.preview_image = self.FindName("previewImage")
        self.preview_text = self.FindName("previewText")
        self.chk_exterior = self.FindName("chkExterior")
        self.chk_per_wall = self.FindName("chkPerWall")

        self.materials = []
        self.visible_materials = []
        self.images = []
        self.choice = None

        self.search_box.TextChanged += self._on_search
        self.material_list.SelectionChanged += self._on_material_selected
        self.image_list.SelectionChanged += self._on_image_selected
        self.FindName("btnRefresh").Click += self._on_refresh
        self.FindName("btnApply").Click += self._on_apply

        self._load()

    # ---------- 加载（只出文字，不解码图片）----------
    def _load(self):
        self.materials = all_materials()
        self.images = image_files()
        self._fill_materials()
        self.image_list.Items.Clear()
        for path in self.images:
            self.image_list.Items.Add(os.path.basename(path))
        self._update_status()
        self._clear_preview()

    def _update_status(self):
        self.status.Text = u"材质 {0} 个 · 墙纸图片 {1} 张（点条目看预览）".format(
            len(self.materials), len(self.images))

    def _clear_preview(self):
        safe_call(lambda: setattr(self.preview_image, "Source", None))
        backdrop = brush_from_hex(u"#1D2733")
        if backdrop is not None:
            safe_call(lambda: setattr(self.preview_border, "Background", backdrop))
        safe_call(lambda: setattr(self.preview_text, "Text", u""))

    def _fill_materials(self):
        keyword = (self.search_box.Text or u"").strip().lower()
        self.visible_materials = [m for m in self.materials
                                  if not keyword or keyword in m["name"].lower()]
        self.material_list.Items.Clear()
        for item in self.visible_materials:
            self.material_list.Items.Add(u"{0}    {1}".format(item["name"], item["color"]))

    # ---------- 选中才加载预览 ----------
    def _on_material_selected(self, sender, args):
        index = safe_call(lambda: self.material_list.SelectedIndex, -1)
        if index is None or index < 0 or index >= len(self.visible_materials):
            return
        item = self.visible_materials[index]
        self.choice = ("material", item["id"])
        self._clear_preview()
        self.preview_text.Text = u"材质：{0}\n色值：{1}".format(item["name"], item["color"])

        texture = material_texture_path_by_id(item["id"])
        if texture:
            source = make_bitmap(texture)
            if source is not None:
                safe_call(lambda s=source: setattr(self.preview_image, "Source", s))
                self.preview_text.Text += u"\n贴图：{0}".format(os.path.basename(texture))
                self.status.Text = u"已选材质：{0}（含贴图）".format(item["name"])
                return
        brush = brush_from_hex(item["color"])
        if brush is not None:
            safe_call(lambda b=brush: setattr(self.preview_border, "Background", b))
        self.status.Text = u"已选材质：{0}".format(item["name"])

    def _on_image_selected(self, sender, args):
        index = safe_call(lambda: self.image_list.SelectedIndex, -1)
        if index is None or index < 0 or index >= len(self.images):
            return
        path = self.images[index]
        self.choice = ("image", path)
        self._clear_preview()
        source = make_bitmap(path, 480)
        if source is not None:
            safe_call(lambda s=source: setattr(self.preview_image, "Source", s))
        self.preview_text.Text = u"墙纸：{0}\n{1}".format(
            os.path.basename(path), path)
        self.status.Text = u"已选墙纸：{0}".format(os.path.basename(path))

    def _on_search(self, sender, args):
        self._fill_materials()
        self._clear_preview()
        self.choice = None

    def _on_refresh(self, sender, args):
        self.choice = None
        self._load()

    # ---------- 应用 ----------
    def _on_apply(self, sender, args):
        if not self.choice:
            forms.alert(u"先选一个材质或墙纸。")
            return
        walls = selected_walls()
        if not walls:
            forms.alert(u"先在模型里选中要贴的墙。")
            return

        kind, value = self.choice
        per_wall = bool(safe_call(lambda: self.chk_per_wall.IsChecked, True))
        exterior = bool(safe_call(lambda: self.chk_exterior.IsChecked, True))

        if not per_wall:
            answer = forms.alert(
                u"将修改墙类型的结构层材质（影响该类型所有墙）。继续吗？",
                title=u"材质库", yes=True, no=True)
            if answer not in (True, u"Yes", u"yes", u"是"):
                return

        try:
            with revit.Transaction(u"贴材质 / 墙纸"):
                if kind == "image":
                    material_texture_state[0] = False
                    material = ensure_image_material(value)
                else:
                    material = doc.GetElement(value)
                if material is None:
                    forms.alert(u"取不到材质。")
                    return
                ok, failed = apply_material(walls, material, per_wall, exterior)
        except Exception:
            detail = traceback.format_exc()
            logger.error(detail)
            forms.alert(u"贴材质失败：\n{0}".format(detail), title=u"材质库")
            return

        text = u"成功 {0} / {1} 面墙".format(ok, len(walls))
        if kind == "image":
            text += u"；" + (u"已挂图片纹理" if material_texture_state[0]
                             else u"仅生成同色材质（纹理需在材质浏览器里换图）")
        if failed:
            text += u"；失败 {0}".format(len(failed))
            output.print_md(u"### 失败明细\n" + u"\n".join(u"- " + f for f in failed[:20]))
        self.status.Text = text


def main():
    if doc is None:
        forms.alert(u"请先打开一个项目。", exitscript=True)
        return
    MaterialWindow().ShowDialog()


try:
    main()
except Exception:
    output.print_md(u"脚本出错：\n```\n{0}\n```".format(traceback.format_exc()))
    forms.alert(u"脚本出错，详见输出窗口。", title=u"材质库")
