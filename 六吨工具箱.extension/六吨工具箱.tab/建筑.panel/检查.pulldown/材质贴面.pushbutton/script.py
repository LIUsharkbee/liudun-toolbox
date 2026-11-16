# -*- coding: utf-8 -*-
"""材质贴面

两个标签页：
  * 材质库：列出项目里的材质，选中后显示预览；
  * 图片墙纸：列出 文档\六吨工具箱\墙纸 里的图片，选中后显示预览。

两种贴法：
  1. 按面刷（油漆）：给选中的墙刷面材质；
  2. 贴面（选中构件）：给选中的墙（竖直面）/楼板（顶面）生成 N mm 贴面实体。

注意：窗口是模态的，Revit 不允许在模态窗口里 PickObject 拾取面，
所以所有操作都基于「当前选中」，不进入拾取状态。
"""

from __future__ import print_function

import hashlib
import os
import traceback

import clr

clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")

from Autodesk.Revit.DB import (
    AppearanceAssetElement,
    Color,
    DirectShape,
    ElementId,
    FilteredElementCollector,
    GeometryCreationUtilities,
    GeometryObject,
    Material,
    Options,
    PlanarFace,
    SolidOptions,
    UV,
    Wall,
    XYZ,
)
from Autodesk.Revit.DB.Visual import AssetType
from System import Uri, UriKind
from System.Collections.Generic import List as NetList
from System.Windows.Markup import XamlReader

from pyrevit import forms, revit, script

doc = revit.doc
uidoc = revit.uidoc
output = script.get_output()
logger = script.get_logger()

WALLPAPER_DIR = os.path.join(os.path.expanduser("~"), "Documents", "六吨工具箱", "墙纸")
TEXTURE_CACHE = os.path.join(os.environ.get("LOCALAPPDATA", ""), "liudun-textures")
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".bmp")
TEXTURE_PROPS = ("unifiedbitmap_Bitmap", "UnifiedBitmapSchema", "bitmap")


def safe_call(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


# ---------------------------------------------------------------- 数据

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
            r = int(safe_call(lambda c=color: c.Red, 128) or 128)
            g = int(safe_call(lambda c=color: c.Green, 128) or 128)
            b = int(safe_call(lambda c=color: c.Blue, 128) or 128)
            hex_value = u"#{0:02X}{1:02X}{2:02X}".format(
                max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b)))
        result.append({"name": name, "id": material.Id, "color": hex_value})
    result.sort(key=lambda x: x["name"])
    return result


def image_files():
    if not os.path.isdir(WALLPAPER_DIR):
        return []
    return [os.path.join(WALLPAPER_DIR, name)
            for name in sorted(os.listdir(WALLPAPER_DIR))
            if name.lower().endswith(IMAGE_EXTS)]


def find_material_by_name(name):
    for material in safe_call(lambda: list(FilteredElementCollector(doc).OfClass(Material)), []) or []:
        if safe_call(lambda m=material: m.Name, u"") == name:
            return material
    return None


def selected_surfaces():
    ids = safe_call(lambda: list(uidoc.Selection.GetElementIds()), []) or []
    result = []
    for element_id in ids:
        element = safe_call(lambda i=element_id: doc.GetElement(i), None)
        if element is None:
            continue
        category = safe_call(lambda e=element: e.Category, None)
        name = safe_call(lambda c=category: c.Name, u"")
        is_wall = safe_call(lambda e=element: isinstance(e, Wall), False)
        is_floor = (u"楼板" in name) or (u"Floor" in name)
        result.append((element, is_wall, is_floor))
    return result


# ---------------------------------------------------------------- 材质

