# -*- coding: utf-8 -*-
"""检查更新（只检查，不自动下载）

同时支持两种发行形式：

  * pyRevit 扩展版：<扩展根目录>/version.json
        ↔ 仓库 main/version.json
  * 独立版（LiuDun）：<LiuDun>/version.json
        ↔ 仓库 main/独立版/version.json

定位安装目录时从按钮脚本所在位置向上找 version.json 或 manifest.txt，
所以「扩展版 tab/panel/button」和「独立版 scripts/bNNN」两种布局都能用。

发现新版本时只提示并给出 GitHub 地址，由用户自行下载安装；
不自动下载、解压、覆盖，避免网络或文件操作把安装弄坏。
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

RAW_BASE = "https://raw.githubusercontent.com/{0}/{1}/{2}/".format(
    REPO_OWNER, REPO_NAME, BRANCH)

# 仓库里两份版本文件：扩展版在根目录，独立版在 独立版/ 下。
# 路径按 UTF-8 百分号编码，避免中文路径在部分代理 / WebClient 下失败。
VERSION_FILES = {
    "extension": "version.json",
    "standalone": "%E7%8B%AC%E7%AB%8B%E7%89%88/version.json",
}

# 国内实测：gh-proxy 系可用，raw / api 多半不通，所以放后面
MIRRORS = [
    "https://gh-proxy.com/",
    "https://ghfast.top/",
    "https://ghproxy.net/",
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


def install_root():
    """从按钮脚本所在目录向上找安装根目录。

    扩展版：pushbutton → panel → tab → 扩展根目录（有 version.json）
    独立版：scripts/bNNN → scripts → LiuDun（有 manifest.txt + version.json）
    """
    here = os.path.dirname(os.path.abspath(__file__))
    current = here
    for _ in range(6):
        if (os.path.isfile(os.path.join(current, "version.json"))
                or os.path.isfile(os.path.join(current, "manifest.txt"))):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent

    # 兜底：按扩展版布局往上三层
    here = os.path.dirname(os.path.abspath(__file__))
    for _ in range(3):
        here = os.path.dirname(here)
    return here


def local_info():
    """返回 (版本号或 None, "extension"/"standalone", 安装根目录)。"""
    root = install_root()
    is_standalone = os.path.isfile(os.path.join(root, "manifest.txt"))

    path = os.path.join(root, "version.json")
    if not os.path.isfile(path):
        return None, ("standalone" if is_standalone else "extension"), root

    try:
        with codecs.open(path, "r", "utf-8") as handle:
            data = json.loads(handle.read())
    except Exception:
        return None, ("standalone" if is_standalone else "extension"), root

    edition = u"{0}".format(data.get("edition") or u"").strip().lower()
    if edition not in ("extension", "standalone"):
        edition = "standalone" if is_standalone else "extension"

    version = u"{0}".format(data.get("version") or u"").strip()
    if not re.match(r"^[0-9]", version):
        version = None
    return version, edition, root


def version_urls(edition):
    rel = VERSION_FILES.get(edition, VERSION_FILES["extension"])
    raw = RAW_BASE + rel
    urls = [mirror + raw for mirror in MIRRORS]
    urls.append(
        "https://api.github.com/repos/{0}/{1}/contents/{2}?ref={3}".format(
            REPO_OWNER, REPO_NAME, rel, BRANCH))
    urls.append(raw)
    return urls


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


def remote_version(edition):
    errors = []
    for url in version_urls(edition):
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


def install_hint(edition):
    if edition == "standalone":
        return (
            u"安装方法（独立版）：关闭 Revit → 到仓库下载 独立版\\六吨工具箱.zip，\n"
            u"或者直接双击仓库里的「安装独立版.cmd」。它会解压覆盖：\n"
            u"%APPDATA%\\Autodesk\\Revit\\Addins\\2020\\LiuDun\\\n"
            u"然后重新打开 Revit。")
    return (
        u"安装方法（pyRevit 扩展）：下载源码压缩包 → 解压 → 把 六吨工具箱.extension\n"
        u"整个文件夹覆盖到：\n"
        u"%APPDATA%\\pyRevit\\Extensions\\\n"
        u"然后重启 Revit。")


def main():
    current, edition, root = local_info()
    edition_name = u"独立版" if edition == "standalone" else u"pyRevit 扩展版"

    output.print_md(u"## 检查更新")
    output.print_md(u"发行版：**{0}**，安装位置：`{1}`".format(edition_name, root))
    if current:
        output.print_md(u"当前版本：**v{0}**".format(current))
    else:
        output.print_md(u"当前版本：**未知**（安装目录里没有可读的 version.json）")
    output.print_md(u"正在读取远端版本…")

    latest, error = remote_version(edition)
    if not latest:
        output.print_md(u"无法获取远端版本：{0}".format(error or u"未知错误"))
        forms.alert(
            u"检查更新失败（网络受限）。\n\n可以手动打开仓库查看最新版本：\n{0}".format(
                REPO_URL),
            title=u"检查更新")
        return

    output.print_md(u"远端最新版本：**v{0}**".format(latest))

    if current and parse_version(latest) <= parse_version(current):
        output.print_md(u"已是最新版本，无需更新。")
        forms.alert(u"当前 v{0}，已是最新版本。".format(current),
                    title=u"检查更新")
        return

    if not current:
        answer = forms.alert(
            u"无法读取本地版本号，所以不能判断是否已是最新。\n\n"
            u"如果你装的是独立版 2.0.0 或更早的版本，安装包里没有 version.json，\n"
            u"这是已知问题，直接覆盖安装一次最新版即可。\n\n"
            u"{0}\n\n"
            u"远端最新版本：v{0}\n"
            u"{1}\n\n"
            u"是否现在打开下载页面？".format(latest, REPO_URL),
            title=u"检查更新",
            yes=True,
            no=True)
    else:
        answer = forms.alert(
            u"发现新版本 v{0}（当前 v{1}）。\n\n"
            u"{2}\n\n"
            u"{3}\n\n"
            u"是否现在打开下载页面？".format(latest, current, install_hint(edition), REPO_URL),
            title=u"发现新版本",
            yes=True,
            no=True)

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
