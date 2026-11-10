# -*- coding: utf-8 -*-
"""AI 建模助手

聊天式操作 Revit：用中文直接说需求，由模型调用工具完成
创建墙 / 管道 / 风管、按属性过滤批量改参数等操作。

引擎是同目录下的 RevitAgent2020.dll（C# / .NET Framework 4.7.2，
随按钮一起分发，不需要另外安装）。首次使用点窗口右上角「设置」
填写 DeepSeek API Key。

注意：需要 pyRevit 的 IronPython 引擎（默认），或安装了 pythonnet 的
CPython 引擎；纯 CPython 无 .NET 桥时按钮会给出提示。
"""

from __future__ import print_function

import os
import traceback

from pyrevit import forms, revit, script

logger = script.get_logger()

HERE = os.path.dirname(__file__)
DLL = os.path.join(HERE, 'RevitAgent2020.dll')


def get_host_app():
    """拿 UIApplication。

    pyRevit 5 里 __revit__ 由 C# 执行器注入到 builtins（不是脚本全局），
    所以 globals() 取不到；优先用 revit.HOST_APP.uiapp。
    """
    host = getattr(revit, 'HOST_APP', None)
    if host is not None:
        app = getattr(host, 'uiapp', None)
        if app is not None:
            return app

    try:
        return __revit__  # noqa: F821  （builtin，可能直接就是 UIApplication）
    except NameError:
        return None


def is_ui_application(app):
    try:
        from Autodesk.Revit.UI import UIApplication
        return isinstance(app, UIApplication)
    except Exception:
        return True   # 拿不到类型就按 UIApplication 试


def load_agent_host():
    """返回 (AgentHost 类, 错误文本)。"""
    try:
        import clr
    except Exception:
        return None, (u'当前 pyRevit 引擎没有 .NET 桥（找不到 clr）。\n'
                      u'请在 pyRevit 设置里使用 IronPython 引擎，或给 CPython 安装 pythonnet。')

    try:
        if hasattr(clr, 'AddReferenceToFileAndPath'):
            clr.AddReferenceToFileAndPath(DLL)
        else:
            clr.AddReference(DLL)
        from RevitAgent.App import AgentHost
        return AgentHost, None
    except Exception:
        return None, traceback.format_exc()


def main():
    revit_app = get_host_app()
    if revit_app is None:
        forms.alert(
            u'拿不到 Revit 的 UIApplication。\n\n'
            u'请确认 pyRevit 版本 ≥ 4.8，并在 Revit 里点击该按钮。',
            title=u'AI 建模助手')
        return

    if not is_ui_application(revit_app):
        forms.alert(
            u'拿到的对象不是 UIApplication，无法打开聊天窗口。\n\n'
            u'请在 pyRevit 的命令上下文中运行（不要从 Python 控制台直接调用）。',
            title=u'AI 建模助手')
        return

    if not os.path.exists(DLL):
        forms.alert(
            u'找不到引擎文件：\n{0}\n\n'
            u'请确认按钮目录里有 RevitAgent2020.dll 和 skills 文件夹。'.format(DLL),
            title=u'AI 建模助手')
        return

    host, err = load_agent_host()
    if host is None:
        logger.error(err)
        forms.alert(
            u'AI 引擎加载失败：\n\n{0}\n\n'
            u'排查：\n'
            u'1) pyRevit 需用 IronPython 引擎（本按钮是 .NET 组件）；\n'
            u'2) Revit 2020 需要 .NET Framework 4.7.2 或更高；\n'
            u'3) DLL 与 skills 必须放在同一个按钮目录里。'.format(err),
            title=u'AI 建模助手')
        return

    try:
        host.Show(revit_app)
    except Exception:
        detail = traceback.format_exc()
        logger.error(detail)
        forms.alert(u'窗口启动失败：\n\n{0}'.format(detail), title=u'AI 建模助手')


if __name__ == '__main__':
    main()
