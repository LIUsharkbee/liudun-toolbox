# -*- coding: utf-8 -*-
"""结构构件：板开孔 / 柱下基础 / 墙下条基 / 女儿墙 / 轴网布柱 / 沿线布墙

按「选中什么做什么」的思路：
  * 板开孔     —— 选中楼板 + 选中闭合线（模型线/详图线/墙定位线）
  * 柱下基础   —— 选中结构柱，在柱底放基础（承台/独立基础/桩）
  * 墙下条基   —— 选中墙，沿墙放线状基础
  * 女儿墙     —— 选中墙，在其顶面再起一道墙
  * 轴网布柱   —— 选中轴网，在交点放柱（用选中的柱当模板）
  * 沿线布墙   —— 选中模型线/详图线，沿线建墙

全部单事务，可 Ctrl+Z 整体撤销；失败逐条汇报。
"""

from __future__ import print_function

import traceback

from Autodesk.Revit.DB import (
    BuiltInCategory,
    BuiltInParameter,
    CurveArray,
    CurveElement,
    ElementId,
    FilteredElementCollector,
    Grid,
    Line,
    LocationCurve,
    Options,
    Transaction,
    ViewDetailLevel,
    Wall,
    XYZ,
)
from Autodesk.Revit.DB.Structure import StructuralType
from System.Collections.Generic import List as DotNetList

from pyrevit import revit, forms, script

doc = revit.doc
uidoc = revit.uidoc

MM_TO_FT = 1.0 / 304.8
FT_TO_MM = 304.8
TOL = 0.02


def text_of(value):
    if isinstance(value, unicode):
        return value
    try:
        return unicode(value)
    except Exception:
        return u"%s" % value


def cat_id(element):
    try:
        category = element.Category
        return category.Id.IntegerValue if category is not None else -1
    except Exception:
        return -1


def name_of(element, default=u""):
    try:
        return text_of(element.Name)
    except Exception:
        return default


# ---------------------------------------------------------------- 通用
def selected():
    result = []
    for element_id in uidoc.Selection.GetElementIds():
        element = doc.GetElement(element_id)
        if element is not None:
            result.append(element)
    return result


def levels_sorted():
    from Autodesk.Revit.DB import Level
    result = []
    for level in FilteredElementCollector(doc).OfClass(Level):
        result.append(level)
    result.sort(key=lambda lv: lv.Elevation)
    return result


def level_by_elevation(z):
    best = None
    for level in levels_sorted():
        if best is None or abs(level.Elevation - z) < abs(best.Elevation - z):
            best = level
    return best


def curve_of(element):
    """把图元转成一条 Curve（能转就转）。"""
    location = element.Location
    if isinstance(location, LocationCurve):
        return location.Curve
    if isinstance(element, CurveElement):
        try:
            return element.GeometryCurve
        except Exception:
            return None
    return None


def selected_curves():
    curves = []
    for element in selected():
        curve = curve_of(element)
        if curve is not None:
            curves.append(curve)
    return curves


def near(a, b, tol=TOL):
    return a.DistanceTo(b) <= tol


def assemble_loops(curves, tol=TOL):
    """线段接龙成闭合环；返回 [[p0,p1,p2,...], ...]。"""
    segments = []
    for curve in curves:
        points = []
        try:
            points = curve.Tessellate()
        except Exception:
            points = [curve.GetEndPoint(0), curve.GetEndPoint(1)]
        for index in range(len(points) - 1):
            segments.append((points[index], points[index + 1]))

    loops = []
    used = [False] * len(segments)
    for start in range(len(segments)):
        if used[start]:
            continue
        chain = [segments[start][0], segments[start][1]]
        used[start] = True
        progressed = True
        while progressed:
            progressed = False
            tail = chain[-1]
            for index in range(len(segments)):
                if used[index]:
                    continue
                a, b = segments[index]
                if near(tail, a, tol):
                    chain.append(b)
                    used[index] = True
                    progressed = True
                    break
                if near(tail, b, tol):
                    chain.append(a)
                    used[index] = True
                    progressed = True
                    break
        if len(chain) > 2 and near(chain[0], chain[-1], tol):
            loops.append(chain[:-1])
    return loops


