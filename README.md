# WhatToEat 插件 — AstrBot 吃什么菜单

[![License: CC BY-NC-SA 4.0](https://img.shields.io/badge/License-CC%20BY--NC--SA%204.0-lightgrey.svg)](https://creativecommons.org/licenses/by-nc-sa/4.0/)
Licensed under CC BY-NC-SA 4.0 — 署名 · 非商用 · 相同方式共享。

一个实用的吃什么菜单插件，支持菜品管理、随机推荐、导出导入、**WebUI设置页面**与 **AI Agent 程序化接口**。

## 安装

1. 将本文件夹 `what_to_eat` 复制到 AstrBot 的 `data/plugins/` 目录下。
2. 重启 AstrBot，插件会自动加载。
3. 菜单数据持久化保存在 `data/plugin_data/what_to_eat/menu.json` 中，重启不丢失。

## 指令列表

| 命令                       | 功能                                                                                 |
| -------------------------- | ------------------------------------------------------------------------------------ |
| `添加菜单 菜名1,菜名2,...` | 传入以逗号间隔的多个菜品，加入菜单文件，并**引用原消息**发送回执                     |
| `菜单统计`                 | 统计菜品数量并输出消息                                                               |
| `删除菜品 菜名`            | 传入一个菜品名称，删除菜单中这个菜品，并**引用原消息**发送回执。兼顾菜品不存在的情况 |
| `吃什么`                   | 随机菜品并输出：`今天吃{菜品}喵~`                                                    |
| `开席`                     | 随机 5 个菜品（不重复），输出列表                                                    |
| `清空菜单`                 | 清空菜单文件                                                                         |
| `导出菜单`                 | 导出当前菜单为 JSON 格式文本，可直接复制保存                                         |
| `导入菜单 JSON/文本`       | 批量导入菜品。支持 JSON 数组 `"["红烧肉","糖醋排骨"]` 或逗号/换行分隔的文本          |

### 使用示例

````
用户: 添加菜单 红烧肉,糖醋排骨,宫保鸡丁
Bot: ✅ 成功添加 3 个菜品：红烧肉, 糖醋排骨, 宫保鸡丁

用户: 吃什么
Bot: 今天吃红烧肉喵~

用户: 开席
Bot: 🍽️ 今日宴席菜单：1. 糖醋排骨｜2. 宫保鸡丁｜3. 红烧肉｜4. 麻婆豆腐｜5. 清蒸鲈鱼

用户: 导出菜单
Bot: 📤 当前共 5 道菜品，请复制下面的内容保存：
Bot: ```json
      ["红烧肉","糖醋排骨","宫保鸡丁","麻婆豆腐","清蒸鲈鱼"]
      ```

用户: 导入菜单 ["酸菜鱼","火锅"]
Bot: ✅ 成功导入 2 个菜品：酸菜鱼, 火锅
````

## WebUI 设置页面

插件提供 WebUI 设置页面，可以更方便地管理菜单和数据。

### 访问方式

1. 进入 AstrBot WebUI
2. 进入「插件」页面
3. 找到「what_to_eat」插件
4. 点击进入详情，点击「settings」页面

### 功能特性

- **菜单管理**：可视化添加/删除菜品
- **数据存储**：插件存储的json数据位于\AstrBot\data\plugin_data\what_to_eat\menus\文件夹下
- **随机推荐**：一键随机推荐菜品
- **开席推荐**：随机推荐多道菜品组成宴席
- **回复模板自定义**：可以修改机器人的回复格式

## AI Agent 接口

本插件在初始化后，将 `MenuManager` 实例挂载在 `WhatToEatPlugin.menu` 上，供 AI Agent（或其他插件）直接调用：

```python
# 假设 agent 通过 context 获取到插件实例
plugin = context.get_plugin("what_to_eat")
menu = plugin.menu          # MenuManager 实例

# 1. 获取全部菜品
all_dishes = menu.get_all()     # List[str]

# 2. 获取数量
cnt = menu.count()              # int

# 3. 批量添加（自动去重、持久化）
result = menu.add(["酸菜鱼", "火锅"])
# result = {"added": ["酸菜鱼", "火锅"], "skipped": []}

# 4. 删除菜品
ok = menu.remove("红烧肉")      # bool

# 5. 清空
menu.clear()

# 6. 随机一道菜
dish = menu.random_one()        # str

# 7. 随机 n 道不重复的菜
dishes = menu.random_n(5)       # List[str]

# 8. 导出 JSON
json_str = menu.export_json()   # str

# 9. 导入数据（支持 JSON 数组或逗号/换行分隔文本）
result = menu.import_data('["红烧肉","糖醋排骨"]')
# result = {"added": [...], "skipped": [...], "invalid": 0}
```

### 接口说明表

| 方法                    | 参数        | 返回值                                         | 说明                                    |
| ----------------------- | ----------- | ---------------------------------------------- | --------------------------------------- |
| `menu.get_all()`        | —           | `List[str]`                                    | 获取当前菜单副本                        |
| `menu.count()`          | —           | `int`                                          | 菜品总数                                |
| `menu.add(dishes)`      | `List[str]` | `{"added": [...], "skipped": [...]}`           | 批量添加，自动去重并落盘                |
| `menu.remove(dish)`     | `str`       | `bool`                                         | 删除指定菜品，成功返回 `True`           |
| `menu.clear()`          | —           | —                                              | 清空菜单并落盘                          |
| `menu.random_one()`     | —           | `str`                                          | 随机一道菜；空菜单返回 `""`             |
| `menu.random_n(n)`      | `int`       | `List[str]`                                    | 随机 `n` 道不重复的菜；不足时返回全部   |
| `menu.export_json()`    | —           | `str`                                          | 将菜单导出为 JSON 字符串                |
| `menu.import_data(raw)` | `str`       | `{"added": [], "skipped": [], "invalid": int}` | 从 JSON 数组或逗号/换行分隔文本导入菜品 |

## 数据存储

根据 AstrBot 规范，插件数据存储在 `data/plugin_data/what_to_eat/` 目录下：

```
data/plugin_data/what_to_eat/
├── menu.json     # 菜单数据
└── config.json   # 插件配置（包含回复模板等设置）
```

这样做的好处是：更新或重装插件不会丢失数据。

## 许可与致谢

本项目的娱乐模块参考并借鉴自插件 "Self Evolution"（作者：Renyus），对其实现做了必要修改。原始仓库：https://github.com/Renyus/astrbot_plugin_self_evolution

本项目中上述借鉴内容以 Creative Commons Attribution-NonCommercial 4.0 International (CC BY-NC 4.0) 许可证进行使用与共享。许可证全文请参见：https://creativecommons.org/licenses/by-nc/4.0/ 。如需商业使用或有其它授权问题，请联系原作者。
