# -*- coding: utf-8 -*-
"""检查更新

比较本地扩展版本与 GitHub 仓库里的最新版本；有新版本时下载源码包并覆盖安装。

  * 本地版本：扩展根目录的 version.json（{"version": "1.2.0"}）
  * 远程版本：GitHub 仓库 main 分支的 version.json
  * 更新方式：下载仓库 zip → 解压 → 把 六吨工具箱.extension 覆盖到当前安装目录

覆盖后需要**重启 Revit** 才会重新加载；脚本本身改的是文件，不影响当前会话。
"""

from __future__ import print_function

import codecs
import json
import os
import shutil
import tempfile
import traceback

import clr

clr.AddReference("System")
clr.AddReference("System.IO.Compression.FileSystem")

from System.Net import SecurityProtocolType, ServicePointManager, WebClient
from System.IO import File
from System.IO.Compression import ZipFile

from pyrevit import forms, script

output = script.get_output()

REPO_OWNER = "LIUsharkbee"
REPO_NAME = "liudun-toolbox"
BRANCH = "main"

RAW_VERSION_URLS = [
    "https://raw.githubusercontent.com/{0}/{1}/{2}/version.json".format(
        REPO_OWNER, REPO_NAME, BRANCH),
    # 镜像：raw 被墙或 502 时兜底
    "https://cdn.jsdelivr.net/gh/{0}/{1}@{2}/version.json".format(
        REPO_OWNER, REPO_NAME, BRANCH),
    "https://gh-proxy.com/https://raw.githubusercontent.com/{0}/{1}/{2}/version.json".format(
        REPO_OWNER, REPO_NAME, BRANCH),
]
API_VERSION_URL = "https://api.github.com/repos/{0}/{1}/contents/version.json?ref={2}".format(
    REPO_OWNER, REPO_NAME, BRANCH)
ZIP_URLS = [
    "https://codeload.github.com/{0}/{1}/zip/refs/heads/{2}".format(
        REPO_OWNER, REPO_NAME, BRANCH),
    "https://github.com/{0}/{1}/archive/refs/heads/{2}.zip".format(
        REPO_OWNER, REPO_NAME, BRANCH),
]


