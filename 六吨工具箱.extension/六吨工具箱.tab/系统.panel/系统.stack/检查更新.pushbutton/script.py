# -*- coding: utf-8 -*-
"""检查更新（只检查，不自动下载）

  * 本地版本：扩展根目录的 version.json（{"version": "1.2.0"}）
  * 远端版本：GitHub 仓库 main 分支的 version.json（多镜像轮询）

发现新版本时只提示并给出 GitHub 地址，由用户自行下载安装；
不再自动下载、解压、覆盖，避免网络或文件操作把扩展弄坏。
"""

from __future__ import print_function

import codecs
import json
import os
import re
import traceback

import clr

clr.AddReference("System")

from System.Net import SecurityProtocolType, ServicePointManager, WebClient

from pyrevit import forms, script

output = script.get_output()

REPO_OWNER = "LIUsharkbee"
REPO_NAME = "liudun-toolbox"
BRANCH = "main"

REPO_URL = "https://github.com/{0}/{1}".format(REPO_OWNER, REPO_NAME)
RELEASES_URL = REPO_URL + "/releases"
ARCHIVE_URL = REPO_URL + "/archive/refs/heads/{0}.zip".format(BRANCH)

RAW_URL = "https://raw.githubusercontent.com/{0}/{1}/{2}/version.json".format(
    REPO_OWNER, REPO_NAME, BRANCH)

# 国内实测：gh-proxy 系可用，raw / jsdelivr 多半不通，所以放后面
VERSION_URLS = [
    "https://gh-proxy.com/" + RAW_URL,
    "https://ghfast.top/" + RAW_URL,
    "https://ghproxy.net/" + RAW_URL,
    "https://api.github.com/repos/{0}/{1}/contents/version.json?ref={2}".format(
        REPO_OWNER, REPO_NAME, BRANCH),
    RAW_URL,
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


def download_text(url):
    enable_tls12()
    client = WebClient()
    client.Headers["User-Agent"] = "liudun-toolbox-updater"
    try:
        data = client.DownloadData(url)
    finally:
        safe_call(lambda: client.Dispose())
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


def extract_version(text):
    """从 JSON / 包装文本 / API base64 里抠版本号。"""
    import base64

    text = text or u""
    match = re.search(r'"version"\s*:\s*"([^"]+)"', text)
    if match:
        return match.group(1).strip()
    match = re.search(
        r'version\s*[:=]\s*["\']?([0-9]+\.[0-9]+(?:\.[0-9]+)?)', text)
    if match:
        return match.group(1).strip()

    # GitHub API：内容在 base64 里
    try:
        data = json.loads(text)
        content = data.get("content")
        if content:
            inner = base64.b64decode(content).decode("utf-8")
            match = re.search(r'"version"\s*:\s*"([^"]+)"', inner)
            if match:
                return match.group(1).strip()
    except Exception:
        pass
    return None


def remote_version():
    errors = []
    for url in VERSION_URLS:
        host = url.split("/")[2]
        try:
            text = download_text(url)
            if not text or not text.strip():
                errors.append(u"{0}: 空响应".format(host))
                continue
            version = extract_version(text)
            if version:
                return version, u""
            errors.append(u"{0}: 未找到版本号".format(host))
        except Exception as ex:
            errors.append(u"{0}: {1}".format(host, ex))
    return None, u"；".join(errors)


def parse_version(text):
    text = (text or u"").strip()
    if text[:1] in (u"v", u"V"):
        text = text[1:]
    parts = []
    for chunk in text.replace(u"-", u".").split(u"."):
        try:
            parts.append(int(chunk))
        except ValueError:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def open_url(url):
    try:
        os.startfile(url)
        return True
    except Exception:
        return False


def main():
    current = local_version()
    output.print_md(u"## 检查更新")
    output.print_md(u"当前版本：**v{0}**，安装位置：`{1}`".format(
        current, extension_root()))
    output.print_md(u"正在读取远端版本…")

    latest, error = remote_version()
    if not latest:
        output.print_md(u"无法获取远端版本：{0}".format(error or u"未知错误"))
        forms.alert(
            u"检查更新失败（网络受限）。\n\n可以手动打开仓库查看最新版本：\n{0}".format(
                REPO_URL),
            title=u"检查更新")
        return

    output.print_md(u"远端最新版本：**v{0}**".format(latest))

    if parse_version(latest) <= parse_version(current):
        output.print_md(u"已是最新版本，无需更新。")
        forms.alert(u"当前 v{0}，已是最新版本。".format(current),
                    title=u"检查更新")
        return

    message = (
        u"发现新版本 v{0}（当前 v{1}）。\n\n"
        u"请到 GitHub 下载后手动安装：\n"
        u"{2}\n\n"
        u"安装方法：下载源码压缩包 → 解压 → 把 六吨工具箱.extension\n"
        u"整个文件夹覆盖到：\n"
        u"%APPDATA%\\pyRevit\\Extensions\\\n"
        u"然后重启 Revit。\n\n"
        u"是否现在打开下载页面？").format(latest, current, REPO_URL)

    answer = forms.alert(message, title=u"发现新版本", yes=True, no=True)
    if answer not in (True, u"Yes", u"yes", u"是"):
        return

    if open_url(REPO_URL):
        output.print_md(u"已在浏览器打开：{0}".format(REPO_URL))
    else:
        forms.alert(u"无法自动打开浏览器，请手动访问：\n{0}".format(REPO_URL))


try:
    main()
except Exception:
    output.print_md(u"脚本出错：\n```\n{0}\n```".format(traceback.format_exc()))
    forms.alert(u"脚本出错，详见输出窗口。", title=u"检查更新")
