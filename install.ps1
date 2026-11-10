param([switch]$NoPause)
$ErrorActionPreference = "Stop"

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$zip = Join-Path $here "独立版\六吨工具箱.zip"
$addins = Join-Path $env:APPDATA "Autodesk\Revit\Addins\2020"
$target = Join-Path $addins "LiuDun"
$addinFile = Join-Path $addins "LiuDun.addin"
$oldAddin = Join-Path $addins "六吨工具箱.addin"

Write-Host "六吨工具箱 2.0 独立版 安装程序"
Write-Host ""

if (-not (Test-Path $zip)) {
    Write-Host "找不到安装包：$zip"
    if (-not $NoPause) { Read-Host "按回车退出" | Out-Null }
    exit 1
}

$running = @(Get-Process -Name "Revit" -ErrorAction SilentlyContinue)
if ($running.Count -gt 0) {
    Write-Host "检测到 Revit 正在运行，请先完全关闭 Revit 再安装。"
    if (-not $NoPause) { Read-Host "按回车退出" | Out-Null }
    exit 2
}

$tmp = Join-Path $env:TEMP ("liudun_" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $tmp -Force | Out-Null
Write-Host "正在解压..."
Expand-Archive -Path $zip -DestinationPath $tmp -Force
$payload = Join-Path $tmp "六吨工具箱"
if (-not (Test-Path (Join-Path $payload "LiuDun.dll"))) {
    Write-Host "解压后没有找到 LiuDun.dll，安装包可能不完整。"
    if (-not $NoPause) { Read-Host "按回车退出" | Out-Null }
    exit 1
}

New-Item -ItemType Directory -Path $addins -Force | Out-Null

# 旧的中文目录/清单：留着会和新的同时加载，这里只禁用清单，不删文件。
if (Test-Path $oldAddin) {
    try { Move-Item -Path $oldAddin -Destination ($oldAddin + ".disabled") -Force }
    catch { Write-Host "提示：旧清单 六吨工具箱.addin 无法重命名，请在 Revit 关闭后手动删除。" }
}

if (Test-Path $target) {
    try { Remove-Item $target -Recurse -Force }
    catch { Write-Host "旧目录删不掉（可能仍被 Revit 占用），请关闭 Revit 后重试。"; if (-not $NoPause) { Read-Host "按回车退出" | Out-Null }; exit 3 }
}
Copy-Item -Path $payload -Destination $target -Recurse -Force
Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue

$dll = Join-Path $target "LiuDun.dll"
$xml = @"
<?xml version="1.0" encoding="utf-8"?>
<RevitAddIns>
  <AddIn Type="Application">
    <Name>LiuDun</Name>
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
$logs = Join-Path $env:APPDATA "六吨工具箱\logs"
New-Item -ItemType Directory -Path $logs -Force | Out-Null

Write-Host ""
Write-Host "安装完成"
Write-Host "  程序目录：$target"
Write-Host "  清单文件：$addinFile"
Write-Host "  按钮数量：$count"
Write-Host "  运行日志：$logs"
Write-Host ""
Write-Host "请完全关闭 Revit 2020 再打开，功能区会出现「六吨工具箱」选项卡。"
Write-Host "卸载：删除程序目录和清单文件即可。"
if (-not $NoPause) { Read-Host "按回车退出" | Out-Null }
