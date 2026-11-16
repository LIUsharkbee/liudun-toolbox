# -*- coding: utf-8 -*-
"""剖切动画（CutScene 风格）

在三维视图里让「剖面框」沿某个方向推进，逐帧导出 PNG，
再用 ffmpeg 合成 MP4 或 GIF，得到模型逐层显现 / 建造生长动画。

特点：
  * 不修改模型，只动视图的剖面框，结束时整体 RollBack；
  * 支持 6 个方向（+X/-X/+Y/-Y/+Z/-Z）与起止百分比；
  * 分辨率、帧数、FPS 可调；
  * 有 ffmpeg 时直接出 MP4/GIF，没有则只留 PNG 序列。
"""

from __future__ import print_function

import os
import subprocess
import time
import traceback

from Autodesk.Revit.DB import (
    ElementId,
    ExportRange,
    FitDirectionType,
    ImageExportOptions,
    ImageFileType,
    Transaction,
    SubTransaction,
    View3D,
    ZoomFitType,
    FilteredElementCollector,
)
from System.Collections.Generic import List as DotNetList

from pyrevit import revit, forms, script

doc = revit.doc
uidoc = revit.uidoc
output = script.get_output()

LOG_PATH = r"C:\Users\cyl\Documents\deepseek-harness\default-workspace\_剖切动画日志.txt"
DEFAULT_ROOT = os.path.join(os.path.expanduser("~"), u"Desktop", u"六吨动画")

AXES = ["X", "Y", "Z"]


def log_line(text):
    try:
        f = open(LOG_PATH, "ab")
        f.write((u"%s %s\n" % (time.strftime("%H:%M:%S"), text)).encode("utf-8"))
        f.close()
    except Exception:
        pass


def text_of(value):
    if isinstance(value, unicode):
        return value
    try:
        return unicode(value)
    except Exception:
        return u"%s" % value


def view_name(view):
    try:
        return text_of(view.Name)
    except Exception:
        return u"<视图>"


def all_3d_views():
    views = []
    for element in FilteredElementCollector(doc).OfClass(View3D):
        try:
            if element.IsTemplate:
                continue
        except Exception:
            pass
        views.append(element)
    views.sort(key=lambda v: view_name(v))
    return views


def box_copy(box):
    from Autodesk.Revit.DB import BoundingBoxXYZ, XYZ
    copy = BoundingBoxXYZ()
    copy.Min = XYZ(box.Min.X, box.Min.Y, box.Min.Z)
    copy.Max = XYZ(box.Max.X, box.Max.Y, box.Max.Z)
    copy.Transform = box.Transform
    return copy


def box_size(box):
    return (box.Max.X - box.Min.X, box.Max.Y - box.Min.Y, box.Max.Z - box.Min.Z)


def ensure_section_box(view):
    """返回视图当前的剖面框；未启用则用视图范围造一个并返回。"""
    try:
        active = view.IsSectionBoxActive
    except Exception:
        active = False
    if active:
        return box_copy(view.GetSectionBox())

    # 没开启：用该视图里所有图元的联合包围盒造一个
    from Autodesk.Revit.DB import BoundingBoxXYZ
    lo = [None, None, None]
    hi = [None, None, None]
    try:
        collector = FilteredElementCollector(doc, view.Id).WhereElementIsNotElementType()
        for element in collector:
            try:
                bbox = element.get_BoundingBox(view)
            except Exception:
                bbox = None
            if bbox is None:
                continue
            for axis, (a, b) in enumerate(((bbox.Min.X, bbox.Max.X),
                                           (bbox.Min.Y, bbox.Max.Y),
                                           (bbox.Min.Z, bbox.Max.Z))):
                lo[axis] = a if lo[axis] is None else min(lo[axis], a)
                hi[axis] = b if hi[axis] is None else max(hi[axis], b)
    except Exception as ex:
        log_line(u"取模型包围盒失败: %s" % ex)
    if lo[0] is None:
        return None
    from Autodesk.Revit.DB import XYZ
    box = BoundingBoxXYZ()
    box.Min = XYZ(lo[0], lo[1], lo[2])
    box.Max = XYZ(hi[0], hi[1], hi[2])
    return box


