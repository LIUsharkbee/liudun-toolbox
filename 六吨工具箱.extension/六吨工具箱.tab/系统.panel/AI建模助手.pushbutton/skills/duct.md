---
name: duct
description: 风管的创建、矩形/圆形尺寸修改、按系统类型批量扩宽
keywords: 风管,风道,duct,送风,回风,排风,风管宽度
---

# 风管（Duct）

## 创建
1. `revit_get_context` 查看 `ductTypes`、`mechanicalSystems`、`levels`。
2. 调用 `revit_create_duct`：
   - 矩形风管：给 `width` / `height`（mm）；
   - 圆形风管：给 `diameter`（mm）；
   - `systemType`：送风/回风/排风等系统名，留空用第一个；
   - `ductType`：风管类型名，留空用第一个。
3. 返回 `elementId` 后 `revit_zoom`。

## 修改尺寸
- 单根：`revit_set_param`（`宽度` / `高度` / `直径`）。
- 批量（系统类型为送风的风管宽度改成 500）：
  ```json
  {"category":"风管",
   "filters":[{"param":"系统类型","op":"contains","value":"送风"}],
   "changes":[{"param":"宽度","op":"set","value":500}]}
  ```
- 系统类型为送风、宽度小于 400 的风管加宽 100：
  ```json
  {"category":"风管",
   "filters":[{"param":"系统类型","op":"contains","value":"送风"},
              {"param":"宽度","op":"<","value":400}],
   "changes":[{"param":"宽度","op":"add","value":100}]}
  ```

## 注意
- 矩形风管才有「宽度/高度」，圆形风管只有「直径」；写错参数名会返回错误，按返回信息改。
- 风管系统类型与风管类型是两个东西：`systemType` 决定送/回/排风，`ductType` 决定尺寸系列。
- 先查后改，改完再查核对；不要一次改整张图而不报数量。
