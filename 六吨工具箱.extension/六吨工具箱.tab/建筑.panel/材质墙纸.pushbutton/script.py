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
    AppearanceAssetElement,
    Color,
    ElementId,
    FilteredElementCollector,
    Material,
    Wall,
    WallType,
)
from Autodesk.Revit.DB.Visual import AssetType
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
material_note = [u""]

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



TEXTURE_BASE_PROPS = ("unifiedbitmap_Bitmap", "UnifiedBitmapSchema", "bitmap")


def find_texture_base_asset():
    """从系统外观资源里找一个含位图属性的，返回 (asset, 属性名)。"""
    assets = safe_call(
        lambda: list(doc.Application.GetAssets(AssetType.Appearance)), []) or []
    for asset in assets:
        for name in TEXTURE_BASE_PROPS:
            if safe_call(lambda a=asset, n=name: a.FindByName(n), None) is not None:
                return asset, name
    return None, None


def _bitmap_prop_of(asset):
    for name in TEXTURE_BASE_PROPS:
        prop = safe_call(lambda a=asset, n=name: a.FindByName(n), None)
        if prop is not None:
            return prop, name
    return None, None


def _set_double(asset, name, value):
    prop = safe_call(lambda a=asset, n=name: a.FindByName(n), None)
    if prop is not None:
        safe_call(lambda p=prop, v=value: setattr(p, "Value", float(v)))


def find_project_texture_asset():
    """项目里已有、且带位图属性的外观资源；优先挑已经有图的。"""
    fallback = None
    for element in safe_call(
            lambda: list(FilteredElementCollector(doc).OfClass(AppearanceAssetElement)), []) or []:
        asset = safe_call(lambda e=element: e.GetRenderingAsset(), None)
        if asset is None:
            continue
        prop, name = _bitmap_prop_of(asset)
        if prop is None:
            continue
        value = safe_call(lambda p=prop: p.Value, u"") or u""
        if value.strip():
            return element, name, True
        if fallback is None:
            fallback = (element, name, False)
    return fallback if fallback else (None, None, False)


TEXTURE_CACHE = os.path.join(os.environ.get("LOCALAPPDATA", ""), "liudun-textures")


def ensure_color_material(image_path):
    """只取图片主色做材质，不挂任何贴图资源（规避渲染黑的问题）。"""
    base = os.path.splitext(os.path.basename(image_path))[0]
    name = u"纯色-" + base

    material = find_material_by_name(name)
    if material is None:
        created = Material.Create(doc, name)
        material = safe_call(lambda: doc.GetElement(created), None)
    if material is None:
        return None

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

    safe_call(lambda: setattr(material, "Color", Color(color[0], color[1], color[2])))
    safe_call(lambda: setattr(material, "UseRenderAppearanceForShading", True))

    # 关键：真实样式看的是外观资源；没有外观就是黑的，所以建一个纯色外观
    element = build_solid_appearance(color, u"外观-纯色-" + base)
    if element is not None:
        safe_call(lambda m=material, e=element: setattr(m, "AppearanceAssetId", e.Id))
    return material


GENERIC_COLOR_PROPS = ("generic_diffuse", "generic_transparency", "generic_glossiness")


def find_generic_asset():
    """找一个带 generic_diffuse 的系统外观资源（纯色用）。"""
    assets = safe_call(
        lambda: list(doc.Application.GetAssets(AssetType.Appearance)), []) or []
    for asset in assets:
        if safe_call(lambda a=asset: a.FindByName("generic_diffuse"), None) is not None:
            return asset
    return None


def whiten_asset_colors(asset):
    """把外观资源里所有颜色类属性刷成白色，避免贴图被乘黑。"""
    if asset is None:
        return 0
    count = safe_call(lambda a=asset: a.Size, 0) or 0
    changed = 0
    for index in range(count):
        prop = safe_call(lambda a=asset, i=index: a.Get(i), None)
        if prop is None:
            continue
        name = safe_call(lambda p=prop: p.Name, u"") or u""
        lowered = name.lower()
        if not (u"color" in lowered or u"diffuse" in lowered):
            continue
        ok = safe_call(
            lambda p=prop: p.SetValueAsColor(Color(255, 255, 255)), None)
        if ok is None:
            changed += 1
    return changed