def cut_box(orig, axis_index, start_frac, end_frac, keep_positive):
    """按方向把剖面框的一侧推进到 start→end 的位置。"""
    from Autodesk.Revit.DB import XYZ
    box = box_copy(orig)
    lo = [orig.Min.X, orig.Min.Y, orig.Min.Z][axis_index]
    hi = [orig.Max.X, orig.Max.Y, orig.Max.Z][axis_index]
    span = hi - lo
    if abs(span) < 1e-9:
        return box
    frac = start_frac + (end_frac - start_frac)
    pos = lo + span * frac

    if keep_positive:
        values = [box.Min.X, box.Min.Y, box.Min.Z]
        values[axis_index] = pos
        box.Min = XYZ(values[0], values[1], values[2])
    else:
        values = [box.Max.X, box.Max.Y, box.Max.Z]
        values[axis_index] = pos
        box.Max = XYZ(values[0], values[1], values[2])
    return box


def png_files(folder):
    result = set()
    try:
        for name in os.listdir(folder):
            if name.lower().endswith(".png"):
                result.add(name)
    except Exception:
        pass
    return result


def export_frame(view, folder, index, pixel_size):
    """导出单帧，返回实际文件路径（失败返回 None）。"""
    before = png_files(folder)
    base = os.path.join(folder, u"tmp_%04d" % index)

    opts = ImageExportOptions()
    opts.ExportRange = ExportRange.SetOfViews
    ids = DotNetList[ElementId]()
    try:
        ids.Add(view.Id)
        opts.SetViewsAndSheets(ids)
    except Exception:
        opts.ExportRange = ExportRange.CurrentView
    opts.FilePath = base
    opts.HLRandWFViewsFileType = ImageFileType.PNG
    opts.ShadowViewsFileType = ImageFileType.PNG
    opts.ZoomType = ZoomFitType.FitToPage
    opts.PixelSize = int(pixel_size)
    opts.FitDirection = FitDirectionType.Horizontal

    try:
        doc.ExportImage(opts)
    except Exception as ex:
        log_line(u"ExportImage 失败 #%d: %s" % (index, ex))
        return None

    after = png_files(folder)
    new_files = list(after - before)
    if not new_files:
        return None

    produced = os.path.join(folder, new_files[0])
    target = os.path.join(folder, u"frame_%04d.png" % index)
    try:
        if os.path.exists(target):
            os.remove(target)
        os.rename(produced, target)
    except Exception:
        return produced
    return target


def ramp_fraction(index, count):
    if count <= 1:
        return 1.0
    return float(index) / float(count - 1)


def run_frames(view, folder, params):
    """逐帧修改剖面框并导出；返回 (成功帧数, 失败列表, 首帧路径)。"""
    from Autodesk.Revit.DB import XYZ

    axis_index = params["axis"]
    keep_positive = params["keep"]
    start_frac = params["start"]
    end_frac = params["end"]
    count = params["frames"]
    pixel = params["pixel"]

    orig = ensure_section_box(view)
    if orig is None:
        return 0, [u"取不到剖面框，请先在三维视图里勾选「剖面框」"], None

    ok = 0
    failures = []
    first = None

    main_tx = Transaction(doc, u"剖切动画")
    main_tx.Start()
    try:
        for index in range(count):
            sub = SubTransaction(doc)
            sub.Start()
            try:
                frac = start_frac + (end_frac - start_frac) * ramp_fraction(index, count)
                span = [orig.Max.X - orig.Min.X, orig.Max.Y - orig.Min.Y,
                        orig.Max.Z - orig.Min.Z][axis_index]
                lo = [orig.Min.X, orig.Min.Y, orig.Min.Z][axis_index]
                pos = lo + span * frac
                cut = box_copy(orig)
                if keep_positive:
                    values = [cut.Min.X, cut.Min.Y, cut.Min.Z]
                    values[axis_index] = pos
                    cut.Min = XYZ(values[0], values[1], values[2])
                else:
                    values = [cut.Max.X, cut.Max.Y, cut.Max.Z]
                    values[axis_index] = pos
                    cut.Max = XYZ(values[0], values[1], values[2])

                view.IsSectionBoxActive = True
                view.SetSectionBox(cut)
                doc.Regenerate()
            except Exception as ex:
                failures.append(u"第 %d 帧剖面框：%s" % (index + 1, ex))
                sub.RollBack()
                continue

            path = export_frame(view, folder, index, pixel)
            sub.Commit()
            if path is None:
                failures.append(u"第 %d 帧导出失败" % (index + 1))
            else:
                ok += 1
                if first is None:
                    first = path
    finally:
        main_tx.RollBack()

    return ok, failures, first


