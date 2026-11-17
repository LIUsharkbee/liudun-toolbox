# -*- coding: utf-8 -*-
"""推拉 —— 面挤出 / 挖凹槽（SketchUp 式推拉）

用法：
  1. 选模式（挤出面层 / 挖凹槽），设厚度、材质；
  2. 点「开始」，窗口关闭；
  3. 鼠标移到要处理的面上（Revit 会高亮），左键点面；
  4. 在面上点两个点，定出矩形范围；
  5. 自动挤出 / 挖槽，完成后接着做下一个，Esc 结束。

挤出面层 = 在面上贴一层厚度为 t 的实体（常规模型，可贴材质）。
挖凹槽   = 生成一个向内挖的实体，并用「剪切」把它从宿主里扣掉（宿主不支持剪切时会提示）。
"""

import traceback

import clr

clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

from System.Collections.Generic import List as DotNetList

from Autodesk.Revit.DB import (
    BuiltInCategory,
    BuiltInParameter,
    CurveLoop,
    DirectShape,
    ElementId,
    FilteredElementCollector,
    GeometryCreationUtilities,
    GeometryObject,
    IFailuresPreprocessor,
    Line,
    Material,
    Options,
    Plane,
    SolidOptions,
    SolidSolidCutUtils,
    Transaction,
    ViewDetailLevel,
    XYZ,
)
from Autodesk.Revit.DB import FailureHandlingOptions, FailureProcessingResult, FailureSeverity
from Autodesk.Revit.UI.Selection import ObjectType

from pyrevit import forms, revit, script

doc = revit.doc
uidoc = revit.uidoc
logger = script.get_logger()

FT_TO_MM = 304.8
MM_TO_FT = 1.0 / 304.8

LOG_PATH = r"C:\Users\cyl\Documents\deepseek-harness\default-workspace\_推拉日志.txt"


def write_log(text):
    try:
        f = open(LOG_PATH, "ab")
        try:
            f.write((u"{0}\n".format(text)).encode("utf-8"))
        finally:
            f.close()
    except Exception:
        pass


def type_name(element):
    if element is None:
        return u"?"
    try:
        return element.Name
    except Exception:
        pass
    try:
        return element.get_Name()
    except Exception:
        return u"<{0}>".format(element.Id.IntegerValue)


def all_materials():
    result = []
    try:
        for element in FilteredElementCollector(doc).OfClass(Material):
            result.append(element)
    except Exception:
        pass
    result.sort(key=type_name)
    return result


def face_at(reference):
    """从 Reference 拿到 Face（PickObject 点面时用）。"""
    element = doc.GetElement(reference)
    face = None
    try:
        face = element.GetGeometryObjectFromReference(reference)
    except Exception:
        face = None
    if face is None:
        opts = Options()
        opts.ComputeReferences = True
        opts.DetailLevel = ViewDetailLevel.Fine
        try:
            geom = element.get_Geometry(opts)
            for obj in geom:
                for f in obj.Faces:
                    if f.Reference is not None and \
                            f.Reference.ConvertToStableRepresentation(doc) == \
                            reference.ConvertToStableRepresentation(doc):
                        return element, f
        except Exception:
            pass
    return element, face


def face_normal(face, point):
    try:
        proj = face.Project(point)
        if proj is not None:
            return face.ComputeNormal(proj.UVPoint)
    except Exception:
        pass
    try:
        return face.ComputeNormal(None)
    except Exception:
        pass
    return XYZ(0, 0, 1)


def face_basis(normal):
    n = XYZ(normal.X, normal.Y, normal.Z)
    if n.GetLength() < 1e-9:
        n = XYZ(0, 0, 1)
    n = n.Normalize()
    helper = XYZ(0, 0, 1) if abs(n.Z) < 0.9 else XYZ(1, 0, 0)
    u = n.CrossProduct(helper)
    if u.GetLength() < 1e-9:
        u = n.CrossProduct(XYZ(1, 0, 0))
    u = u.Normalize()
    v = n.CrossProduct(u).Normalize()
    return u, v