def safe_call(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def enable_tls12():
    try:
        ServicePointManager.SecurityProtocol = SecurityProtocolType.Tls12
    except Exception:
        pass


def download_bytes(url, timeout_ms=60000):
    enable_tls12()
    client = WebClient()
    client.Headers["User-Agent"] = "liudun-toolbox-updater"
    try:
        return client.DownloadData(url)
    finally:
        try:
            client.Dispose()
        except Exception:
            pass


def download_text(url):
    data = download_bytes(url)
    for encoding in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return data.decode(encoding)
        except Exception:
            continue
    return u""


def extension_root():
    """script.py 往上三层：pushbutton → panel → tab → extension。"""
    here = os.path.dirname(os.path.abspath(__file__))
    for _ in range(3):
        here = os.path.dirname(here)
    return here


def local_version():
    path = os.path.join(extension_root(), "version.json")
    if not os.path.isfile(path):
        return u"0.0.0"
    try:
        with codecs.open(path, "r", "utf-8") as handle:
            data = json.loads(handle.read())
        return u"{0}".format(data.get("version", u"0.0.0"))
    except Exception:
        return u"0.0.0"


def remote_version():
    """返回 (版本字符串, 说明)。"""
    errors = []
    for url in RAW_VERSION_URLS:
        try:
            text = download_text(url)
            if not text:
                errors.append(u"空响应")
                continue
            data = json.loads(text)
            return u"{0}".format(data.get("version", u"0.0.0")), u""
        except Exception as ex:
            errors.append(u"{0}: {1}".format(url.split("/")[2], ex))

    # 备用：GitHub API（base64 内容）
    try:
        import base64
        text = download_text(API_VERSION_URL)
        data = json.loads(text)
        content = base64.b64decode(data["content"])
        payload = json.loads(content.decode("utf-8"))
        return u"{0}".format(payload.get("version", u"0.0.0")), u""
    except Exception as ex:
        errors.append(u"api: {0}".format(ex))

    return None, u"；".join(errors)


def parse_version(text):
    text = (text or u"").strip()
    if text[:1] in (u"v", u"V"):
        text = text[1:]
    parts = []
    for chunk in (text or u"").replace(u"-", u".").split(u"."):
        try:
            parts.append(int(chunk))
        except ValueError:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def download_zip(temp_dir):
    errors = []
    for url in ZIP_URLS:
        try:
            data = download_bytes(url, 120000)
            if not data or len(data) < 1000:
                errors.append(u"{0}: 文件过小".format(url.split("/")[2]))
                continue
            path = os.path.join(temp_dir, "update.zip")
            File.WriteAllBytes(path, data)
            return path, u""
        except Exception as ex:
            errors.append(u"{0}: {1}".format(url.split("/")[2], ex))
    return None, u"；".join(errors)


def find_payload(root):
    """在解压目录里找 六吨工具箱.extension。"""
    for base, dirs, files in os.walk(root):
        for name in list(dirs):
            if name == u"六吨工具箱.extension":
                return os.path.join(base, name)
    return None


def copy_over(src_root, dst_root):
    """把 src_root 下的内容覆盖复制到 dst_root。"""
    copied = 0
    for base, dirs, files in os.walk(src_root):
        rel = os.path.relpath(base, src_root)
        target_dir = dst_root if rel == u"." else os.path.join(dst_root, rel)
        if not os.path.isdir(target_dir):
            os.makedirs(target_dir)
        for name in files:
            src = os.path.join(base, name)
            dst = os.path.join(target_dir, name)
            shutil.copyfile(src, dst)
            copied += 1
    return copied


def main():
    current = local_version()
    output.print_md(u"## 检查更新")
    output.print_md(u"当前版本：**v{0}**".format(current))
    output.print_md(u"正在读取远端版本…")

    latest, error = remote_version()
    if not latest:
        output.print_md(u"无法获取远端版本：{0}".format(error or u"未知错误"))
        forms.alert(u"检查更新失败：{0}".format(error or u"网络不可用"),
                    title=u"检查更新")
        return

    output.print_md(u"最新版本：**v{0}**".format(latest))

    if parse_version(latest) <= parse_version(current):
        output.print_md(u"已是最新版本，无需更新。")
        forms.alert(u"当前 v{0}，已是最新版本。".format(current),
                    title=u"检查更新")
        return

    answer = forms.alert(
        u"发现新版本 v{0}（当前 v{1}）。\n\n是否立即下载并覆盖安装？\n"
        u"覆盖后需要重启 Revit 生效。".format(latest, current),
        title=u"发现新版本", yes=True, no=True)
    if answer not in (True, u"Yes", u"yes", u"是"):
        return

    temp_dir = safe_call(lambda: tempfile.mkdtemp(prefix="liudun_update_"), None)
    if not temp_dir:
        forms.alert(u"无法创建临时目录。")
        return

    try:
        zip_path, zip_error = download_zip(temp_dir)
        if not zip_path:
            output.print_md(u"下载失败：{0}".format(zip_error))
            forms.alert(u"下载更新包失败：{0}".format(zip_error))
            return
        output.print_md(u"已下载更新包（{0:.1f} KB）".format(
            os.path.getsize(zip_path) / 1024.0))

        extract_dir = os.path.join(temp_dir, "extract")
        ZipFile.ExtractToDirectory(zip_path, extract_dir)

        payload = find_payload(extract_dir)
        if not payload:
            forms.alert(u"更新包里没有找到 六吨工具箱.extension。")
            return

        target = extension_root()
        copied = copy_over(payload, target)
        new_version = local_version()

        output.print_md(u"\n已覆盖 **{0}** 个文件到：`{1}`".format(copied, target))
        output.print_md(u"版本已更新为 **v{0}**。".format(new_version))
        output.print_md(u"\n**请重启 Revit** 以加载新版本。")

        forms.alert(
            u"已更新到 v{0}（覆盖 {1} 个文件）。\n\n请重启 Revit 生效。".format(
                new_version, copied),
            title=u"更新完成")
    except Exception as ex:
        output.print_md(u"更新失败：\n```\n{0}\n```".format(traceback.format_exc()))
        forms.alert(u"更新失败：{0}".format(ex), title=u"检查更新")
    finally:
        safe_call(lambda: shutil.rmtree(temp_dir, ignore_errors=True))


try:
    main()
except Exception:
    output.print_md(u"脚本出错：\n```\n{0}\n```".format(traceback.format_exc()))
    forms.alert(u"脚本出错，详见输出窗口。", title=u"检查更新")