def find_ffmpeg():
    candidates = [
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "liudun-video", "imageio_ffmpeg",
                     "binaries", "ffmpeg-win-x86_64-v7.1.exe"),
    ]
    try:
        for name in os.listdir(os.path.join(os.environ.get("LOCALAPPDATA", ""), "liudun-video",
                                            "imageio_ffmpeg", "binaries")):
            if name.lower().startswith("ffmpeg") and name.lower().endswith(".exe"):
                candidates.append(os.path.join(os.environ.get("LOCALAPPDATA", ""), "liudun-video",
                                               "imageio_ffmpeg", "binaries", name))
    except Exception:
        pass
    candidates.append("ffmpeg")
    for path in candidates:
        try:
            if path == "ffmpeg":
                return path
            if os.path.exists(path):
                return path
        except Exception:
            pass
    return None


def compose(folder, params, ffmpeg):
    fps = max(1, int(params["fps"]))
    fmt = params["format"]
    out_name = u"剖切动画_%s" % time.strftime("%Y%m%d_%H%M%S")
    if fmt == "mp4":
        out_path = os.path.join(folder, out_name + u".mp4")
        cmd = [ffmpeg, "-y", "-framerate", str(fps), "-i", os.path.join(folder, u"frame_%04d.png"),
               "-c:v", "libx264", "-pix_fmt", "yuv420p",
               "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", out_path]
    elif fmt == "gif":
        out_path = os.path.join(folder, out_name + u".gif")
        filt = ("fps=%d,scale=720:-1:flags=lanczos,split[a][b];[a]palettegen[p];[b][p]paletteuse" % fps)
        cmd = [ffmpeg, "-y", "-framerate", str(fps), "-i", os.path.join(folder, u"frame_%04d.png"),
               "-vf", filt, "-loop", "0", out_path]
    else:
        return None, None

    flags = 0x08000000 if os.name == "nt" else 0
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                creationflags=flags)
        out, err = proc.communicate()
        if proc.returncode != 0:
            log_line(u"ffmpeg 失败 %s: %s" % (proc.returncode, err[-800:]))
            return None, text_of(err[-400:])
    except Exception as ex:
        log_line(u"ffmpeg 调用异常: %s" % ex)
        return None, text_of(ex)
    return out_path, None


def describe(params):
    axis = AXES[params["axis"]]
    keep = u"保留正侧" if params["keep"] else u"保留负侧"
    return (u"视图 %s\n方向 %s（%s）\n起始 %d%% → 结束 %d%%\n%d 帧 · %d FPS · %d px\n输出 %s" %
            (params["view_name"], axis, keep, int(params["start"] * 100),
             int(params["end"] * 100), params["frames"], params["fps"],
             params["pixel"], params["format"].upper()))