def flatten(points, z):
    return [XYZ(point.X, point.Y, z) for point in points]


def loop_to_curve_array(points):
    array = CurveArray()
    count = len(points)
    for index in range(count):
        a = points[index]
        b = points[(index + 1) % count]
        if a.DistanceTo(b) < 1e-6:
            continue
        array.Append(Line.CreateBound(a, b))
    return array


def floor_top_z(floor):
    z = 0.0
    try:
        level = doc.GetElement(floor.LevelId)
        z = level.Elevation
    except Exception:
        try:
            z = doc.GetElement(floor.LevelId).Elevation
        except Exception:
            z = 0.0
    try:
        offset = floor.get_Parameter(BuiltInParameter.FLOOR_HEIGHTABOVELEVEL_PARAM).AsDouble()
        z += offset
    except Exception:
        pass
    try:
        structure = floor.FloorType.GetCompoundStructure()
        z += structure.GetWidth()
    except Exception:
        pass
    return z


# ---------------------------------------------------------------- 模式 1：板开孔
def mode_slab_opening(params):
    floors = [element for element in selected() if cat_id(element) == int(BuiltInCategory.OST_Floors)]
    curve_elements = [element for element in selected() if isinstance(element, CurveElement)]
    if not floors:
        return 0, [u"没有选中楼板"]
    if not curve_elements:
        return 0, [u"没有选中闭合线（请用模型线画出洞口轮廓）"]

    loops = assemble_loops([element.GeometryCurve for element in curve_elements])
    if not loops:
        return 0, [u"选中的线没能围成闭合轮廓（共有 %d 段）" % len(curve_elements)]

    made = 0
    failures = []
    for floor in floors:
        z = floor_top_z(floor)
        for points in loops:
            try:
                flat = flatten(points, z)
                array = loop_to_curve_array(flat)
                doc.Create.NewOpening(floor, array, True)
                made += 1
            except Exception as ex:
                failures.append(u"%s：%s" % (name_of(floor, u"楼板"), text_of(ex)))
    return made, failures


# ---------------------------------------------------------------- 模式 2：柱下基础
def foundation_symbols():
    from Autodesk.Revit.DB import FamilySymbol
    symbols = []
    for symbol in FilteredElementCollector(doc).OfClass(FamilySymbol):
        if cat_id(symbol) == int(BuiltInCategory.OST_StructuralFoundation):
            symbols.append(symbol)
    symbols.sort(key=lambda s: name_of(s))
    return symbols


def activate(symbol):
    try:
        if not symbol.IsActive:
            symbol.Activate()
            doc.Regenerate()
    except Exception:
        pass


def mode_column_footing(params):
    columns = [element for element in selected() if cat_id(element) == int(BuiltInCategory.OST_StructuralColumns)]
    if not columns:
        return 0, [u"没有选中结构柱"]
    symbol = params.get("symbol")
    if symbol is None:
        return 0, [u"没有可用的基础族类型（需要「结构基础」类别的族）"]

    activate(symbol)
    made = 0
    failures = []
    for column in columns:
        try:
            curve = curve_of(column)
            base_point = None
            if curve is not None:
                p0 = curve.GetEndPoint(0)
                p1 = curve.GetEndPoint(1)
                base_point = p0 if p0.Z <= p1.Z else p1
            if base_point is None:
                box = column.get_BoundingBox(None)
                base_point = XYZ((box.Min.X + box.Max.X) / 2.0, (box.Min.Y + box.Max.Y) / 2.0, box.Min.Z)
            level = level_by_elevation(base_point.Z)
            instance = doc.Create.NewFamilyInstance(base_point, symbol, level, StructuralType.Footing)
            try:
                offset = instance.get_Parameter(BuiltInParameter.FAMILY_BASE_LEVEL_OFFSET_PARAM)
                if offset is not None:
                    offset.Set(base_point.Z - level.Elevation - params["base_offset"] * MM_TO_FT)
            except Exception:
                pass
            made += 1
        except Exception as ex:
            failures.append(u"%s：%s" % (name_of(column, u"柱"), text_of(ex)))
    return made, failures


