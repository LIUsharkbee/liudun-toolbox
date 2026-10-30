---
name: filter
description: 属性过滤 + 批量改参数的标准流程与写法
keywords: 过滤,筛选,大于,小于,批量,修改,条件,filter
---

# 属性过滤与批量修改

## 标准流程（必须按顺序）
1. `revit_query` 用同样的 `filters` 查一次，拿到 `count` 和样例元素。
2. 把命中数量告诉用户（例如「直径大于 100 的管道有 37 根」）。
3. 调 `revit_update` 执行修改。
4. 再 `revit_query` 一次核对；或直接用 update 返回的 `updated` / `errors` 汇报。

## filters 写法
```json
{"param": "直径", "op": ">", "value": 100, "unit": "mm"}
```
- `op`：`>` `>=` `<` `<=` `==` `!=` `contains`
- `param`：参数中文名或英文名（直径/Diameter、宽度/Width、高度/Height、墙高、底部偏移、系统类型、标高、类型名称）
- `value`：长度类参数按 mm；字符串用 contains 最稳
- 多个条件放进数组 = AND

## changes 写法
```json
{"param": "直径", "op": "add", "value": 20}
```
- `set`：直接赋值
- `add`：原值 + value（长度按 mm）
- `multiply`：原值 × value（系数）

## 常见需求对照
| 用户说法 | filters | changes |
|---|---|---|
| 直径大于 100 的管道直径增大 | 直径 > 100 | 直径 add N |
| 墙高大于 3000 的墙改成 3600 | 墙高 > 3000 | 墙高 set 3600 |
| 系统类型为送风的风管宽度扩大 | 系统类型 contains 送风 | 宽度 set N / multiply 1.x |
| 某个标高上的管道 | 标高 contains 标高1 | — |

## 注意事项
- 参数只读（IsReadOnly）会报错，逐个元素收集错误并在回答里说明数量。
- 结果里 `errors` 最多返回 10 条，告诉用户「共 N 个错误，示例……」。
- 大批量修改前提醒用户先保存/备份模型。