XAML = u"""
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="剖切动画" Height="640" Width="660"
        WindowStartupLocation="CenterScreen">
  <ScrollViewer VerticalScrollBarVisibility="Auto">
    <StackPanel Margin="16">
      <TextBlock Text="剖切动画（CutScene 风格）" FontSize="18" FontWeight="Bold"/>
      <TextBlock Margin="0,6,0,10" Foreground="#666" TextWrapping="Wrap"
                 Text="用剖面框沿方向逐帧推进，生成模型逐层显现的动画。不修改模型，结束后自动恢复。"/>

      <TextBlock Text="① 视图与方向" FontWeight="Bold" Margin="0,6,0,4"/>
      <Grid Margin="0,0,0,4">
        <Grid.ColumnDefinitions>
          <ColumnDefinition Width="120"/>
          <ColumnDefinition Width="*"/>
        </Grid.ColumnDefinitions>
        <TextBlock Text="三维视图" VerticalAlignment="Center"/>
        <ComboBox x:Name="viewBox" Grid.Column="1" Height="26"/>
      </Grid>
      <Grid Margin="0,0,0,4">
        <Grid.ColumnDefinitions>
          <ColumnDefinition Width="120"/>
          <ColumnDefinition Width="*"/>
          <ColumnDefinition Width="120"/>
          <ColumnDefinition Width="*"/>
        </Grid.ColumnDefinitions>
        <TextBlock Text="推进方向" VerticalAlignment="Center"/>
        <ComboBox x:Name="axisBox" Grid.Column="1" Height="26"/>
        <TextBlock Text="保留哪侧" Grid.Column="2" VerticalAlignment="Center" Margin="8,0,0,0"/>
        <ComboBox x:Name="keepBox" Grid.Column="3" Height="26"/>
      </Grid>

      <TextBlock Text="② 范围与帧率" FontWeight="Bold" Margin="0,10,0,4"/>
      <Grid Margin="0,0,0,4">
        <Grid.ColumnDefinitions>
          <ColumnDefinition Width="120"/>
          <ColumnDefinition Width="*"/>
          <ColumnDefinition Width="120"/>
          <ColumnDefinition Width="*"/>
        </Grid.ColumnDefinitions>
        <TextBlock Text="起始 (%)" VerticalAlignment="Center"/>
        <TextBox x:Name="startBox" Grid.Column="1" Height="26" Text="0"/>
        <TextBlock Text="结束 (%)" Grid.Column="2" VerticalAlignment="Center" Margin="8,0,0,0"/>
        <TextBox x:Name="endBox" Grid.Column="3" Height="26" Text="100"/>
      </Grid>
      <Grid Margin="0,0,0,4">
        <Grid.ColumnDefinitions>
          <ColumnDefinition Width="120"/>
          <ColumnDefinition Width="*"/>
          <ColumnDefinition Width="120"/>
          <ColumnDefinition Width="*"/>
        </Grid.ColumnDefinitions>
        <TextBlock Text="帧数" VerticalAlignment="Center"/>
        <TextBox x:Name="frameBox" Grid.Column="1" Height="26" Text="60"/>
        <TextBlock Text="FPS" Grid.Column="2" VerticalAlignment="Center" Margin="8,0,0,0"/>
        <TextBox x:Name="fpsBox" Grid.Column="3" Height="26" Text="20"/>
      </Grid>

      <TextBlock Text="③ 输出" FontWeight="Bold" Margin="0,10,0,4"/>
      <Grid Margin="0,0,0,4">
        <Grid.ColumnDefinitions>
          <ColumnDefinition Width="120"/>
          <ColumnDefinition Width="*"/>
          <ColumnDefinition Width="120"/>
          <ColumnDefinition Width="*"/>
        </Grid.ColumnDefinitions>
        <TextBlock Text="格式" VerticalAlignment="Center"/>
        <ComboBox x:Name="formatBox" Grid.Column="1" Height="26"/>
        <TextBlock Text="分辨率 (px)" Grid.Column="2" VerticalAlignment="Center" Margin="8,0,0,0"/>
        <ComboBox x:Name="pixelBox" Grid.Column="3" Height="26"/>
      </Grid>
      <Grid Margin="0,0,0,4">
        <Grid.ColumnDefinitions>
          <ColumnDefinition Width="120"/>
          <ColumnDefinition Width="*"/>
        </Grid.ColumnDefinitions>
        <TextBlock Text="输出目录" VerticalAlignment="Center"/>
        <TextBox x:Name="pathBox" Grid.Column="1" Height="26"/>
      </Grid>

      <TextBlock x:Name="infoText" Margin="0,10,0,6" Foreground="#444" TextWrapping="Wrap"
                 Text="选择视图后点「预览首帧」确认构图，再点「开始导出」。"/>
      <StackPanel Orientation="Horizontal" Margin="0,4,0,0">
        <Button x:Name="btnPreview" Content="预览首帧" Width="110" Height="30" Margin="0,0,8,0"/>
        <Button x:Name="btnRun" Content="开始导出" Width="110" Height="30" FontWeight="Bold" Margin="0,0,8,0"/>
        <Button x:Name="btnOpen" Content="打开目录" Width="100" Height="30" Margin="0,0,8,0"/>
        <Button x:Name="btnClose" Content="关闭" Width="80" Height="30"/>
      </StackPanel>
    </StackPanel>
  </ScrollViewer>
</Window>
"""