# ---------------------------------------------------------------- 模式 3：墙下条基
def mode_wall_footing(params):
    walls = [element for element in selected() if cat_id(element) == int(BuiltInCategory.OST_Walls)]
    if not walls:
        return 0, [u"没有选中墙"]
    symbol = params.get("symbol")
    if symbol is None:
        return 0, [u"没有可用的基础族类型"]

    activate(symbol)
    made = 0
    failures = []
    for wall in walls:
        try:
            curve = curve_of(wall)
            if curve is None:
                failures.append(u"%s：没有定位线" % name_of(wall, u"墙"))
                continue
            z = curve.GetEndPoint(0).Z - params["base_offset"] * MM_TO_FT
            line = Line.CreateBound(XYZ(curve.GetEndPoint(0).X, curve.GetEndPoint(0).Y, z),
                                    XYZ(curve.GetEndPoint(1).X, curve.GetEndPoint(1).Y, z))
            level = level_by_elevation(z)
            doc.Create.NewFamilyInstance(line, symbol, level, StructuralType.Footing)
            made += 1
        except Exception as ex:
            failures.append(u"%s：%s" % (name_of(wall, u"墙"), text_of(ex)))
    return made, failures


# ---------------------------------------------------------------- 模式 4：女儿墙
def mode_parapet(params):
    walls = [element for element in selected() if cat_id(element) == int(BuiltInCategory.OST_Walls)]
    if not walls:
        return 0, [u"没有选中墙（选屋顶外墙即可）"]
    wall_type = params.get("wall_type")
    if wall_type is None:
        return 0, [u"没有可用的墙类型"]

    made = 0
    failures = []
    for wall in walls:
        try:
            curve = curve_of(wall)
            if curve is None:
                continue
            top = None
            offset = wall.get_Parameter(BuiltInParameter.WALL_USER_HEIGHT_PARAM)
            base_z = curve.GetEndPoint(0).Z
            if offset is not None:
                top = base_z + offset.AsDouble()
            else:
                box = wall.get_BoundingBox(None)
                top = box.Max.Z
            line = Line.CreateBound(XYZ(curve.GetEndPoint(0).X, curve.GetEndPoint(0).Y, top),
                                    XYZ(curve.GetEndPoint(1).X, curve.GetEndPoint(1).Y, top))
            level = level_by_elevation(top)
            new_wall = Wall.Create(doc, line, wall_type.Id, level.Id,
                                   params["height"] * MM_TO_FT, 0.0, False, False)
            made += 1
        except Exception as ex:
            failures.append(u"%s：%s" % (name_of(wall, u"墙"), text_of(ex)))
    return made, failures


# ---------------------------------------------------------------- 模式 5：轴网布柱
def grid_line(grid):
    try:
        return grid.Curve
    except Exception:
        return None


def intersect_lines(a, b):
    p1 = a.GetEndPoint(0)
    p2 = a.GetEndPoint(1)
    p3 = b.GetEndPoint(0)
    p4 = b.GetEndPoint(1)
    d1 = XYZ(p2.X - p1.X, p2.Y - p1.Y, 0)
    d2 = XYZ(p4.X - p3.X, p4.Y - p3.Y, 0)
    denom = d1.X * d2.Y - d1.Y * d2.X
    if abs(denom) < 1e-9:
        return None
    dx = p3.X - p1.X
    dy = p3.Y - p1.Y
    t = (dx * d2.Y - dy * d2.X) / denom
    u = (dx * d1.Y - dy * d1.X) / denom
    if t < -0.01 or t > 1.01 or u < -0.01 or u > 1.01:
        return None
    return XYZ(p1.X + d1.X * t, p1.Y + d1.Y * t, p1.Z)


