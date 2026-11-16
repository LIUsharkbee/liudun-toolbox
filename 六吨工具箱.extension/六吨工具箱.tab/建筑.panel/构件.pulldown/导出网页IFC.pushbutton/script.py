# -*- coding: utf-8 -*-
"""导出网页（IFC 单文件 HTML）

思路：
  1. 用 Revit 原生 IFC 导出（几何由 Revit 自己算，不靠 pyRevit 抠图元）；
  2. 把 IFC 文件 + three.js + web-ifc 引擎一起嵌进一个 HTML；
  3. 双击 HTML 用浏览器打开，手机也能看。

依赖文件（与本脚本同目录）：
  three.module.js / web-ifc-api.js / web-ifc.wasm / template_ifc.html
"""

from __future__ import print_function

import base64
import io
import os
import tempfile
import traceback

from Autodesk.Revit.DB import FilteredElementCollector, View3D
from pyrevit import revit, forms, script

try:
    from Autodesk.Revit.DB import IFCExportOptions
except Exception:
    IFCExportOptions = None

doc = revit.doc
output = script.get_output()

HERE = os.path.dirname(os.path.abspath(__file__))
THREE_PATH = os.path.join(HERE, "three.module.js")
IFCLIB_PATH = os.path.join(HERE, "web-ifc-api.js")
WASM_PATH = os.path.join(HERE, "web-ifc.wasm")
TEMPLATE_PATH = os.path.join(HERE, "template_ifc.html")


def safe_call(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def describe_exception(ex):
    parts = []
    current = ex
    depth = 0
    while current is not None and depth < 4:
        parts.append(u"{0}: {1}".format(
            safe_call(lambda c=current: c.GetType().Name, u"?"),
            safe_call(lambda c=current: c.Message, u"{0}".format(current))))
        current = safe_call(lambda c=current: c.InnerException, None)
        depth += 1
    return u" → ".join(parts)


def _export_methods(options_name):
    found = []
    for method in safe_call(lambda: doc.GetType().GetMethods(), []) or []:
        try:
            if method.Name != u"Export":
                continue
            params = method.GetParameters()
            if params and params[-1].ParameterType.Name == options_name:
                found.append(method)
        except Exception:
            continue
    return found


def _try_export(folder, name, label, call):
    """执行一次导出，返回 (成功, 说明)。"""
    try:
        # IFC 导出会修改文档，必须在事务里执行
        with revit.Transaction(u"导出 IFC"):
            call()
    except Exception:
        return False, label + u" → " + traceback.format_exc()[-900:]
    target = os.path.join(folder, name + u".ifc")
    if os.path.isfile(target) and os.path.getsize(target) > 1024:
        return True, label
    return False, label + u" → 调用成功但没有生成 IFC"


def export_ifc(folder, name):
    """导出 IFC，返回 (是否成功, 说明)。"""
    if IFCExportOptions is None:
        return False, u"当前 Revit 版本没有 IFCExportOptions"

    attempts = []

    def direct():
        options = IFCExportOptions()
        doc.Export(folder, name, options)

    attempts.append((u"直接调用", direct))

    from System import Array, Object

    for index, method in enumerate(_export_methods(u"IFCExportOptions")):
        def reflect(method=method):
            options = IFCExportOptions()
            params = method.GetParameters()
            args = []
            for info in params:
                type_name = info.ParameterType.Name
                if type_name == u"String":
                    args.append(folder if len(args) == 0 else name)
                elif type_name.endswith(u"ExportOptions"):
                    args.append(options)
                else:
                    args.append(None)
            method.Invoke(doc, Array[Object](args))

        attempts.append((u"反射#{0}".format(index), reflect))

    def with_version():
        options = IFCExportOptions()
        try:
            from Autodesk.Revit.DB import IFCVersion
            options.FileVersion = IFCVersion.IFC2x3
        except Exception:
            pass
        doc.Export(folder, name, options)

    attempts.append((u"直接调用(IFC2x3)", with_version))

    errors = []
    for label, call in attempts:
        ok, detail = _try_export(folder, name, label, call)
        if ok:
            return True, detail
        errors.append(detail)

    return False, u"\n\n".join(errors[:3])

def read_b64(path):
    with open(path, "rb") as handle:
        return base64.b64encode(handle.read()).decode("ascii")


def build_html(ifc_path, html_path, title):
    for path in (THREE_PATH, IFCLIB_PATH, WASM_PATH, TEMPLATE_PATH):
        if not os.path.isfile(path):
            return u"缺少文件：{0}".format(path)

    with io.open(TEMPLATE_PATH, "r", encoding="utf-8") as handle:
        html = handle.read()

    html = html.replace(u"__THREE_B64__", read_b64(THREE_PATH))
    html = html.replace(u"__IFCLIB_B64__", read_b64(IFCLIB_PATH))
    html = html.replace(u"__IFCWASM_B64__", read_b64(WASM_PATH))
    html = html.replace(u"__IFC_B64__", read_b64(ifc_path))
    html = html.replace(u"__TITLE__", title)

    with io.open(html_path, "w", encoding="utf-8") as handle:
        handle.write(html)
    return None


def main():
    if doc is None:
        forms.alert(u"请先打开一个项目。", exitscript=True)

    name = safe_call(
        lambda: os.path.splitext(os.path.basename(doc.PathName))[0], u"") or u"model"

    saver = safe_call(lambda: forms.save_file, None)
    html_path = safe_call(
        lambda: saver(file_ext="html", default_name=name + u".html"), None)
    if not html_path:
        forms.alert(u"已取消。")
        return

    temp_dir = safe_call(lambda: tempfile.mkdtemp(prefix="ld_ifc_"), None)
    if not temp_dir:
        forms.alert(u"无法创建临时目录。")
        return

    output.print_md(u"## 导出网页（IFC）")
    output.print_md(u"正在用 Revit 导出 IFC…")

    ok, detail = export_ifc(temp_dir, name)
    if not ok:
        output.print_md(u"### 失败")
        output.print_md(u"```\n{0}\n```".format(detail))
        forms.alert(u"IFC 导出失败。\n详见输出窗口。", title=u"导出网页")
        return

    ifc_path = os.path.join(temp_dir, name + u".ifc")
    ifc_size = safe_call(lambda: os.path.getsize(ifc_path), 0) or 0
    output.print_md(u"IFC 已生成：{0:.1f} MB".format(ifc_size / 1024.0 / 1024.0))
    output.print_md(u"正在打包 HTML…")

    error = build_html(ifc_path, html_path, name)
    if error:
        forms.alert(error, title=u"导出网页")
        return

    size = safe_call(lambda: os.path.getsize(html_path), 0) or 0
    safe_call(lambda: os.remove(ifc_path))

    output.print_md(u"\n### 完成")
    output.print_md(u"HTML：`{0}`（{1:.1f} MB）".format(html_path, size / 1024.0 / 1024.0))
    forms.alert(
        u"已生成单文件网页：\n{0}\n\n"
        u"双击用浏览器打开（首次解析需要几秒到几十秒）。".format(html_path),
        title=u"导出完成")


try:
    main()
except Exception:
    output.print_md(u"脚本出错：\n```\n{0}\n```".format(traceback.format_exc()))
    forms.alert(u"脚本出错，详见输出窗口。", title=u"导出网页")