def build_solid_appearance(color_rgb, name):
    """创建纯色外观资源，真实样式下按颜色显示。"""
    base = find_generic_asset()
    if base is None:
        return None
    element = safe_call(
        lambda: AppearanceAssetElement.Create(doc, name, base), None)
    if element is None:
        element = safe_call(
            lambda: AppearanceAssetElement.GetAppearanceAssetElementByName(doc, name), None)
    if element is None:
        return None
    rendering = safe_call(lambda e=element: e.GetRenderingAsset(), None)
    if rendering is None:
        return element
    prop = safe_call(lambda r=rendering: r.FindByName("generic_diffuse"), None)
    if prop is not None:
        safe_call(lambda p=prop, c=Color(color_rgb[0], color_rgb[1], color_rgb[2]):
                  p.SetValueAsColor(c))
    return element


def paint_walls(walls, material_id):
    """用油漆工具直接刷墙面（只刷竖直面），返回 (成功面数, 失败列表)。"""
    from Autodesk.Revit.DB import Options, PlanarFace

    options = Options()
    options.ComputeReferences = True
    painted = 0
    failed = []

    for wall in walls:
        faces = 0
        try:
            geometry = safe_call(lambda w=wall: w.get_Geometry(options), None)
            if geometry is None:
                failed.append(u"取不到几何")
                continue

            for item in geometry:
                solid = item
                face_list = safe_call(lambda s=solid: s.Faces, None)
                if face_list is None:
                    continue
                for face in face_list:
                    try:
                        planar = face if isinstance(face, PlanarFace) else None
                        if planar is None:
                            continue
                        normal = safe_call(lambda f=planar: f.FaceNormal, None)
                        if normal is None:
                            continue
                        if abs(safe_call(lambda n=normal: n.Z, 0.0)) > 0.1:
                            continue      # 跳过顶面/底面，只刷竖直面
                        doc.Paint(wall.Id, face, material_id)
                        faces += 1
                    except Exception:
                        continue
            if faces:
                painted += faces
            else:
                failed.append(u"没有可刷的竖直面")
        except Exception:
            failed.append(traceback.format_exc().splitlines()[-1])
    return painted, failed


def stage_texture(image_path):
    """把图片复制到纯英文路径，避免 Revit 渲染器读不了中文路径。"""
    try:
        if not os.path.isdir(TEXTURE_CACHE):
            os.makedirs(TEXTURE_CACHE)
        ext = os.path.splitext(image_path)[1].lower() or ".png"
        import hashlib
        digest = hashlib.md5(image_path.encode("utf-8")).hexdigest()[:12]
        target = os.path.join(TEXTURE_CACHE, "tex_" + digest + ext)
        source_size = os.path.getsize(image_path)
        if (not os.path.isfile(target)) or os.path.getsize(target) != source_size:
            with open(image_path, "rb") as src:
                data = src.read()
            with open(target, "wb") as dst:
                dst.write(data)
        return target
    except Exception:
        return image_path