def project_on_plane(point, origin, u, v):
    d = point - origin
    return d.DotProduct(u), d.DotProduct(v)


def point_from_uv(origin, u, v, uu, vv):
    return origin + u * uu + v * vv


def rect_corners(origin, u, v, u1, v1, u2, v2):
    a = point_from_uv(origin, u, v, u1, v1)
    b = point_from_uv(origin, u, v, u2, v1)
    c = point_from_uv(origin, u, v, u2, v2)
    d = point_from_uv(origin, u, v, u1, v2)
    return [a, b, c, d]


def loop_from_points(points, shift):
    """把四个点（已含 shift 方向）做成闭合 CurveLoop。"""
    loop = CurveLoop()
    count = len(points)
    for i in range(count):
        p1 = points[i] + shift
        p2 = points[(i + 1) % count] + shift
        if p1.DistanceTo(p2) < 1e-6:
            continue
        loop.Append(Line.CreateBound(p1, p2))
    return loop


def outward(normal, face_point, host):
    """判断面法向是否朝外：法向朝向「宿主包围盒中心」的反方向即朝外。"""
    try:
        box = host.get_BoundingBox(None)
        if box is not None:
            center = (box.Min + box.Max) * 0.5
            if (face_point - center).DotProduct(normal) < 0:
                return normal * -1.0
    except Exception:
        pass
    return normal


class WarningSuppressor(IFailuresPreprocessor):
    def PreprocessFailures(self, accessor):
        try:
            for message in accessor.GetFailureMessages():
                if message.GetSeverity() == FailureSeverity.Warning:
                    accessor.DeleteWarning(message)
        except Exception:
            pass
        return FailureProcessingResult.Continue


def solid_of(loop_list, direction, distance, material_id):
    options = None
    try:
        if material_id is not None and material_id.IntegerValue > 0:
            options = SolidOptions(material_id, ElementId.InvalidElementId)
    except Exception:
        options = None
    if options is not None:
        return GeometryCreationUtilities.CreateExtrusionGeometry(
            loop_list, direction, distance, options)
    return GeometryCreationUtilities.CreateExtrusionGeometry(
        loop_list, direction, distance)


def make_direct_shape(shape_list, name):
    category_id = ElementId(BuiltInCategory.OST_GenericModel)
    shape = DirectShape.CreateElement(doc, category_id)
    try:
        shape.SetShape(shape_list)
    except Exception:
        shape.SetShape(shape_list)
    try:
        shape.Name = name
    except Exception:
        pass
    return shape


def extrude_on_face(host, face, point, p1, p2, params):
    """在面上挤出厚度为 t 的面层（朝外）。"""
    normal = face_normal(face, point)
    n = outward(normal, point, host)
    u, v = face_basis(n)
    origin = point
    u1, v1 = project_on_plane(p1, origin, u, v)
    u2, v2 = project_on_plane(p2, origin, u, v)
    if abs(u1 - u2) < 0.02 or abs(v1 - v2) < 0.02:
        return None, u"矩形太小（两个点几乎重合）"
    corners = rect_corners(origin, u, v, u1, v1, u2, v2)
    thickness = max(1.0, params["thickness_mm"]) * MM_TO_FT
    loop = loop_from_points(corners, XYZ(0, 0, 0))
    loops = DotNetList[CurveLoop]()
    loops.Add(loop)
    solid = solid_of(loops, n, thickness, params.get("material_id"))
    shape_list = DotNetList[GeometryObject]()
    shape_list.Add(solid)
    shape = make_direct_shape(shape_list, params.get("label") or u"推拉-面层")
    return shape, None


