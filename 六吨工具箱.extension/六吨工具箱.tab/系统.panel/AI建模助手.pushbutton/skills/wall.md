---
name: wall
description: 墙的创建、改高度/偏移、按条件批量调整
keywords: 墙,墙体,wall,砌体,隔墙,墙高
---

# 墙（Wall）

## 创建
1. 先 `revit_get_context` 看 `levels` 和 `wallTypes`，确认标高名与墙类型名。
2. 调用 `revit_create_wall`：
   - `start` / `end`：`[x, y]`，单位 mm；
   - `height`：墙高 mm，默认 3000；
   - `offset`：底部偏移 mm，默认 0；
   - `level` / `type` 留空时用默认标高和第一个基本墙类型。
3. 成功后用返回的 `elementId` 调 `revit_zoom` 定位给用户看。

## 修改
- 单面墙：`revit_set_param`，`param` 用「墙高」（未连接高度）、「底部偏移」、「顶部偏移」。
- 批量：`revit_update` + `filters`，例如：
  - 墙高大于 3000 的墙改成 3600：
    `{"category":"墙","filters":[{"param":"墙高","op":">","value":3000}],"changes":[{"param":"墙高","op":"set","value":3600}]}`
  - 底部偏移整体抬高 300：
    `changes:[{"param":"底部偏移","op":"add","value":300}]`
  - 只改标高1 上的墙：`filters` 加 `{"param":"标高","op":"contains","value":"标高1"}`

## 注意
- 「墙高」对应 Revit 的“未连接高度”，有附着顶的墙该参数只读，改不了要如实告诉用户。
- 墙的长度是曲线长度，不能直接改；要改长度请改起点/终点重新建，或移动端点（暂未提供工具）。
- 单位一律 mm，工具内部换算；不要在参数里传英尺。