def mode_grid_columns(params):
    grids = [element for element in selected() if isinstance(element, Grid)]
    if len(grids) < 2:
        return 0, [u"请至少选中两条轴线"]
    symbol = params.get("column_symbol")
    if symbol is None:
        return 0, [u"没有可用的柱族类型（先在项目里选一根柱，或放一根柱当模板）"]

    activate(symbol)
    made = 0
    failures = []
    level = params.get("level")
    z = level.Elevation + params["base_offset"] * MM_TO_FT
    count = len(grids)
    for i in range(count):
        for j in range(i + 1, count):
            a = grid_line(grids[i])
            b = grid_line(grids[j])
            if a is None or b is None:
                continue
            point = intersect_lines(a, b)
            if point is None:
                continue
            try:
                doc.Create.NewFamilyInstance(XYZ(point.X, point.Y, z), symbol, level, StructuralType.Column)
                made += 1
            except Exception as ex:
                failures.append(u"交点 (%0.0f, %0.0f)：%s" % (point.X, point.Y, text_of(ex)))
    if made == 0 and not failures:
        failures.append(u"选中的轴线没有相交（或交点在轴线范围外）")
    return made, failures


# ---------------------------------------------------------------- 模式 6：沿线布墙
def mode_walls_on_curves(params):
    wall_type = params.get("wall_type")
    if wall_type is None:
        return 0, [u"没有可用的墙类型"]
    curves = []
    for element in selected():
        curve = curve_of(element)
        if curve is not None:
            curves.append(curve)
    if not curves:
        return 0, [u"没有选中可用的线（模型线/详图线/墙）"]

    level = params.get("level")
    made = 0
    failures = []
    for curve in curves:
        try:
            p0 = curve.GetEndPoint(0)
            p1 = curve.GetEndPoint(1)
            z = level.Elevation + params["base_offset"] * MM_TO_FT
            line = Line.CreateBound(XYZ(p0.X, p0.Y, z), XYZ(p1.X, p1.Y, z))
            Wall.Create(doc, line, wall_type.Id, level.Id, params["height"] * MM_TO_FT, 0.0, False, False)
            made += 1
        except Exception as ex:
            failures.append(u"线：%s" % text_of(ex))
    return made, failures


# ---------------------------------------------------------------- 资源
def wall_types_list():
    from Autodesk.Revit.DB import WallType as _WallType
    result = []
    for element in FilteredElementCollector(doc).OfClass(_WallType):
        result.append(element)
    result.sort(key=lambda t: name_of(t))
    return result


def column_symbols():
    from Autodesk.Revit.DB import FamilySymbol
    result = []
    for symbol in FilteredElementCollector(doc).OfClass(FamilySymbol):
        if cat_id(symbol) == int(BuiltInCategory.OST_StructuralColumns):
            result.append(symbol)
    result.sort(key=lambda s: name_of(s))
    return result


def find_element_by_name(items, name):
    for item in items:
        if name_of(item) == name:
            return item
    return items[0] if items else None


# ---------------------------------------------------------------- 事务
def run(params):
    mode = params["mode"]
    if mode == u"板开孔":
        worker = mode_slab_opening
    elif mode == u"柱下基础":
        worker = mode_column_footing
    elif mode == u"墙下条基":
        worker = mode_wall_footing
    elif mode == u"女儿墙":
        worker = mode_parapet
    elif mode == u"轴网布柱":
        worker = mode_grid_columns
    else:
        worker = mode_walls_on_curves

    transaction = Transaction(doc, u"结构构件 - %s" % mode)
    transaction.Start()
    try:
        made, failures = worker(params)
        if made == 0:
            transaction.RollBack()
            return 0, failures
        transaction.Commit()
        return made, failures
    except Exception:
        transaction.RollBack()
        raise


