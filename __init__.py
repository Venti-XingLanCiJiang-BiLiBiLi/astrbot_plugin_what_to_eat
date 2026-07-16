"""
WhatToEat Plugin for AstrBot
=============================
入口文件，导出 WhatToEatPlugin 与 MenuManager。

数据存储位置（ASTRBOT规范）:
    data/plugin_data/what_to_eat/
    ├── menu.json     # 菜单数据
    └── config.json   # 插件配置
"""

from .main import WhatToEatPlugin, MenuManager, PLUGIN_NAME

__all__ = ["WhatToEatPlugin", "MenuManager", "PLUGIN_NAME"]
