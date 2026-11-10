# -*- coding: utf-8 -*-
"""启动我的世界（PCL2）

从 Revit 里一键拉起 PCL2 启动器。PCL2 本身不支持启动外部程序，
所以整合方向只能是「Revit → PCL2」，不能反过来。

路径优先顺序：
  1. %APPDATA%\\六吨工具箱\\pcl2.json 里保存的路径；
  2. 常见位置自动探测（D:\\PCL2 等）；
  3. 都不行就让用户选一次并记住。
"""

from __future__ import print_function

import io
import json
import os
import traceback

from pyrevit import forms, script

output = script.get_output()
logger = script.get_logger()

APP_DIR = os.path.join(os.environ.get("APPDATA", ""), "六吨工具箱")
CONFIG_PATH = os.path.join(APP_DIR, "pcl2.json")

CANDIDATES = [
    r"D:\PCL2\Plain Craft Launcher 2.exe",
    r"D:\PCL2\PCL2.exe",
    r"C:\Users\cyl\Desktop\Plain Craft Launcher 2.exe",
    r"C:\Users\cyl\Downloads\Plain Craft Launcher 2.exe",
]


def load_saved():
    if os.path.isfile(CONFIG_PATH):
        try:
            with io.open(CONFIG_PATH, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            path = data.get("path", u"")
            if path and os.path.isfile(path):
                return path
        except Exception:
            pass
    return None


def save_path(path):
    try:
        if not os.path.isdir(APP_DIR):
            os.makedirs(APP_DIR)
        with io.open(CONFIG_PATH, "w", encoding="utf-8") as handle:
            handle.write(u"{0}".format(json.dumps({"path": path}, ensure_ascii=False, indent=2)))
    except Exception:
        logger.error(traceback.format_exc())


def find_pcl():
    saved = load_saved()
    if saved:
        return saved
    for path in CANDIDATES:
        if os.path.isfile(path):
            return path
    return None


def main():
    path = find_pcl()
    if not path:
        picker = getattr(forms, "pick_file", None)
        if picker is None:
            forms.alert(u"没找到 PCL2，也当前 pyRevit 不支持选文件。\n\n"
                        u"请手动把路径写进：\n{0}".format(CONFIG_PATH))
            return
        chosen = picker(title=u"选择 PCL2 启动器（Plain Craft Launcher 2.exe）")
        if not chosen:
            return
        path = chosen
        save_path(path)

    try:
        os.startfile(path)
        output.print_md(u"已启动 PCL2：`{0}`".format(path))
    except Exception:
        detail = traceback.format_exc()
        logger.error(detail)
        forms.alert(u"启动失败：\n{0}".format(detail), title=u"启动我的世界")


try:
    main()
except Exception:
    output.print_md(u"脚本出错：\n```\n{0}\n```".format(traceback.format_exc()))
    forms.alert(u"脚本出错，详见输出窗口。", title=u"启动我的世界")