XAML = u"""
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="结构构件" Height="600" Width="640"
        WindowStartupLocation="CenterScreen">
  <ScrollViewer VerticalScrollBarVisibility="Auto">
    <StackPanel Margin="16">
      <TextBlock Text="结构构件" FontSize="18" FontWeight="Bold"/>
      <TextBlock Margin="0,6,0,10" Foreground="#666" TextWrapping="Wrap"
                 Text="先在模型里选中对象，再选模式点生成。每个模式都只处理选中的东西，不选就提示。"/>

      <TextBlock Text="① 模式" FontWeight="Bold" Margin="0,4,0,4"/>
      <ComboBox x:Name="modeBox" Height="28"/>

      <TextBlock Text="② 资源" FontWeight="Bold" Margin="0,10,0,4"/>
      <Grid Margin="0,0,0,4">
        <Grid.ColumnDefinitions>
          <ColumnDefinition Width="120"/>
          <ColumnDefinition Width="*"/>
        </Grid.ColumnDefinitions>
        <TextBlock Text="基础 / 柱类型" VerticalAlignment="Center"/>
        <ComboBox x:Name="symbolBox" Grid.Column="1" Height="26"/>
      </Grid>
      <Grid Margin="0,0,0,4">
        <Grid.ColumnDefinitions>
          <ColumnDefinition Width="120"/>
          <ColumnDefinition Width="*"/>
        </Grid.ColumnDefinitions>
        <TextBlock Text="墙类型" VerticalAlignment="Center"/>
        <ComboBox x:Name="wallBox" Grid.Column="1" Height="26"/>
      </Grid>
      <Grid Margin="0,0,0,4">
        <Grid.ColumnDefinitions>
          <ColumnDefinition Width="120"/>
          <ColumnDefinition Width="*"/>
        </Grid.ColumnDefinitions>
        <TextBlock Text="标高" VerticalAlignment="Center"/>
        <ComboBox x:Name="levelBox" Grid.Column="1" Height="26"/>
      </Grid>

      <TextBlock Text="③ 尺寸" FontWeight="Bold" Margin="0,10,0,4"/>
      <Grid Margin="0,0,0,4">
        <Grid.ColumnDefinitions>
          <ColumnDefinition Width="120"/>
          <ColumnDefinition Width="*"/>
          <ColumnDefinition Width="120"/>
          <ColumnDefinition Width="*"/>
        </Grid.ColumnDefinitions>
        <TextBlock Text="高度 (mm)" VerticalAlignment="Center"/>
        <TextBox x:Name="heightBox" Grid.Column="1" Height="26" Text="900"/>
        <TextBlock Text="底/顶偏移 (mm)" Grid.Column="2" VerticalAlignment="Center" Margin="8,0,0,0"/>
        <TextBox x:Name="offsetBox" Grid.Column="3" Height="26" Text="0"/>
      </Grid>

      <TextBlock x:Name="hintText" Margin="0,10,0,6" Foreground="#444" TextWrapping="Wrap"
                 Text="提示：板开孔要先画好洞口轮廓线（模型线闭合成环）。"/>
      <StackPanel Orientation="Horizontal">
        <Button x:Name="btnRun" Content="生成" Width="100" Height="30" FontWeight="Bold" Margin="0,0,8,0"/>
        <Button x:Name="btnClose" Content="关闭" Width="80" Height="30"/>
      </StackPanel>
    </StackPanel>
  </ScrollViewer>
</Window>
"""

MODES = [u"板开孔", u"柱下基础", u"墙下条基", u"女儿墙", u"轴网布柱", u"沿线布墙"]


