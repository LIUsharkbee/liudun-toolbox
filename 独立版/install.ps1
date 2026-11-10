param([switch]$NoPause)
$ErrorActionPreference = "Stop"

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$payload = Join-Path $here "六吨工具箱"
$addins = Join-Path $env:APPDATA "Autodesk\Revit\Addins\2020"
$target = Join-Path $addins "六吨工具箱"
$addinFile = Join-Path $addins "六吨工具箱.addin"

Write-Host "六吨工具箱 2.0 独立版 安装程序"
Write-Host ""

if (-not (Test-Path $payload)) {
    Write-Host "找不到发布目录：$payload"
    if (-not $NoPause) { Read-Host "按回车退出" | Out-Null }
    exit 1
}

New-Item -ItemType Directory -Path $addins -Force | Out-Null
if (Test-Path $target) { Remove-Item $target -Recurse -Force }
Copy-Item -Path $payload -Destination $target -Recurse -Force

$dll = Join-Path $target "LiuDun.dll"
if (-not (Test-Path $dll)) {
    Write-Host "复制后没有找到 LiuDun.dll，安装失败。"
    if (-not $NoPause) { Read-Host "按回车退出" | Out-Null }
    exit 1
}

$xml = @"
<?xml version="1.0" encoding="utf-8"?>
<RevitAddIns>
  <AddIn Type="Application">
    <Name>六吨工具箱</Name>
    <Assembly>$dll</Assembly>
    <FullClassName>LiuDun.App</FullClassName>
    <ClientId>6f3d2c1a-8b4e-4a55-9f2d-1c7b0a9e3d41</ClientId>
    <VendorId>LIUDUN</VendorId>
    <VendorDescription>六吨工具箱 2.0 独立版</VendorDescription>
  </AddIn>
</RevitAddIns>
"@
$utf8 = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($addinFile, $xml, $utf8)

$count = (Get-ChildItem (Join-Path $target "scripts") -Directory).Count

Write-Host ""
Write-Host "安装完成"
Write-Host "  程序目录：$target"
Write-Host "  清单文件：$addinFile"
Write-Host "  按钮数量：$count"
Write-Host ""
Write-Host "请完全关闭 Revit 2020 再打开，功能区会出现「六吨工具箱」。"
Write-Host "卸载：删除上面两个路径即可。"
if (-not $NoPause) { Read-Host "按回车退出" | Out-Null }