def average_color(image_path):
    color = (200, 200, 200)
    try:
        from System.Drawing import Bitmap
        bmp = Bitmap(image_path)
        try:
            step_x = max(1, bmp.Width // 16)
            step_y = max(1, bmp.Height // 16)
            r = g = b = count = 0
            for x in range(0, bmp.Width, step_x):
                for y in range(0, bmp.Height, step_y):
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
    return color


def find_generic_asset():
    assets = safe_call(
        lambda: list(doc.Application.GetAssets(AssetType.Appearance)), []) or []
    for asset in assets:
        if safe_call(lambda a=asset: a.FindByName("generic_diffuse"), None) is not None:
            return asset
    return None


def build_solid_appearance(rgb, name):
    base = find_generic_asset()
    if base is None:
        return None
    element = safe_call(lambda: AppearanceAssetElement.Create(doc, name, base), None)
    if element is None:
        element = safe_call(
            lambda: AppearanceAssetElement.GetAppearanceAssetElementByName(doc, name), None)
    if element is None:
        return None
    rendering = safe_call(lambda e=element: e.GetRenderingAsset(), None)
    prop = safe_call(lambda r=rendering: r.FindByName("generic_diffuse"), None) if rendering else None
    if prop is not None:
        safe_call(lambda p=prop: p.SetValueAsColor(Color(rgb[0], rgb[1], rgb[2])))
    return element


def ensure_color_material(image_path):
    base = os.path.splitext(os.path.basename(image_path))[0]
    name = u"纯色-" + base
    material = find_material_by_name(name)
    if material is None:
        created = Material.Create(doc, name)
        material = safe_call(lambda: doc.GetElement(created), None)
    if material is None:
        return None
    rgb = average_color(image_path)
    safe_call(lambda: setattr(material, "Color", Color(rgb[0], rgb[1], rgb[2])))
    safe_call(lambda: setattr(material, "UseRenderAppearanceForShading", True))
    element = build_solid_appearance(rgb, u"外观-纯色-" + base)
    if element is not None:
        safe_call(lambda: setattr(material, "AppearanceAssetId", element.Id))
    return material


def stage_texture(image_path):
    try:
        if not os.path.isdir(TEXTURE_CACHE):
            os.makedirs(TEXTURE_CACHE)
        ext = os.path.splitext(image_path)[1].lower() or ".png"
        digest = hashlib.md5(image_path.encode("utf-8")).hexdigest()[:12]
        target = os.path.join(TEXTURE_CACHE, "tex_" + digest + ext)
        if (not os.path.isfile(target)) or os.path.getsize(target) != os.path.getsize(image_path):
            with open(image_path, "rb") as src:
                data = src.read()
            with open(target, "wb") as dst:
                dst.write(data)
        return target
    except Exception:
        return image_path


def find_texture_base_asset():
    assets = safe_call(
        lambda: list(doc.Application.GetAssets(AssetType.Appearance)), []) or []
    for asset in assets:
        for name in TEXTURE_PROPS:
            if safe_call(lambda a=asset, n=name: a.FindByName(n), None) is not None:
                return asset, name
    return None, None


def whiten_asset_colors(asset):
    count = safe_call(lambda a=asset: a.Size, 0) or 0
    for index in range(count):
        prop = safe_call(lambda a=asset, i=index: a.Get(i), None)
        if prop is None:
            continue
        name = (safe_call(lambda p=prop: p.Name, u"") or u"").lower()
        if u"color" in name or u"diffuse" in name:
            safe_call(lambda p=prop: p.SetValueAsColor(Color(255, 255, 255)))


def build_textured_material(image_path):
    base = os.path.splitext(os.path.basename(image_path))[0]
    mat_name = u"墙纸-" + base
    asset_name = u"外观-墙纸-" + base

    material = find_material_by_name(mat_name)
    element = safe_call(
        lambda: AppearanceAssetElement.GetAppearanceAssetElementByName(doc, asset_name), None)

    prop_name = None
    if element is None:
        base_asset, prop_name = find_texture_base_asset()
        if base_asset is None:
            return material, False, u"项目里没有可用的贴图外观资源"
        element = safe_call(
            lambda: AppearanceAssetElement.Create(doc, asset_name, base_asset), None)

    if element is None:
        return material, False, u"创建外观资源失败"

    rendering = safe_call(lambda e=element: e.GetRenderingAsset(), None)
    if rendering is None:
        return material, False, u"取不到外观资源"

    if prop_name is None:
        for name in TEXTURE_PROPS:
            if safe_call(lambda r=rendering, n=name: r.FindByName(n), None) is not None:
                prop_name = name
                break
    if prop_name is None:
        return material, False, u"外观资源里没有位图属性"

    prop = safe_call(lambda r=rendering, n=prop_name: r.FindByName(n), None)
    safe_call(lambda p=prop, v=stage_texture(image_path): setattr(p, "Value", v))
    if not safe_call(lambda p=prop: p.Value, None):
        return material, False, u"位图写入失败"

    for scale_name in ("unifiedbitmap_RealWorldScaleX", "unifiedbitmap_RealWorldScaleY"):
        scale_prop = safe_call(lambda r=rendering, n=scale_name: r.FindByName(n), None)
        if scale_prop is not None:
            safe_call(lambda p=scale_prop: setattr(p, "Value", 1000.0))
    whiten_asset_colors(rendering)

    if material is None:
        created = Material.Create(doc, mat_name)
        material = safe_call(lambda: doc.GetElement(created), None)
    if material is None:
        return None, False, u"创建材质失败"
    safe_call(lambda: setattr(material, "AppearanceAssetId", element.Id))
    safe_call(lambda: setattr(material, "Color", Color(255, 255, 255)))
    safe_call(lambda: setattr(material, "UseRenderAppearanceForShading", True))
    return material, True, u"已写入图片纹理"


# ---------------------------------------------------------------- 贴面实体

def generic_model_category_id():
    categories = safe_call(lambda: list(doc.Settings.Categories), []) or []
    for category in categories:
        name = safe_call(lambda c=category: c.Name, u"")
        if name in (u"常规模型", u"Generic Models"):
            return safe_call(lambda c=category: c.Id, None)
    return None


def face_centroid(loops):
    xs, ys, zs = [], [], []
    for loop in loops:
        try:
            for curve in loop:
                point = safe_call(lambda c=curve: c.GetEndPoint(0), None)
                if point is None:
                    continue
                xs.append(safe_call(lambda p=point: p.X, 0.0))
                ys.append(safe_call(lambda p=point: p.Y, 0.0))
                zs.append(safe_call(lambda p=point: p.Z, 0.0))
        except Exception:
            continue
    if not xs:
        return None
    return (sum(xs) / len(xs), sum(ys) / len(ys), sum(zs) / len(zs))


def faces_for_element(element, is_wall, is_floor):
    faces = []
    options = Options()
    options.ComputeReferences = True
    geometry = safe_call(lambda: element.get_Geometry(options), None)
    if geometry is None:
        return faces
    for item in geometry:
        face_list = safe_call(lambda s=item: s.Faces, None)
        if face_list is None:
            continue
        for face in face_list:
            if safe_call(lambda f=face: not isinstance(f, PlanarFace), True):
                continue
            normal = safe_call(lambda f=face: f.FaceNormal, None)
            if normal is None:
                continue
            nz = safe_call(lambda n=normal: n.Z, 0.0)
            if is_floor:
                if nz > 0.7:
                    faces.append(face)
            elif is_wall:
                if abs(nz) < 0.2:
                    faces.append(face)
            else:
                faces.append(face)
    return faces


def build_panel_on_face(element, face, material_id, thickness_mm):
    loops = safe_call(lambda: face.GetEdgesAsCurveLoops(), None)
    if loops is None or not safe_call(lambda l=loops: l.Count, 0):
        return False, u"取不到面的边界"

    normal = safe_call(lambda: face.ComputeNormal(UV(0.5, 0.5)), None)
    if normal is None:
        return False, u"取不到面的法向"

    centroid = face_centroid(loops)
    direction = normal
    if centroid is not None:
        box = safe_call(lambda: element.get_BoundingBox(None), None)
        if box is not None:
            cx = (safe_call(lambda b=box: b.Min.X, 0.0) + safe_call(lambda b=box: b.Max.X, 0.0)) / 2
            cy = (safe_call(lambda b=box: b.Min.Y, 0.0) + safe_call(lambda b=box: b.Max.Y, 0.0)) / 2
            cz = (safe_call(lambda b=box: b.Min.Z, 0.0) + safe_call(lambda b=box: b.Max.Z, 0.0)) / 2
            outward = XYZ(centroid[0] - cx, centroid[1] - cy, centroid[2] - cz)
            if safe_call(lambda: normal.DotProduct(outward), 0.0) < 0:
                direction = XYZ(-normal.X, -normal.Y, -normal.Z)

    thickness_ft = thickness_mm / 304.8
    options = SolidOptions(material_id, ElementId.InvalidElementId)
    solid = safe_call(
        lambda: GeometryCreationUtilities.CreateExtrusionGeometry(
            loops, direction, thickness_ft, options), None)
    if solid is None:
        solid = safe_call(
            lambda: GeometryCreationUtilities.CreateExtrusionGeometry(
                loops, direction, thickness_ft), None)
    if solid is None:
        return False, u"生成贴面几何失败"

    category_id = generic_model_category_id()
    if category_id is None:
        category_id = safe_call(lambda: element.Category.Id, None)
    if category_id is None:
        return False, u"取不到类别"

    shape = DirectShape.CreateElement(doc, category_id)
    if shape is None:
        return False, u"创建贴面元素失败"
    shapes = NetList[GeometryObject]()
    shapes.Add(solid)
    safe_call(lambda: shape.SetShape(shapes))
    safe_call(lambda: setattr(shape, "Name", u"贴面-{0:.0f}mm".format(thickness_mm)))
    return True, u""


def paint_walls(walls, material_id):
    painted = 0
    failed = []
    for wall in walls:
        faces = faces_for_element(wall, True, False)
        if not faces:
            failed.append(u"没有可刷的竖直面")
            continue
        for face in faces:
            safe_call(lambda w=wall, f=face: doc.Paint(w.Id, f, material_id))
            painted += 1
    return painted, failed


# ---------------------------------------------------------------- 诊断

def diagnose(elements):
    lines = []
    for element, is_wall, is_floor in elements[:3]:
        lines.append(u"■ {0}（Id {1}）".format(
            safe_call(lambda e=element: e.Name, u"?"),
            safe_call(lambda e=element: e.Id.IntegerValue, -1)))
        category = safe_call(lambda e=element: e.Category, None)
        lines.append(u"  类别：{0}".format(safe_call(lambda c=category: c.Name, u"?")))
        faces = faces_for_element(element, is_wall, is_floor)
        painted = 0
        for face in faces:
            if safe_call(lambda f=face: doc.IsPainted(element.Id, f), False):
                painted += 1
        lines.append(u"  可贴面：{0} 个（已油漆 {1} 个）".format(len(faces), painted))
        lines.append(u"")
    lines.append(u"提示：「贴面」生成独立实体（常规模型），不依赖油漆与外观。")
    return u"\n".join(lines)


# ---------------------------------------------------------------- 界面

SHELL_XAML = u"""<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
    xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
    Title="材质贴面" Height="780" Width="1140" WindowStartupLocation="CenterScreen">
  <Grid Margin="10">
    <Grid.RowDefinitions>
      <RowDefinition Height="Auto"/>
      <RowDefinition Height="*"/>
      <RowDefinition Height="Auto"/>
    </Grid.RowDefinitions>

    <WrapPanel Grid.Row="0" Orientation="Horizontal" Margin="0,0,0,8">
      <TextBox x:Name="searchBox" Width="190" Height="28" Margin="0,0,8,6"/>
      <Button x:Name="btnRefresh" Content="刷新" Height="28" Margin="0,0,8,6" Padding="14,2"/>
      <Button x:Name="btnApply" Content="贴到所选墙" Height="28" Margin="0,0,8,6" Padding="14,2"/>
      <Button x:Name="btnPanel" Content="贴面（选中构件）" Height="28" Margin="0,0,6,6" Padding="14,2"/>
      <TextBlock Text="厚度" Margin="0,8,4,6" VerticalAlignment="Center" Opacity="0.8"/>
      <TextBox x:Name="boxThickness" Width="52" Height="28" Margin="0,0,10,6" Text="20"/>
      <Button x:Name="btnDiag" Content="诊断" Height="28" Margin="0,0,14,6" Padding="14,2"/>
      <CheckBox x:Name="chkColorOnly" Content="仅用主色" IsChecked="False" Margin="0,6,10,6" VerticalContentAlignment="Center"/>
    </WrapPanel>

    <Grid Grid.Row="1">
      <Grid.ColumnDefinitions>
        <ColumnDefinition Width="*"/>
        <ColumnDefinition Width="300"/>
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
                       Text="图片放在 文档\六吨工具箱\墙纸，点「刷新」后列出。"/>
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
        <TextBlock TextWrapping="Wrap" Margin="0,10,0,0" FontSize="11" Opacity="0.55"
                   Text="用法：选中墙或楼板 → 选材质/图片 → 点「贴面（选中构件）」。窗口是模态的，无法在窗口打开时拾取面，所以按选中构件处理。"/>
      </StackPanel>
    </Grid>

    <TextBlock Grid.Row="2" x:Name="status" Margin="2,8,0,0"
               Text="先在模型里选中墙或楼板，再选材质。"/>
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
        return XamlReader.Parse(
            u'<SolidColorBrush xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" Color="{0}"/>'.format(hex_value))
    except Exception:
        return None


def material_texture_path(material):
    try:
        asset_id = safe_call(lambda: material.AppearanceAssetId, None)
        if asset_id is None or asset_id.IntegerValue in (-1, 0):
            return None
        element = safe_call(lambda: doc.GetElement(asset_id), None)
        rendering = safe_call(lambda e=element: e.GetRenderingAsset(), None)
        if rendering is None:
            return None
        for name in TEXTURE_PROPS:
            prop = safe_call(lambda r=rendering, n=name: r.FindByName(n), None)
            if prop is None:
                continue
            value = safe_call(lambda p=prop: p.Value, None)
            if value and os.path.isfile(value):
                return value
    except Exception:
        return None
    return None


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
        self.box_thickness = self.FindName("boxThickness")
        self.chk_color_only = self.FindName("chkColorOnly")

        self.materials = []
        self.visible_materials = []
        self.images = []
        self.choice = None

        self.search_box.TextChanged += self._on_search
        self.material_list.SelectionChanged += self._on_material_selected
        self.image_list.SelectionChanged += self._on_image_selected
        self.FindName("btnRefresh").Click += self._on_refresh
        self.FindName("btnApply").Click += self._on_apply
        self.FindName("btnPanel").Click += self._on_panel
        self.FindName("btnDiag").Click += self._on_diag

        self._load()

    def _load(self):
        self.materials = all_materials()
        self.images = image_files()
        self._fill_materials()
        self.image_list.Items.Clear()
        for path in self.images:
            self.image_list.Items.Add(os.path.basename(path))
        self.status.Text = u"材质 {0} 个 · 墙纸图片 {1} 张".format(
            len(self.materials), len(self.images))
        self._clear_preview()

    def _fill_materials(self):
        keyword = (self.search_box.Text or u"").strip().lower()
        self.visible_materials = [m for m in self.materials
                                  if not keyword or keyword in m["name"].lower()]
        self.material_list.Items.Clear()
        for item in self.visible_materials:
            self.material_list.Items.Add(u"{0}    {1}".format(item["name"], item["color"]))

    def _clear_preview(self):
        safe_call(lambda: setattr(self.preview_image, "Source", None))
        backdrop = brush_from_hex(u"#1D2733")
        if backdrop is not None:
            safe_call(lambda: setattr(self.preview_border, "Background", backdrop))
        safe_call(lambda: setattr(self.preview_text, "Text", u""))

    def _on_search(self, sender, args):
        self._fill_materials()
        self._clear_preview()
        self.choice = None

    def _on_refresh(self, sender, args):
        self.choice = None
        self._load()

    def _on_material_selected(self, sender, args):
        index = safe_call(lambda: self.material_list.SelectedIndex, -1)
        if index is None or index < 0 or index >= len(self.visible_materials):
            return
        item = self.visible_materials[index]
        self.choice = ("material", item["id"])
        self._clear_preview()
        self.preview_text.Text = u"材质：{0}\n色值：{1}".format(item["name"], item["color"])
        material = safe_call(lambda: doc.GetElement(item["id"]), None)
        texture = material_texture_path(material) if material is not None else None
        if texture:
            source = make_bitmap(texture)
            if source is not None:
                safe_call(lambda s=source: setattr(self.preview_image, "Source", s))
                self.preview_text.Text += u"\n贴图：{0}".format(os.path.basename(texture))
        else:
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
        source = make_bitmap(path)
        if source is not None:
            safe_call(lambda s=source: setattr(self.preview_image, "Source", s))
        self.preview_text.Text = u"墙纸：{0}\n{1}".format(os.path.basename(path), path)
        self.status.Text = u"已选墙纸：{0}".format(os.path.basename(path))

    def _prepare_material(self):
        kind, value = self.choice
        color_only = bool(safe_call(lambda: self.chk_color_only.IsChecked, False))
        if kind == "material":
            return doc.GetElement(value), u""
        if color_only:
            return ensure_color_material(value), u"仅用主色"
        material, textured, note = build_textured_material(value)
        if material is None:
            return ensure_color_material(value), u"退回纯色"
        return material, note

    def _on_panel(self, sender, args):
        if not self.choice:
            forms.alert(u"先选一个材质或图片。")
            return
        try:
            thickness = float((self.box_thickness.Text or u"20").strip())
        except Exception:
            thickness = 20.0
        if thickness <= 0 or thickness > 500:
            forms.alert(u"厚度请填 1~500 之间的毫米数。")
            return

        items = selected_surfaces()
        if not items:
            forms.alert(u"先在模型里选中墙或楼板，再点「贴面（选中构件）」。")
            return

        try:
            with revit.Transaction(u"准备材质"):
                material, note = self._prepare_material()
        except Exception:
            detail = traceback.format_exc()
            logger.error(detail)
            forms.alert(u"准备材质失败：\n{0}".format(detail), title=u"材质贴面")
            return
        if material is None:
            forms.alert(u"取不到材质。")
            return
        material_id = safe_call(lambda: material.Id, None) or material

        count = 0
        done = 0
        failed = []
        for element, is_wall, is_floor in items:
            faces = faces_for_element(element, is_wall, is_floor)
            if not faces:
                failed.append(u"{0}：没有可贴的面".format(
                    safe_call(lambda e=element: e.Name, u"?")))
                continue
            element_ok = 0
            for face in faces:
                try:
                    with revit.Transaction(u"生成贴面"):
                        ok, error = build_panel_on_face(
                            element, face, material_id, thickness)
                    if ok:
                        count += 1
                        element_ok += 1
                    elif error:
                        failed.append(error)
                except Exception:
                    failed.append(traceback.format_exc().splitlines()[-1])
            if element_ok:
                done += 1

        text = u"已为 {0} 个构件生成 {1} 层贴面（{2:.0f} mm）".format(done, count, thickness)
        if note:
            text += u"；" + note
        if failed:
            text += u"；失败 {0}".format(len(failed))
            output.print_md(u"### 贴面失败\n" + u"\n".join(u"- " + f for f in failed[:10]))
        self.status.Text = text
        forms.alert(text, title=u"材质贴面")

    def _on_apply(self, sender, args):
        if not self.choice:
            forms.alert(u"先选一个材质或图片。")
            return
        items = selected_surfaces()
        walls = [element for element, is_wall, is_floor in items if is_wall]
        if not walls:
            forms.alert(u"先在模型里选中墙；楼板请用「贴面（选中构件）」。")
            return

        try:
            with revit.Transaction(u"准备材质"):
                material, note = self._prepare_material()
        except Exception:
            detail = traceback.format_exc()
            logger.error(detail)
            forms.alert(u"准备材质失败：\n{0}".format(detail), title=u"材质贴面")
            return
        if material is None:
            forms.alert(u"取不到材质。")
            return
        material_id = safe_call(lambda: material.Id, None) or material

        try:
            with revit.Transaction(u"按面刷"):
                ok, failed = paint_walls(walls, material_id)
        except Exception:
            detail = traceback.format_exc()
            logger.error(detail)
            forms.alert(u"刷面失败：\n{0}".format(detail), title=u"材质贴面")
            return

        text = u"已刷 {0} 个墙面".format(ok)
        if note:
            text += u"；" + note
        if failed:
            text += u"；{0}".format(failed[0])
        self.status.Text = text
        forms.alert(text, title=u"材质贴面")

    def _on_diag(self, sender, args):
        items = selected_surfaces()
        if not items:
            forms.alert(u"先在模型里选中墙或楼板。")
            return
        text = diagnose(items)
        output.print_md(u"### 诊断\n```\n{0}\n```".format(text))
        forms.alert(text[:1200], title=u"材质贴面 · 诊断")


def main():
    if doc is None:
        forms.alert(u"请先打开一个项目。", exitscript=True)
        return
    MaterialWindow().ShowDialog()


try:
    main()
except Exception:
    output.print_md(u"脚本出错：\n```\n{0}\n```".format(traceback.format_exc()))
    forms.alert(u"脚本出错，详见输出窗口。", title=u"材质贴面")
