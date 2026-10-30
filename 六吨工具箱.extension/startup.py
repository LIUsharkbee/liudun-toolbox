# -*- coding: utf-8 -*-
"""六吨工具箱 · 启动检查更新

pyRevit 会在 Revit 启动加载扩展时执行本文件（extension 根目录的 startup.py）。

行为：
  * 每 24 小时最多检查一次，避免拖慢启动；
  * 用镜像源读取仓库 version.json，3 秒超时，失败静默；
  * 远端版本更高时弹提醒（优先 toast，没有就弹窗）；
  * 同一个版本只提醒一次，点过「检查更新」也不重复烦人。

任何异常都会被吞掉——启动脚本绝不能影响 Revit 启动。
"""

from __future__ import print_function

import io
import json
import os
import re
import time

REPO_OWNER = "LIUsharkbee"
REPO_NAME = "liudun-toolbox"
BRANCH = "main"

RAW_URL = "https://raw.githubusercontent.com/{0}/{1}/{2}/version.json".format(
    REPO_OWNER, REPO_NAME, BRANCH)

MIRRORS = [
    "https://gh-proxy.com/" + RAW_URL,
    "https://ghfast.top/" + RAW_URL,
    "https://ghproxy.net/" + RAW_URL,
    RAW_URL,
]

APP_DIR = os.path.join(os.environ.get("APPDATA", ""), "六吨工具箱")
STATE_PATH = os.path.join(APP_DIR, "update-check.json")
CHECK_INTERVAL = 24 * 3600      # 秒
TIMEOUT_MS = 3000


def local_version():
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "version.json")
    if not os.path.isfile(path):
        return u"0.0.0"
    try:
        with io.open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return u"{0}".format(data.get("version", u"0.0.0"))
    except Exception:
        return u"0.0.0"


def parse_version(text):
    text = (text or u"").strip()
    if text[:1] in (u"v", u"V"):
        text = text[1:]
    parts = []
    for chunk in text.replace(u"-", u".").split(u"."):
        digits = re.match(r"(\d+)", chunk)
        parts.append(int(digits.group(1)) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def download_text(url):
    import clr
    clr.AddReference("System")
    from System.IO import StreamReader
    from System.Net import SecurityProtocolType, ServicePointManager, WebRequest

    try:
        ServicePointManager.SecurityProtocol = SecurityProtocolType.Tls12
    except Exception:
        pass

    request = WebRequest.Create(url)
    request.Timeout = TIMEOUT_MS
    request.ReadWriteTimeout = TIMEOUT_MS
    try:
        request.UserAgent = "liudun-toolbox-updater"
    except Exception:
        pass

    response = request.GetResponse()
    try:
        # 用 .NET 的 StreamReader 读 .NET 流（IronPython 下 io.BufferedReader 不适用）
        reader = StreamReader(response.GetResponseStream())
        try:
            return reader.ReadToEnd()
        finally:
            try:
                reader.Close()
            except Exception:
                pass
    finally:
        try:
            response.Close()
        except Exception:
            pass


def extract_version(text):
    match = re.search(r'"version"\s*:\s*"([^"]+)"', text or u"")
    if match:
        return match.group(1).strip()
    return None


def remote_version():
    for url in MIRRORS:
        try:
            text = download_text(url)
            version = extract_version(text)
            if version:
                return version
        except Exception:
            continue
    return None


def load_state():
    try:
        if os.path.isfile(STATE_PATH):
            with io.open(STATE_PATH, "r", encoding="utf-8") as handle:
                return json.load(handle)
    except Exception:
        pass
    return {}


def save_state(state):
    try:
        if not os.path.isdir(APP_DIR):
            os.makedirs(APP_DIR)
        with io.open(STATE_PATH, "w", encoding="utf-8") as handle:
            handle.write(u"{0}".format(json.dumps(state, ensure_ascii=False, indent=2)))
    except Exception:
        pass


def notify(latest, current):
    message = (
        u"六吨工具箱有新版本：v{0}\n当前：v{1}\n\n"
        u"打开「系统 → 检查更新」可跳转下载。".format(latest, current))
    try:
        from pyrevit import forms
        toast = getattr(forms, "toast", None)
        if toast:
            toast(message, title=u"六吨工具箱更新")
            return
    except Exception:
        pass
    try:
        from pyrevit import forms
        forms.alert(message, title=u"六吨工具箱更新")
    except Exception:
        pass


def main():
    state = load_state()
    now = time.time()
    last = state.get("last_check", 0) or 0
    if now - last < CHECK_INTERVAL:
        return

    current = local_version()
    latest = remote_version()

    state["last_check"] = now
    if not latest:
        state["last_result"] = u"offline"
        save_state(state)
        return

    state["last_result"] = latest
    if parse_version(latest) > parse_version(current):
        already = state.get("notified_version", u"")
        if already != latest:
            notify(latest, current)
            state["notified_version"] = latest
    save_state(state)


# 测试时可以设 LIUDUN_TOOLBOX_NO_AUTORUN=1 跳过自检
if os.environ.get("LIUDUN_TOOLBOX_NO_AUTORUN") != "1":
    try:
        main()
    except Exception:
        pass