def carve_recess(host, face, point, p1, p2, params):
    """挖凹槽：向内挤出实体，再从宿主里剪切掉。"""
    normal = face_normal(face, point)
    n = outward(normal, point, host)
    inward_dir = n * -1.0
    depth = max(1.0, params["thickness_mm"]) * MM_TO_FT

    u, v = face_basis(n)
    u1, v1 = project_on_plane(p1, point, u, v)
    u2, v2 = project_on_plane(p2, point, u, v)
    if abs(u1 - u2) < 0.02 or abs(v1 - v2) < 0.02:
        return None, u"矩形太小（两个点几乎重合）"

    corners = rect_corners(point, u, v, u1, v1, u2, v2)
    # 稍微往外偏一点，保证切得干净
    shift = n * (0.5 * MM_TO_FT)
    loop = loop_from_points(corners, shift)
    loops = DotNetList[CurveLoop]()
    loops.Add(loop)
    solid = solid_of(loops, inward_dir, depth, params.get("material_id"))
    shape_list = DotNetList[GeometryObject]()
    shape_list.Add(solid)
    cutter = make_direct_shape(shape_list, u"推拉-凹槽")

    try:
        SolidSolidCutUtils.AddCutBetweenSolids(doc, host, cutter)
        return cutter, None
    except Exception as ex:
        write_log(u"剪切失败：{0}".format(ex))
        try:
            doc.Delete(cutter.Id)
        except Exception:
            pass
        return None, (u"这块构件不支持自动剪切（{0}）。\n"
                      u"可以改用「洞口」或对该构件先创建空心族。".format(ex))


def pick_face():
    """让用户点一个面，返回 (host, face, point)。"""
    reference = uidoc.Selection.PickObject(
        ObjectType.PointOnElement, u"点选要推拉的表面（Esc 结束）")
    host, face = face_at(reference)
    if host is None or face is None:
        return None, None, None
    return host, face, reference.GlobalPoint


SHELL_XAML = """
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="推拉" Height="470" Width="470"
        WindowStartupLocation="CenterScreen">
  <ScrollViewer VerticalScrollBarVisibility="Auto">
    <StackPanel Margin="14">
      <TextBlock Text="推拉（SketchUp 式）" FontSize="18" FontWeight="Bold" Margin="0,0,0,8"/>
      <TextBlock TextWrapping="Wrap" Foreground="#666666" Margin="0,0,0,10"
                 Text="先在面上点一下（Revit 会高亮该面），再在面上点两个点圈出矩形范围，自动挤出或挖槽。"/>
      <TextBlock Text="① 动作" FontWeight="Bold" Margin="0,6,0,4"/>
      <ComboBox x:Name="modeBox" Height="26">
        <ComboBoxItem Content="挤出面层（在面上加一层）" IsSelected="True"/>
        <ComboBoxItem Content="挖凹槽（从构件里扣掉）"/>
      </ComboBox>
      <TextBlock Text="② 厚度 / 深度 (mm)" FontWeight="Bold" Margin="0,10,0,4"/>
      <TextBox x:Name="thickBox" Text="20" Height="26"/>
      <TextBlock Text="③ 材质（可选）" FontWeight="Bold" Margin="0,10,0,4"/>
      <ComboBox x:Name="matBox" Height="26"/>
      <TextBlock x:Name="tipText" TextWrapping="Wrap" Foreground="#666666" Margin="0,10,0,0"/>
      <StackPanel Orientation="Horizontal" HorizontalAlignment="Right" Margin="0,14,0,0">
        <Button x:Name="btnStart" Content="开始" Width="86" Height="30" Margin="0,0,8,0"/>
        <Button x:Name="btnClose" Content="关闭" Width="86" Height="30"/>
      </StackPanel>
    </StackPanel>
  </ScrollViewer>
</Window>
"""


