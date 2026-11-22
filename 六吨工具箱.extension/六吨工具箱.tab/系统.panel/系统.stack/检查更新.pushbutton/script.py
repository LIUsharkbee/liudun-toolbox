# -*- coding: utf-8 -*-
"""检查更新（只检查，不自动下载）

  * 本地版本：扩展根目录的 version.json
  * 远端版本：GitHub 仓库 main 分支的 version.json（多镜像轮询，每个 5 秒超时）
  * 断网兜底：BUILTIN_LATEST 是发版时写进来的「已知最新版本」，
    网络不通时也能告诉使用者「你大概是不是最新」。

发现新版本只提示 + 给链接，由用户自己下载安装。
"""

from __future__ import print_function

import codecs
import json
import os
import re
import traceback

import clr

clr.AddReference("System")

from System import TimeSpan  # noqa: E402
from System.IO import StreamReader  # noqa: E402
from System.Net import (  # noqa: E402
    HttpWebRequest,
    SecurityProtocolType,
    ServicePointManager,
)

from pyrevit import forms, script  # noqa: E402

output = script.get_output()

REPO_OWNER = "LIUsharkbee"
REPO_NAME = "liudun-toolbox"
BRANCH = "main"

# 发版时同步更新这一行（断网兜底用）
BUILTIN_LATEST = u"2.3.1"

REPO_URL = "https://github.com/{0}/{1}".format(REPO_OWNER, REPO_NAME)
RELEASES_URL = REPO_URL + "/releases"

RAW_URL = "https://raw.githubusercontent.com/{0}/{1}/{2}/version.json".format(
    REPO_OWNER, REPO_NAME, BRANCH)
API_URL = "https://api.github.com/repos/{0}/{1}/contents/version.json?ref={2}".format(
    REPO_OWNER, REPO_NAME, BRANCH)
JSDELIVR_URL = "https://cdn.jsdelivr.net/gh/{0}/{1}@{2}/version.json".format(
    REPO_OWNER, REPO_NAME, BRANCH)

# 依次尝试；国内实测 gh-proxy 系通常最先通
VERSION_URLS = [
    "https://gh-proxy.com/" + RAW_URL,
    "https://ghfast.top/" + RAW_URL,
    "https://ghproxy.net/" + RAW_URL,
    "https://raw.gitmirror.com/{0}/{1}/{2}/version.json".format(
        REPO_OWNER, REPO_NAME, BRANCH),
    JSDELIVR_URL,
    API_URL,
    RAW_URL,
]

TIMEOUT_MS = 5000


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


def download_text(url, timeout_ms=TIMEOUT_MS):
    enable_tls12()
    request = HttpWebRequest.Create(url)
    request.Method = "GET"
    request.UserAgent = "liudun-toolbox-updater"
    request.Timeout = timeout_ms
    request.ReadWriteTimeout = timeout_ms
    request.AllowAutoRedirect = True
    try:
        request.Proxy = None          # 绕过本机代理设置
    except Exception:
        pass
    response = request.GetResponse()
    try:
        stream = response.GetResponseStream()
        reader = StreamReader(stream)
        try:
            return reader.ReadToEnd()
        finally:
            safe_call(lambda: reader.Dispose())
    finally:
        safe_call(lambda: response.Close())


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
                return version, errors
            errors.append(u"{0}: 未找到版本号".format(host))
        except Exception as ex:
            errors.append(u"{0}: {1}".format(host, ex))
    return None, errors


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


def ask_open(message, title):
    answer = forms.alert(message, title=title, yes=True, no=True)
    return answer in (True, u"Yes", u"yes", u"是")


def main():
    current = local_version()
    builtin = BUILTIN_LATEST

    output.print_md(u"## 检查更新")
    output.print_md(u"当前版本：**v{0}**".format(current))
    output.print_md(u"安装位置：`{0}`".format(extension_root()))
    output.print_md(u"内置参考版本：v{0}（发版时写入）".format(builtin))
    output.print_md(u"正在读取远端版本…")

    latest, errors = remote_version()

    # ① 联网成功
    if latest:
        output.print_md(u"远端最新版本：**v{0}**".format(latest))
        if parse_version(latest) <= parse_version(current):
            output.print_md(u"已是最新版本。")
            forms.alert(
                u"当前 v{0}，远端 v{1}。\n\n已是最新版本。".format(current, latest),
                title=u"检查更新")
            return
        message = (
            u"发现新版本 v{0}（当前 v{1}）。\n\n"
            u"请到 GitHub 下载后手动安装：\n{2}\n\n"
            u"独立版：下载 liudun-toolbox-{0}-standalone.zip → 解压 → 运行「安装独立版.cmd」\n"
            u"扩展版：下载 liudun-toolbox-{0}.zip → 解压 → 覆盖到\n"
            u"%APPDATA%\\pyRevit\\Extensions\\ → 重启 Revit\n\n"
            u"是否现在打开下载页面？").format(latest, current, RELEASES_URL)
        if ask_open(message, u"发现新版本"):
            if not open_url(RELEASES_URL):
                forms.alert(u"无法自动打开浏览器，请手动访问：\n{0}".format(RELEASES_URL))
        return

    # ② 联网失败 → 用内置版本给结论，别让人一脸懵
    detail = u"\n".join(errors[:4]) if errors else u"未知错误"
    if parse_version(current) >= parse_version(builtin):
        verdict = u"按内置参考版本判断：你**已经是最新**（v{0}）。".format(builtin)
    else:
        verdict = u"按内置参考版本判断：**有新版本 v{0}**，建议更新。".format(builtin)

    message = (
        u"没连上 GitHub，无法确认线上最新版本。\n\n"
        u"当前版本：v{0}\n内置参考版本：v{1}\n{2}\n\n"
        u"仓库地址：\n{3}\n\n"
        u"是否打开仓库页面（联网后看 README 顶部的版本号）？\n\n"
        u"失败明细：\n{4}").format(current, builtin, verdict, REPO_URL, detail)

    output.print_md(u"远端读取失败：\n```\n{0}\n```".format(detail))
    output.print_md(verdict)

    if ask_open(message, u"检查更新（离线）"):
        if not open_url(REPO_URL):
            forms.alert(u"无法自动打开浏览器，请手动访问：\n{0}".format(REPO_URL))


try:
    main()
except Exception:
    output.print_md(u"脚本出错：\n```\n{0}\n```".format(traceback.format_exc()))
    forms.alert(u"脚本出错：\n{0}".format(traceback.format_exc()[-500:]), title=u"检查更新")
