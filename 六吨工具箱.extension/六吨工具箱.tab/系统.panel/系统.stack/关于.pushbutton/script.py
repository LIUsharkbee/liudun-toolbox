# -*- coding: utf-8 -*-
"""关于 - 六吨工具箱

显示版本号、作者、GitHub 仓库链接，以及一句 star 邀请（带颜文字）。
"""
import os
import json
import traceback

import clr
clr.AddReference("RevitAPI")
clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")

from System.Diagnostics import Process

from pyrevit import forms, revit, script

doc = revit.doc
output = script.get_output()

REPO_OWNER = "LIUsharkbee"
REPO_NAME = "liudun-toolbox"
REPO_URL = "https://github.com/{0}/{1}".format(REPO_OWNER, REPO_NAME)
RELEASES_URL = REPO_URL + "/releases"
ISSUES_URL = REPO_URL + "/issues"


def extension_root():
    here = os.path.abspath(__file__)
    # ...\六吨工具箱.extension\六吨工具箱.tab\系统.panel\系统.stack\关于.pushbutton\script.py
    return os.path.abspath(os.path.join(os.path.dirname(here), "..", "..", "..", ".."))


def read_version():
    for base in (extension_root(), os.path.dirname(extension_root())):
        path = os.path.join(base, "version.json")
        if os.path.isfile(path):
            try:
                with open(path, "r") as fp:
                    data = json.load(fp)
                return data.get("version", u"?"), data.get("edition", u"")
            except Exception:
                pass
    return u"?", u""


def open_url(url):
    try:
        Process.Start(url)
        return True
    except Exception:
        pass
    try:
        os.startfile(url)
        return True
    except Exception:
        return False


def main():
    version, edition = read_version()
    is_special = (edition == "special")
    title = u"六吨工具箱" + (u"（番外特别版）" if is_special else u"")

    xaml = u"""<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="关于 六吨工具箱" Height="460" Width="560" WindowStartupLocation="CenterScreen">
  <StackPanel Margin="26">
    <TextBlock x:Name="titleText" Text="%s" FontSize="24" FontWeight="Bold"/>
    <TextBlock x:Name="verText" Text="版本 %s" FontSize="14" Foreground="#4A5568" Margin="0,6,0,16"/>

    <Border BorderBrush="#263043" BorderThickness="2" CornerRadius="12" Padding="14" Background="#FFF3FCF8">
      <StackPanel>
        <TextBlock Text="作者" FontWeight="Bold"/>
        <TextBlock x:Name="authorText" Text="%s" Margin="0,4,0,10"/>
        <TextBlock Text="仓库" FontWeight="Bold"/>
        <TextBlock Text="%s" Margin="0,4,0,10" Foreground="#2F5D9E"/>
        <TextBlock Text="问题反馈" FontWeight="Bold"/>
        <TextBlock Text="%s" Margin="0,4,0,0" Foreground="#2F5D9E"/>
      </StackPanel>
    </Border>

    <TextBlock Text="如果觉得好用的话，记得点一下 star！（◕‿◕✿）"
               FontSize="16" FontWeight="Bold" Foreground="#1F6F54" Margin="0,18,0,6"/>
    <TextBlock Text="你的一个 star，就是我继续更新的动力～"
               Foreground="#4A5568" Margin="0,0,0,16"/>

    <StackPanel Orientation="Horizontal">
      <Button x:Name="starBtn" Content="去点 star ★" Height="36" Width="140" FontWeight="Bold" Margin="0,0,10,0"/>
      <Button x:Name="releaseBtn" Content="查看版本历史" Height="36" Width="130" Margin="0,0,10,0"/>
      <Button x:Name="issueBtn" Content="反馈问题" Height="36" Width="110" Margin="0,0,10,0"/>
      <Button x:Name="closeBtn" Content="关闭" Height="36" Width="90"/>
    </StackPanel>
  </StackPanel>
</Window>""" % (title, version, u"LIUsharkbee（六吨）", REPO_URL, ISSUES_URL)

    window = forms.WPFWindow(xaml, literal_string=True)
    window.FindName("starBtn").Click += lambda s, e: open_url(REPO_URL + "/stargazers")
    window.FindName("releaseBtn").Click += lambda s, e: open_url(RELEASES_URL)
    window.FindName("issueBtn").Click += lambda s, e: open_url(ISSUES_URL)
    window.FindName("closeBtn").Click += lambda s, e: window.Close()
    window.ShowDialog()


try:
    main()
except Exception:
    forms.alert(u"关于窗口出错：\n%s" % traceback.format_exc()[-600:], title=u"关于", warn_icon=True)