def build_textured_material(image_path):
    """创建或复用带图片纹理的材质，返回 (material, 是否写入纹理, 说明)。"""
    base = os.path.splitext(os.path.basename(image_path))[0]
    mat_name = u"墙纸-" + base
    asset_name = u"外观-墙纸-" + base

    material = find_material_by_name(mat_name)

    element, prop_name, had_texture = find_project_texture_asset()
    created_new = False
    if element is not None:
        # 复制项目里已有的外观资源，保留它的全部纹理参数（缩放、映射等）
        dup = safe_call(lambda e=element, n=asset_name: e.Duplicate(n), None)
        if dup is None:
            dup = safe_call(
                lambda: AppearanceAssetElement.GetAppearanceAssetElementByName(doc, asset_name), None)
        element = dup
    else:
        system_asset, prop_name = find_texture_base_asset()
        if system_asset is not None:
            element = safe_call(
                lambda: AppearanceAssetElement.Create(doc, asset_name, system_asset), None)
            created_new = True

    if element is None or prop_name is None:
        return material, False, u"项目里没有可复制的贴图外观资源"

    rendering = safe_call(lambda e=element: e.GetRenderingAsset(), None)
    if rendering is None:
        return material, False, u"取不到外观资源"

    prop = safe_call(lambda r=rendering, n=prop_name: r.FindByName(n), None)
    if prop is None:
        return material, False, u"外观资源里没有位图属性"

    staged = stage_texture(image_path)
    safe_call(lambda p=prop, v=staged: setattr(p, "Value", v))
    written = safe_call(lambda p=prop: p.Value, u"")
    if not written:
        return material, False, u"位图属性写入失败（路径或只读）"

    # 关键：新建/复制后的纹理缩放可能是 0，会渲染成纯黑
    _set_double(rendering, "unifiedbitmap_RealWorldScaleX", 1000.0)
    _set_double(rendering, "unifiedbitmap_RealWorldScaleY", 1000.0)
    _set_double(rendering, "unifiedbitmap_RealWorldOffsetX", 0.0)
    _set_double(rendering, "unifiedbitmap_RealWorldOffsetY", 0.0)
    _set_double(rendering, "unifiedbitmap_Rotation", 0.0)

    # 位图外观里所有颜色属性刷白，避免贴图被乘黑
    whiten_asset_colors(rendering)

    if material is None:
        created = Material.Create(doc, mat_name)
        material = safe_call(lambda: doc.GetElement(created), None)
        if material is None:
            return None, False, u"创建材质失败"
    safe_call(lambda m=material, e=element: setattr(m, "AppearanceAssetId", e.Id))
    safe_call(lambda m=material: setattr(m, "Color", Color(255, 255, 255)))
    safe_call(lambda m=material: setattr(m, "UseRenderAppearanceForShading", True))

    note = u"已写入图片纹理（复制自有贴图的资源）" if had_texture \
        else (u"已写入图片纹理（新建资源）" if created_new else u"已写入图片纹理")
    return material, True, note


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

    # 注意：Material.Create 返回的是 ElementId，不是 Material 对象
    created = Material.Create(doc, name)
    material = safe_call(lambda: doc.GetElement(created), None)
    if material is None:
        material = created  # 兜底：交给调用方判断

    if hasattr(material, "Id"):
        safe_call(lambda: setattr(material, "Color", Color(color[0], color[1], color[2])))
        if try_set_texture(material, image_path):
            material_texture_state[0] = True
    return material


# ---------------------------------------------------------------- 贴材质

def apply_material(walls, material, per_wall_type, exterior_only):
    """把材质写到墙的面层，返回 (成功数, 失败列表)。"""
    ok = 0
    failed = []
    material_id = safe_call(lambda: material.Id, None)
    if material_id is None:
        material_id = material      # 已经是 ElementId
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
    Title="材质库 / 墙纸" Height="780" Width="1120" WindowStartupLocation="CenterScreen">
  <Grid Margin="10">
    <Grid.RowDefinitions>
      <RowDefinition Height="Auto"/>
      <RowDefinition Height="*"/>
      <RowDefinition Height="Auto"/>
    </Grid.RowDefinitions>

    <WrapPanel Grid.Row="0" Orientation="Horizontal" Margin="0,0,0,8">
      <TextBox x:Name="searchBox" Width="200" Height="28" Margin="0,0,8,6"/>
      <Button x:Name="btnRefresh" Content="刷新" Height="28" Margin="0,0,8,6" Padding="14,2"/>
      <Button x:Name="btnApply" Content="贴到所选墙" Height="28" Margin="0,0,8,6" Padding="14,2"/>
      <Button x:Name="btnDiag" Content="诊断选中墙" Height="28" Margin="0,0,14,6" Padding="14,2"/>
      <CheckBox x:Name="chkExterior" Content="只改外表面层" IsChecked="True" Margin="0,6,10,6" VerticalContentAlignment="Center"/>
      <CheckBox x:Name="chkPerWall" Content="每墙独立类型" IsChecked="False" Margin="0,6,10,6" VerticalContentAlignment="Center"/>
      <CheckBox x:Name="chkColorOnly" Content="仅用主色" IsChecked="False" Margin="0,6,10,6" VerticalContentAlignment="Center"/>
      <CheckBox x:Name="chkPaint" Content="按面刷（推荐）" IsChecked="True" Margin="0,6,10,6" VerticalContentAlignment="Center"/>
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