class StructWindow(forms.WPFWindow):
    def __init__(self):
        forms.WPFWindow.__init__(self, XAML, literal_string=True)
        self.foundations = foundation_symbols()
        self.columns = column_symbols()
        self.walls = wall_types_list()
        self.levels = levels_sorted()

        for mode in MODES:
            self.modeBox.Items.Add(mode)
        self.modeBox.SelectedIndex = 0
        self.modeBox.SelectionChanged += self._on_mode

        for symbol in self.foundations:
            self.symbolBox.Items.Add(name_of(symbol, u"<基础>"))
        for symbol in self.columns:
            self.symbolBox.Items.Add(name_of(symbol, u"<柱>"))
        if self.symbolBox.Items.Count:
            self.symbolBox.SelectedIndex = 0

        for wall_type in self.walls:
            self.wallBox.Items.Add(name_of(wall_type, u"<墙>"))
        if self.wallBox.Items.Count:
            self.wallBox.SelectedIndex = 0

        for level in self.levels:
            self.levelBox.Items.Add(u"%s (+%0.3f)" % (name_of(level), level.Elevation * FT_TO_MM / 1000.0))
        if self.levelBox.Items.Count:
            self.levelBox.SelectedIndex = 0

        self.btnRun.Click += self._on_run
        self.btnClose.Click += self._on_close
        self._on_mode(None, None)

    def _num(self, box, default):
        try:
            return float(box.Text)
        except Exception:
            return default

    def _on_mode(self, sender, args):
        mode = MODES[max(0, self.modeBox.SelectedIndex)]
        hints = {
            u"板开孔": u"选中楼板 + 选中闭合线（模型线画洞口轮廓），每块板都会开同样的洞。",
            u"柱下基础": u"选中结构柱，在柱底放基础（承台 / 独立基础 / 桩，取决于上面选的类型）。",
            u"墙下条基": u"选中墙，沿墙中线放线状基础，底部按「底/顶偏移」下移。",
            u"女儿墙": u"选中屋顶外墙，在墙顶再起一道墙（高度用下面参数，默认 900）。",
            u"轴网布柱": u"选中轴网（至少两条），在交点放柱；柱类型取上面选的族类型。",
            u"沿线布墙": u"选中模型线/详图线，沿线建墙，标高取下面选的标高。",
        }
        self.hintText.Text = hints.get(mode, u"")

    def _params(self):
        mode = MODES[max(0, self.modeBox.SelectedIndex)]
        symbol = None
        if self.symbolBox.Items.Count:
            index = max(0, self.symbolBox.SelectedIndex)
            pool = self.foundations + self.columns
            if index < len(pool):
                symbol = pool[index]
        wall_type = None
        if self.wallBox.Items.Count:
            index = max(0, self.wallBox.SelectedIndex)
            if index < len(self.walls):
                wall_type = self.walls[index]
        level = None
        if self.levelBox.Items.Count:
            index = max(0, self.levelBox.SelectedIndex)
            if index < len(self.levels):
                level = self.levels[index]

        return {
            "mode": mode,
            "symbol": symbol,
            "column_symbol": symbol,
            "wall_type": wall_type,
            "level": level,
            "height": self._num(self.heightBox, 900.0),
            "base_offset": self._num(self.offsetBox, 0.0),
        }

    def _on_run(self, sender, args):
        params = self._params()
        try:
            made, failures = run(params)
        except Exception:
            tb = traceback.format_exc()
            forms.alert(u"生成失败：\n{0}".format(tb[-800:]), title=u"结构构件")
            return
        lines = [u"完成 %d 个。" % made]
        if failures:
            lines.append(u"未完成 %d 条：\n%s" % (len(failures), u"\n".join(failures[:12])))
        self.hintText.Text = u"\n".join(lines)
        forms.alert(u"\n".join(lines), title=u"结构构件")

    def _on_close(self, sender, args):
        self.Close()


def main():
    if doc is None:
        forms.alert(u"请先打开一个项目。", exitscript=True)
    window = StructWindow()
    window.ShowDialog()


try:
    main()
except Exception:
    tb = traceback.format_exc()
    script.get_output().print_md(u"脚本出错：\n```\n{0}\n```".format(tb))
    forms.alert(u"脚本出错：\n{0}".format(tb[-800:]), title=u"结构构件")
