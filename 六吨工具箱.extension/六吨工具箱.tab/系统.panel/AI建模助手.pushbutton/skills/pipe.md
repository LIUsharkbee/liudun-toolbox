---
name: pipe
description: 管道的创建、改直径、按系统类型/直径过滤批量调整
keywords: 管道,pipe,管径,直径,水管,水管管径
---

# 管道（Pipe）

## 创建
1. `revit_get_context` 查看 `levels`、`pipeTypes`、`pipingSystems`。
2. 调用 `revit_create_pipe`：
   - `start` / `end`：`[x, y]`，mm；
   - `diameter`：公称直径 mm，默认 100；
   - `systemType`：管道系统名（如「家用冷水」「家用热水」），留空用第一个；
   - `pipeType`：管道类型名，留空用第一个。
3. 返回 `elementId` 后 `revit_zoom` 定位。

## 修改直径
- 单根：`revit_set_param`（`param` = 直径，`value` mm）。
- 批量：
  - 直径大于 100 的管道统一增大 20：
    ```json
    {"category":"管道","filters":[{"param":"直径","op":">","value":100}],
     "changes":[{"param":"直径","op":"add","value":20}]}
    ```
  - 直径大于 100 的管道统一改成 150：
    `changes:[{"param":"直径","op":"set","value":150}]`
  - 直径大于 100 的管道放大 1.25 倍：
    `changes:[{"param":"直径","op":"multiply","value":1.25}]`
  - 只改某个系统：`filters` 加 `{"param":"系统类型","op":"contains","value":"家用冷水"}`

## 注意
- 直径参数名是「直径 / Diameter」；管道系统参数名是「系统类型 / System Type」，用 contains 更稳。
- 先 `revit_query` 报命中数量，再改；改完再查一次核对直径。
- 连接件（弯头、三通）不会跟着变径，需要用户确认是否处理；不要擅自改管件。
