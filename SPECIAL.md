# 番外特别版（Special Edition）

> 正式版**不含**「启动我的世界」按钮；那是番外版独有的。
> 两个版本**目录同名**，只能装一个，覆盖即切换。

## 什么是番外版

和正式版**完全相同**的一套工具，只多一个按钮：

| 按钮 | 位置 | 作用 |
|---|---|---|
| 启动我的世界 | 系统面板 | 从 Revit 里一键拉起 PCL2（Plain Craft Launcher 2） |

它的用途比较私人：建模卡住的时候，点一下直接进游戏，不用切桌面找启动器。

## 下载

**https://github.com/LIUsharkbee/liudun-toolbox/releases/tag/v1.3.2-special**

```
liudun-toolbox-1.3.2-special.zip
```

该 Release 标记为 `prerelease`（预发布），不会挤在正式版列表里。

## 安装

和正式版一样：

1. 解压 zip；
2. 把 `六吨工具箱.extension` 整个文件夹覆盖到：

```
%APPDATA%\pyRevit\Extensions\
```

3. 重启 Revit；
4. 系统面板会多出「启动我的世界」。

> 如果之前装的是正式版，直接覆盖就是升级到番外版；
> 想回正式版，把正式版 zip 的 `六吨工具箱.extension` 再覆盖一次即可。

## 路径怎么找

按钮按这个顺序查找 PCL2：

1. `%APPDATA%\六吨工具箱\pcl2.json` 里存的路径（选过一次就会记住）；
2. 常见位置自动探测：
   - `D:\PCL2\Plain Craft Launcher 2.exe`
   - `D:\PCL2\PCL2.exe`
   - 桌面 / 下载目录
3. 都没有就弹文件选择框，选一次后记住。

想手动指定：编辑 `%APPDATA%\六吨工具箱\pcl2.json`：

```json
{
  "path": "D:\\PCL2\\Plain Craft Launcher 2.exe"
}
```

## 为什么不能反过来

经常有人问「能不能在 PCL2 里启动 Revit」——不能：

- PCL2 是《我的世界》专用启动器，只会启动 Java 版游戏；
- 它没有「运行任意 exe」的入口，也不提供插件接口。

所以整合方向只能是 **Revit → PCL2**。

## 和正式版的关系

| 项 | 正式版 | 番外版 |
|---|---|---|
| 版本号 | 1.3.2 | 1.3.2（`version.json` 里多一个 `"edition": "special"`） |
| 功能 | 全部 | 全部 + 启动我的世界 |
| 检查更新 | 跟随 `main` 分支 | **不跟随**（正式版更新会覆盖掉这个按钮） |

> 番外版是手动分发的，不走自动更新机制。
> 如果你点了「检查更新」并覆盖安装，MC 按钮会消失——这不是 bug。

## 文件清单

```
liudun-toolbox-1.3.2-special.zip
└── 六吨工具箱.extension/
    ├── version.json                 {"version":"1.3.2","edition":"special"}
    └── 六吨工具箱.tab/
        ├── 建筑.panel/              与正式版一致
        ├── 楼梯.panel/              与正式版一致
        ├── 数据.panel/              与正式版一致
        ├── 通风.panel/              与正式版一致
        └── 系统.panel/
            ├── AI建模助手.pushbutton
            ├── 性能优化.pushbutton
            ├── 启动我的世界.pushbutton    ← 番外版独有
            └── 系统.stack
```