class PullWindow(forms.WPFWindow):
    def __init__(self, materials):
        forms.WPFWindow.__init__(self, SHELL_XAML, literal_string=True)
        self.materials = materials
        self.state = {"action": None, "mode": 0, "thickness_mm": 20.0, "material_id": None}

        self.mode_box = self.FindName("modeBox")
        self.thick_box = self.FindName("thickBox")
        self.mat_box = self.FindName("matBox")
        self.tip_text = self.FindName("tipText")

        self.mat_box.Items.Clear()
        self.mat_box.Items.Add(u"<无材质>")
        for material in materials:
            self.mat_box.Items.Add(type_name(material))
        self.mat_box.SelectedIndex = 0

        self.FindName("btnStart").Click += self._on_start
        self.FindName("btnClose").Click += self._on_close
        self.mode_box.SelectionChanged += self._on_mode

    def _on_mode(self, sender, args):
        try:
            index = self.mode_box.SelectedIndex
        except Exception:
            index = 0
        if index == 1:
            self.thick_box.Text = u"100"
            self.tip_text.Text = (u"凹槽：在面上圈出矩形，向内挖到指定深度。\n"
                                  u"只有墙、楼板、屋顶、柱这类「可被剪切」的构件才有效。")
        else:
            self.thick_box.Text = u"20"
            self.tip_text.Text = (u"面层：在面上圈出矩形，向外加一层。\n"
                                  u"厚度可填 5–50mm，材质留给「材质贴面」也行。")

    def _on_start(self, sender, args):
        self.state["mode"] = self.mode_box.SelectedIndex
        try:
            self.state["thickness_mm"] = float(self.thick_box.Text)
        except Exception:
            forms.alert(u"厚度请填数字。")
            return
        index = self.mat_box.SelectedIndex
        if index > 0 and index - 1 < len(self.materials):
            self.state["material_id"] = self.materials[index - 1].Id
        else:
            self.state["material_id"] = None
        self.state["action"] = "go"
        self.Close()

    def _on_close(self, sender, args):
        self.state["action"] = None
        self.Close()


def run_pulling(params):
    ok = 0
    failures = []
    while True:
        try:
            host, face, point = pick_face()
        except Exception:
            break
        if host is None or face is None:
            failures.append((u"未取到面", u"换一个面再试"))
            continue
        try:
            p1 = uidoc.Selection.PickPoint(u"在面上点第一个角点")
        except Exception:
            break
        try:
            p2 = uidoc.Selection.PickPoint(u"在面上点对角点")
        except Exception:
            break

        transaction = Transaction(doc, u"推拉-{0}".format(type_name(host)))
        transaction.Start()
        options = transaction.GetFailureHandlingOptions()
        options.SetFailuresPreprocessor(WarningSuppressor())
        options.SetClearAfterRollback(True)
        transaction.SetFailureHandlingOptions(options)
        try:
            if params["mode"] == 1:
                element, error = carve_recess(host, face, point, p1, p2, params)
            else:
                element, error = extrude_on_face(host, face, point, p1, p2, params)
            if error:
                failures.append((type_name(host), error))
                transaction.RollBack()
            else:
                transaction.Commit()
                ok += 1
        except Exception as ex:
            transaction.RollBack()
            failures.append((type_name(host), u"{0}".format(ex)))
            write_log(u"推拉失败：{0}\n{1}".format(ex, traceback.format_exc()))
    return ok, failures


def main():
    write_log(u"== 推拉 开始 ==")
    if doc is None:
        forms.alert(u"请先打开一个项目。", exitscript=True)
    materials = all_materials()
    window = PullWindow(materials)
    window.ShowDialog()
    if window.state.get("action") != "go":
        return
    params = {
        "mode": window.state["mode"],
        "thickness_mm": window.state["thickness_mm"],
        "material_id": window.state["material_id"],
        "label": u"推拉",
    }
    ok, failures = run_pulling(params)
    if failures:
        lines = [u"成功 {0} 处，失败 {1} 处：".format(ok, len(failures))]
        for name, reason in failures[:8]:
            lines.append(u"· {0}：{1}".format(name, reason))
        forms.alert(u"\n".join(lines), title=u"推拉")
    else:
        forms.alert(u"完成，成功 {0} 处。".format(ok), title=u"推拉")


try:
    main()
except Exception:
    write_log(traceback.format_exc())
    forms.alert(u"推拉出错：\n{0}".format(traceback.format_exc()[-800:]), title=u"推拉")