class AnimWindow(forms.WPFWindow):
    def __init__(self, views):
        forms.WPFWindow.__init__(self, XAML, literal_string=True)
        self.views = views
        self.state = {"action": None, "params": None}
        self.ffmpeg = find_ffmpeg()

        for view in views:
            self.viewBox.Items.Add(view_name(view))
        if views:
            self.viewBox.SelectedIndex = 0

        for label in [u"Z 向上（自下而上显现）", u"Z 向下", u"X 正向", u"X 反向",
                      u"Y 正向", u"Y 反向"]:
            self.axisBox.Items.Add(label)
        self.axisBox.SelectedIndex = 0

        for label in [u"保留推进侧（正侧）", u"保留起始侧（负侧）"]:
            self.keepBox.Items.Add(label)
        self.keepBox.SelectedIndex = 1

        for label in [u"MP4 视频", u"GIF 动图", u"只要 PNG 序列"]:
            self.formatBox.Items.Add(label)
        self.formatBox.SelectedIndex = 0

        for label in [u"960", u"1280", u"1600", u"1920"]:
            self.pixelBox.Items.Add(label)
        self.pixelBox.SelectedIndex = 1

        self.pathBox.Text = os.path.join(DEFAULT_ROOT, time.strftime("%Y%m%d_%H%M%S"))

        self.btnPreview.Click += self._on_preview
        self.btnRun.Click += self._on_run
        self.btnOpen.Click += self._on_open
        self.btnClose.Click += self._on_close

        if self.ffmpeg is None or self.ffmpeg == "ffmpeg":
            self.infoText.Text = (u"提示：没有找到 ffmpeg，只能导出 PNG 序列。\n"
                                  u"如需 MP4/GIF，把 ffmpeg.exe 放到 PATH 或重新安装 imageio-ffmpeg。")

    def _pixel(self):
        try:
            item = self.pixelBox.Items[self.pixelBox.SelectedIndex]
            return int(unicode(item).strip())
        except Exception:
            return 1280

    def _float(self, box, default):
        try:
            return float(box.Text)
        except Exception:
            return default

    def _collect(self):
        if not self.views:
            forms.alert(u"项目里没有三维视图。", exitscript=True)
        view = self.views[max(0, self.viewBox.SelectedIndex)]
        axis_index = [2, 2, 0, 0, 1, 1][max(0, self.axisBox.SelectedIndex)]
        keep = self.axisBox.SelectedIndex % 2 == 0
        fmt = ["mp4", "gif", "png"][max(0, self.formatBox.SelectedIndex)]
        params = {
            "view": view,
            "view_name": view_name(view),
            "axis": axis_index,
            "keep": keep,
            "start": self._float(self.startBox, 0.0) / 100.0,
            "end": self._float(self.endBox, 100.0) / 100.0,
            "frames": max(2, int(self._float(self.frameBox, 60))),
            "fps": max(1, int(self._float(self.fpsBox, 20))),
            "pixel": self._pixel(),
            "format": fmt,
            "folder": self.pathBox.Text,
        }
        return params

    def _on_preview(self, sender, args):
        params = self._collect()
        folder = params["folder"]
        try:
            if not os.path.isdir(folder):
                os.makedirs(folder)
        except Exception as ex:
            forms.alert(u"建目录失败：{0}".format(ex))
            return
        ok, failures, first = run_frames(params["view"], folder, dict(params, frames=1))
        if first is None:
            forms.alert(u"预览失败：\n{0}".format(u"\n".join(failures[:5]) or u"未知原因"))
            return
        self.infoText.Text = (u"预览已生成：\n{0}\n\n{1}".format(first, describe(params)))
        try:
            os.startfile(first)
        except Exception:
            pass

    def _on_run(self, sender, args):
        params = self._collect()
        folder = params["folder"]
        try:
            if not os.path.isdir(folder):
                os.makedirs(folder)
        except Exception as ex:
            forms.alert(u"建目录失败：{0}".format(ex))
            return

        started = time.time()
        ok, failures, first = run_frames(params["view"], folder, params)
        elapsed = time.time() - started

        lines = [u"成功 {0} 帧，失败 {1} 帧，用时 {2:.1f} 秒。".format(ok, len(failures), elapsed)]
        out_path = None
        if ok and params["format"] != "png":
            if self.ffmpeg is None:
                lines.append(u"没有 ffmpeg，已保留 PNG 序列。")
            else:
                out_path, err = compose(folder, params, self.ffmpeg)
                if out_path:
                    lines.append(u"已生成：{0}".format(out_path))
                else:
                    lines.append(u"合成失败：{0}".format(err or u"未知"))

        if failures:
            lines.append(u"\n".join(failures[:8]))

        self.infoText.Text = u"\n".join(lines) + u"\n\n目录：" + folder
        log_line(u"完成 ok=%d fail=%d out=%s" % (ok, len(failures), out_path))
        forms.alert(u"\n".join(lines), title=u"剖切动画")

    def _on_open(self, sender, args):
        folder = self.pathBox.Text
        try:
            if not os.path.isdir(folder):
                os.makedirs(folder)
            os.startfile(folder)
        except Exception as ex:
            forms.alert(u"打不开：{0}".format(ex))

    def _on_close(self, sender, args):
        self.state["action"] = None
        self.Close()


def main():
    if doc is None:
        forms.alert(u"请先打开一个项目。", exitscript=True)

    views = all_3d_views()
    if not views:
        forms.alert(u"项目里没有三维视图。")
        return

    window = AnimWindow(views)
    window.ShowDialog()


try:
    main()
except Exception:
    tb = traceback.format_exc()
    log_line(tb)
    output.print_md(u"脚本出错：\n```\n{0}\n```".format(tb))
    forms.alert(u"脚本出错：\n{0}".format(tb[-700:]), title=u"剖切动画")