def diagnose_walls(walls):
    """把墙面材质的真实情况打出来，定位“为什么还是黑”。"""
    from Autodesk.Revit.DB import Options, PlanarFace

    lines = []
    options = Options()
    options.ComputeReferences = True

    for wall in walls[:3]:
        name = safe_call(lambda w=wall: w.Name, u"?")
        lines.append(u"■ 墙：{0}（Id {1}）".format(
            name, safe_call(lambda w=wall: w.Id.IntegerValue, -1)))

        wall_type = safe_call(lambda w=wall: w.WallType, None)
        type_name = safe_call(lambda t=wall_type: t.Name, u"?")
        lines.append(u"  类型：{0}".format(type_name))

        structure = safe_call(lambda t=wall_type: t.GetCompoundStructure(), None)
        layers = safe_call(lambda s=structure: list(s.GetLayers()), []) or []
        lines.append(u"  结构层数：{0}".format(len(layers)))
        for index, layer in enumerate(layers):
            mid = safe_call(lambda l=layer: l.MaterialId, None)
            material = safe_call(lambda i=mid: doc.GetElement(i), None) if mid else None
            mname = safe_call(lambda m=material: m.Name, u"(无)") if material else u"(无)"
            width = safe_call(lambda l=layer: l.Width, 0) or 0
            lines.append(u"    层{0}：{1} mm · 材质 {2}".format(index + 1, int(width), mname))

        solids = 0
        painted_faces = 0
        total_faces = 0
        materials_on_faces = []
        geometry = safe_call(lambda w=wall: w.get_Geometry(options), None)
        if geometry is not None:
            for item in geometry:
                faces = safe_call(lambda s=item: s.Faces, None)
                if faces is None:
                    continue
                solids += 1
                for face in faces:
                    total_faces += 1
                    is_painted = safe_call(lambda f=face: doc.IsPainted(wall.Id, f), False)
                    if is_painted:
                        painted_faces += 1
                        mid = safe_call(lambda f=face: doc.GetPaintedMaterial(wall.Id, f), None)
                        material = safe_call(lambda i=mid: doc.GetElement(i), None) if mid else None
                        mname = safe_call(lambda m=material: m.Name, u"?") if material else u"?"
                        materials_on_faces.append(mname)
        lines.append(u"  几何体：{0} 个实体，{1} 个面（其中 {2} 个已油漆）".format(
            solids, total_faces, painted_faces))
        if materials_on_faces:
            lines.append(u"  油漆材质：{0}".format(u"、".join(sorted(set(materials_on_faces))[:6])))

        appearance = safe_call(lambda w=wall: w.Category, None)
        lines.append(u"  类别：{0}".format(safe_call(lambda c=appearance: c.Name, u"?")))

        if layers:
            mid = safe_call(lambda l=layers[0]: l.MaterialId, None)
            material = safe_call(lambda i=mid: doc.GetElement(i), None) if mid else None
            if material is not None:
                asset_id = safe_call(lambda m=material: m.AppearanceAssetId, None)
                asset_element = safe_call(lambda i=asset_id: doc.GetElement(i), None) if asset_id else None
                rendering = safe_call(lambda e=asset_element: e.GetRenderingAsset(), None) if asset_element else None
                lines.append(u"  外观资源：{0}".format(
                    safe_call(lambda e=asset_element: e.Name, u"(无)")))
                if rendering is not None:
                    count = safe_call(lambda a=rendering: a.Size, 0) or 0
                    for index in range(min(count, 14)):
                        prop = safe_call(lambda a=rendering, i=index: a.Get(i), None)
                        if prop is None:
                            continue
                        pname = safe_call(lambda p=prop: p.Name, u"?")
                        ptype = safe_call(lambda p=prop: p.Type, None)
                        value = safe_call(lambda p=prop: p.Value, u"")
                        lines.append(u"    {0} = {1}".format(pname, str(value)[:60]))
                else:
                    lines.append(u"    （没有外观资源，真实样式会是黑的）")
        lines.append(u"")

    lines.append(u"提示：如果「已油漆」数量大于 0，说明墙面被油漆覆盖；")
    lines.append(u"      这时「按面刷」会直接替换油漆，但需要勾选并按面刷模式执行。")
    return u"\n".join(lines)


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
        self.chk_color_only = self.FindName("chkColorOnly")
        self.chk_paint = self.FindName("chkPaint")

        self.materials = []
        self.visible_materials = []
        self.images = []
        self.choice = None

        self.search_box.TextChanged += self._on_search
        self.material_list.SelectionChanged += self._on_material_selected
        self.image_list.SelectionChanged += self._on_image_selected
        self.FindName("btnRefresh").Click += self._on_refresh
        self.FindName("btnApply").Click += self._on_apply
        self.FindName("btnDiag").Click += self._on_diag

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
    def _on_diag(self, sender, args):
        walls = selected_walls()
        if not walls:
            forms.alert(u"先在模型里选中一面墙。")
            return
        text = diagnose_walls(walls)
        output.print_md(u"### 墙面材质诊断\n```\n{0}\n```".format(text))
        forms.alert(text[:1200], title=u"诊断结果")

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
        color_only = bool(safe_call(lambda: self.chk_color_only.IsChecked, False))
        paint_mode = bool(safe_call(lambda: self.chk_paint.IsChecked, True))

        if per_wall:
            answer = forms.alert(
                u"「每个墙单独类型」会复制墙类型并换类型，\n"
                u"有连接/附着的墙可能被 Revit 打断连接（会弹「无法使图元保持连接」）。\n\n"
                u"建议先取消勾选，只改现有墙类型的面层材质。\n\n仍要继续吗？",
                title=u"材质库", yes=True, no=True)
            if answer not in (True, u"Yes", u"yes", u"是"):
                return
        if not per_wall:
            answer = forms.alert(
                u"将修改墙类型的结构层材质（影响该类型所有墙）。继续吗？",
                title=u"材质库", yes=True, no=True)
            if answer not in (True, u"Yes", u"yes", u"是"):
                return

        ok = 0
        failed = []
        first_error = None
        material_note[0] = u""
        try:
            from Autodesk.Revit.DB import SubTransaction

            with revit.Transaction(u"贴材质 / 墙纸"):
                if paint_mode:
                    if kind == "image":
                        if color_only:
                            material = ensure_color_material(value)
                            material_note[0] = u"仅用主色"
                        else:
                            material, textured, note = build_textured_material(value)
                            material_texture_state[0] = textured
                            material_note[0] = note
                            if material is None:
                                material = ensure_color_material(value)
                                material_note[0] = u"退回纯色"
                    else:
                        material = doc.GetElement(value)

                    if material is None:
                        forms.alert(u"取不到材质。")
                        return
                    material_id = safe_call(lambda: material.Id, None)
                    if material_id is None:
                        material_id = material
                    ok, failed = paint_walls(walls, material_id)
                    text = u"已刷 {0} 个墙面".format(ok)
                    if material_note[0]:
                        text += u"；" + material_note[0]
                    if failed:
                        text += u"；失败 {0}".format(len(failed))
                    self.status.Text = text
                    return

                if kind == "image":
                    material_texture_state[0] = False
                    if color_only:
                        material = ensure_color_material(value)
                        material_note[0] = u"仅用主色（不贴纹理）"
                    else:
                        material, textured, note = build_textured_material(value)
                        material_texture_state[0] = textured
                        material_note[0] = note
                        if material is None:
                            material = ensure_color_material(value)
                            material_note[0] = u"退回纯色（纹理失败）"
                else:
                    material = doc.GetElement(value)

                if material is None:
                    forms.alert(u"取不到材质。")
                    return

                for wall in walls:
                    sub = SubTransaction(doc)
                    sub.Start()
                    try:
                        sub_ok, sub_failed = apply_material(
                            [wall], material, per_wall, exterior)
                        if sub_ok:
                            sub.Commit()
                            ok += 1
                        else:
                            sub.RollBack()
                            failed.extend(sub_failed)
                    except Exception:
                        sub.RollBack()
                        detail = traceback.format_exc().splitlines()[-1]
                        if first_error is None:
                            first_error = detail
                        failed.append(u"{0}：{1}".format(
                            safe_call(lambda w=wall: w.Name, u"?"), detail))
        except Exception:
            detail = traceback.format_exc()
            logger.error(detail)
            forms.alert(u"贴材质失败：\n{0}".format(detail), title=u"材质库")
            return

        text = u"成功 {0} / {1} 面墙".format(ok, len(walls))
        if kind == "image":
            text += u"；" + (material_note[0] if material_note[0]
                             else (u"已写入图片纹理" if material_texture_state[0]
                                   else u"仅生成同色材质"))
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
