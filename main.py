"""
WhatToEat Plugin for AstrBot
=============================
一个实用的吃什么菜单插件，支持：
- 添加/删除/清空菜品（按群/私聊持久化存储）
- 随机推荐（单菜 / 开席多道菜）
- 菜单统计、导出、导入
- AI Agent 程序化接口
- WebUI 设置页面
- 通过 _conf_schema.json 配置触发关键词、菜单上限、摆酒席冷却等
"""

import json
import os
import random
import re
import shutil
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageEventResult, filter
from astrbot.api.star import Context, Star, register
from quart import jsonify, request

PLUGIN_NAME = "what_to_eat"
BANQUET_WINDOW_SECONDS = 300


class MenuManager:
    """菜单数据管理器，负责持久化存储与核心逻辑。"""

    def __init__(self, file_path: str):
        self._file_path = file_path
        self._menu: List[str] = []
        self._load()

    def _load(self):
        if os.path.exists(self._file_path):
            try:
                with open(self._file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        seen = set()
                        self._menu = []
                        for item in data:
                            if isinstance(item, str) and item.strip() and item not in seen:
                                seen.add(item)
                                self._menu.append(item)
            except Exception:
                self._menu = []
        else:
            self._menu = []

    def _save(self):
        os.makedirs(os.path.dirname(self._file_path), exist_ok=True)
        with open(self._file_path, "w", encoding="utf-8") as f:
            json.dump(self._menu, f, ensure_ascii=False, indent=2)

    def add(self, dishes: List[str], max_items: int = 0) -> dict:
        """批量添加菜品，max_items > 0 时限制菜单总数量。"""
        added, skipped, rejected = [], [], []
        for d in dishes:
            d = d.strip()
            if not d:
                continue
            if d in self._menu:
                skipped.append(d)
                continue
            if max_items > 0 and len(self._menu) >= max_items:
                rejected.append(d)
                continue
            self._menu.append(d)
            added.append(d)
        if added:
            self._save()
        return {"added": added, "skipped": skipped, "rejected": rejected}

    def remove(self, dish: str) -> bool:
        dish = dish.strip()
        if dish in self._menu:
            self._menu.remove(dish)
            self._save()
            return True
        return False

    def clear(self):
        self._menu.clear()
        self._save()

    def get_all(self) -> List[str]:
        return self._menu.copy()

    def count(self) -> int:
        return len(self._menu)

    def random_one(self) -> str:
        return random.choice(self._menu) if self._menu else ""

    def random_n(self, n: int = 5) -> List[str]:
        if n >= len(self._menu):
            return self._menu.copy()
        return random.sample(self._menu, n)

    def export_json(self) -> str:
        return json.dumps(self._menu, ensure_ascii=False, indent=2)

    def import_data(self, raw: str, max_items: int = 0) -> dict:
        raw = raw.strip()
        if not raw:
            return {"added": [], "skipped": [], "rejected": [], "invalid": 0}

        dishes: List[str] = []
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                dishes = [str(x).strip() for x in parsed if str(x).strip()]
        except json.JSONDecodeError:
            dishes = [d.strip() for d in re.split(r"[,，\n]", raw) if d.strip()]

        if not dishes:
            return {"added": [], "skipped": [], "rejected": [], "invalid": 0}

        result = self.add(dishes, max_items=max_items)
        result["invalid"] = 0
        return result


class MenuStore:
    """按会话（群/私聊）管理独立菜单。"""

    def __init__(self, data_dir: Path, init_from_default: bool = False):
        self._data_dir = data_dir
        self._menus_dir = data_dir / "menus"
        self._menus_dir.mkdir(parents=True, exist_ok=True)
        self._cache: Dict[str, MenuManager] = {}
        self._default = MenuManager(str(self._menus_dir / "default.json"))
        self._init_from_default = init_from_default
        self._migrate_legacy_menu()

    def _migrate_legacy_menu(self):
        legacy = self._data_dir / "menu.json"
        default_path = self._menus_dir / "default.json"
        if legacy.exists() and not default_path.exists():
            shutil.copy(legacy, default_path)
            self._default = MenuManager(str(default_path))

    @property
    def default(self) -> MenuManager:
        return self._default

    def _get_or_create(self, key: str) -> MenuManager:
        if key not in self._cache:
            path = self._menus_dir / f"{key}.json"
            is_new = not path.exists()
            if is_new:
                legacy = self._data_dir / "menu.json"
                if legacy.exists():
                    shutil.copy(legacy, path)
                    is_new = False
            self._cache[key] = MenuManager(str(path))
            # 开关①：新会话空菜单自动继承默认菜单
            if is_new and self._init_from_default and self._default.count() > 0:
                self._cache[key].add(self._default.get_all())
                logger.info("[what_to_eat] 新菜单 %s 已自动初始化为默认菜单", key)
            elif is_new:
                # 开关未开启，创建空菜单 JSON 文件
                self._cache[key]._save()
                logger.debug("[what_to_eat] 新菜单 %s 已创建为空菜单", key)
        return self._cache[key]

    def get_for_scope(self, scope: str, target_id: str = "") -> MenuManager:
        scope = (scope or "").strip().lower()
        if scope == "group":
            target = (target_id or "").strip()
            if target:
                return self._get_or_create(f"group_{target}")
            return self.default
        if scope == "private":
            target = (target_id or "").strip()
            if target:
                return self._get_or_create(f"private_{target}")
            return self.default
        return self.default

    def session_key(self, event: AstrMessageEvent) -> str:
        group_id = getattr(event.message_obj, "group_id", "") or ""
        if group_id:
            return f"group_{group_id}"
        return f"private_{event.get_session_id()}"

    def get_for_event(self, event: AstrMessageEvent) -> MenuManager:
        return self.get_for_scope("group" if getattr(event.message_obj, "group_id", "") else "private", str(getattr(event.message_obj, "group_id", "") or event.get_session_id()))

    def get_all_menu_keys(self) -> List[str]:
        """返回所有非默认菜单文件名的 stem 列表。"""
        keys = []
        for f in self._menus_dir.iterdir():
            if f.suffix == '.json' and f.stem != 'default':
                keys.append(f.stem)
        return keys


def get_plugin_data_path() -> Path:
    from astrbot.core.utils.astrbot_path import get_astrbot_data_path

    return Path(get_astrbot_data_path()) / "plugin_data" / PLUGIN_NAME


@register(PLUGIN_NAME, "StarryLanMusic", "吃什么菜单插件", "1.3.0")
class WhatToEatPlugin(Star):
    """AstrBot 插件封装，暴露 self.menu 给 AI Agent 调用。"""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

        data_dir = get_plugin_data_path()
        self._menu_store = MenuStore(data_dir, init_from_default=self._init_from_default_enabled())
        self.menu = self._menu_store.default

        self._banquet_history: Dict[str, List[float]] = {}
        self._banquet_cooldown_until: Dict[str, float] = {}

        logger.info("[what_to_eat] 插件已加载，数据目录: %s", str(data_dir))
        logger.debug("[what_to_eat] 默认菜单共 %d 道菜品", self.menu.count())

        self._register_web_api()

    def _max_items(self) -> int:
        return int(self.config.get("meal_max_items", 500) or 500)

    def _feast_count(self) -> int:
        return int(self.config.get("feast_count", 5) or 5)

    def _init_from_default_enabled(self) -> bool:
        return bool(self.config.get("init_from_default", False))

    def _sync_default_enabled(self) -> bool:
        return bool(self.config.get("sync_default_to_all", False))

    def _banquet_rate_limit(self) -> int:
        return int(self.config.get("meal_banquet_count", 5) or 5)

    def _banquet_cooldown_minutes(self) -> int:
        return int(self.config.get("meal_banquet_cooldown_minutes", 5) or 5)

    def _sync_empty_menus_from_default(self):
        """遍历所有菜单文件，将空菜单初始化为默认菜单内容。"""
        if not self._init_from_default_enabled():
            return
        default_dishes = self.menu.get_all()
        if not default_dishes:
            return
        for key in self._menu_store.get_all_menu_keys():
            mgr = self._menu_store._get_or_create(key)
            if mgr.count() == 0:
                mgr.add(default_dishes, max_items=self._max_items())
                logger.info("[what_to_eat] 已初始化空菜单 %s 为默认菜单", key)

    def _sync_default_to_all_menus(self):
        """将默认菜单的菜品同步至所有其他菜单。"""
        if not self._sync_default_enabled():
            return
        default_dishes = self.menu.get_all()
        if not default_dishes:
            return
        for key in self._menu_store.get_all_menu_keys():
            mgr = self._menu_store._get_or_create(key)
            result = mgr.add(default_dishes, max_items=self._max_items())
            if result["added"]:
                logger.info("[what_to_eat] 已将默认菜单同步至 %s，新增 %d 道菜", key, len(result["added"]))

    def _keyword_list(self, key: str) -> List[str]:
        value = self.config.get(key, [])
        if not isinstance(value, list):
            return []
        return [str(kw).strip() for kw in value if str(kw).strip()]

    def _parse_keyword_command(self, text: str) -> Tuple[Optional[str], str]:
        text = text.strip()
        mappings = [
            ("add", self._keyword_list("meal_add_keywords")),
            ("del", self._keyword_list("meal_del_keywords")),
            ("delall", self._keyword_list("meal_delall_keywords")),
            ("eat", self._keyword_list("meal_eat_keywords")),
            ("banquet", self._keyword_list("meal_banquet_keywords")),
        ]
        for action, keywords in mappings:
            for kw in sorted(keywords, key=len, reverse=True):
                if text == kw:
                    return action, ""
                if text.startswith(kw):
                    rest = text[len(kw):]
                    if not rest or rest[0] in " \t":
                        return action, rest.strip()
        return None, ""

    def _check_banquet_allowed(self, event: AstrMessageEvent) -> Tuple[bool, str]:
        key = self._menu_store.session_key(event)
        now = time.time()
        cooldown_until = self._banquet_cooldown_until.get(key, 0)
        if now < cooldown_until:
            remain = int((cooldown_until - now + 59) // 60)
            return False, f"⏳ 摆酒席冷却中，请 {max(remain, 1)} 分钟后再试喵~"

        history = [ts for ts in self._banquet_history.get(key, []) if now - ts < BANQUET_WINDOW_SECONDS]
        self._banquet_history[key] = history
        if len(history) >= self._banquet_rate_limit():
            cooldown = self._banquet_cooldown_minutes() * 60
            self._banquet_cooldown_until[key] = now + cooldown
            return False, (
                f"⏳ 5 分钟内摆酒席次数已达上限（{self._banquet_rate_limit()} 次），"
                f"请 {self._banquet_cooldown_minutes()} 分钟后再试喵~"
            )
        return True, ""

    def _record_banquet(self, event: AstrMessageEvent):
        key = self._menu_store.session_key(event)
        self._banquet_history.setdefault(key, []).append(time.time())

    def _make_reply_result(self, event: AstrMessageEvent, text: str) -> MessageEventResult:
        msg_id = getattr(event.message_obj, "message_id", None) or ""
        result = MessageEventResult()
        result.chain = [
            Comp.Reply(id=msg_id),
            Comp.Plain(text=text),
        ]
        return result

    def _register_web_api(self):
        ctx = self.context
        routes = [
            (f"/{PLUGIN_NAME}/menu", self._api_get_menu, ["GET"], "获取菜单数据"),
            (f"/{PLUGIN_NAME}/menu/add", self._api_add_dishes, ["POST"], "添加菜品"),
            (f"/{PLUGIN_NAME}/menu/delete", self._api_delete_dish, ["POST"], "删除菜品"),
            (f"/{PLUGIN_NAME}/menu/clear", self._api_clear_menu, ["POST"], "清空菜单"),
            (f"/{PLUGIN_NAME}/menu/export", self._api_export_menu, ["GET"], "导出菜单"),
            (f"/{PLUGIN_NAME}/menu/import", self._api_import_menu, ["POST"], "导入菜单"),
            (f"/{PLUGIN_NAME}/config", self._api_get_config, ["GET"], "获取插件配置"),
            (f"/{PLUGIN_NAME}/config/save", self._api_save_config, ["POST"], "保存插件配置"),
            (f"/{PLUGIN_NAME}/menu/random", self._api_random_pick, ["GET"], "随机推荐菜品"),
            (f"/{PLUGIN_NAME}/log", self._api_log, ["POST"], "记录日志到AstrBot"),
            (f"/{PLUGIN_NAME}/menu/search_ids", self._api_search_ids, ["GET"], "搜索已存在的群号/用户ID"),
        ]
        for path, handler, methods, desc in routes:
            ctx.register_web_api(path, handler, methods, desc)

    async def _get_request_payload(self) -> dict:
        payload = {}

        try:
            json_payload = await request.get_json(silent=True)
            if isinstance(json_payload, dict):
                payload.update(json_payload)
        except Exception:
            pass

        if not payload:
            try:
                form_data = await request.form
                if form_data:
                    for key in form_data.keys():
                        values = form_data.getlist(key)
                        if len(values) > 1:
                            payload[key] = values
                        else:
                            payload[key] = values[0]
            except Exception:
                pass

        if not payload:
            try:
                payload = {key: value for key, value in request.args.items()}
            except Exception:
                payload = {}

        return payload

    def _resolve_menu_context(self, payload: Optional[dict] = None):
        scope = (request.args.get("scope", "") or "").strip().lower()
        target_id = (request.args.get("target_id", "") or "").strip()
        payload = payload or {}
        if not scope and isinstance(payload, dict):
            scope = str(payload.get("scope", "") or "").strip().lower()
            target_id = str(payload.get("target_id", "") or "").strip()
        if not scope:
            scope = "default"
        menu = self._menu_store.get_for_scope(scope, target_id)
        return menu, scope, target_id

    async def _api_get_menu(self):
        menu, scope, target_id = self._resolve_menu_context()
        return jsonify({
            "menu": menu.get_all(),
            "count": menu.count(),
            "scope": scope,
            "target_id": target_id,
        })

    async def _api_add_dishes(self):
        payload = await self._get_request_payload()
        menu, _, _ = self._resolve_menu_context(payload)
        dishes = payload.get("dishes", [])
        if isinstance(dishes, str):
            dishes = [dishes]
        result = menu.add(dishes, max_items=self._max_items())

        # 如果操作的是默认菜单且启用了同步，同步至所有其他菜单
        if result["added"] and self._sync_default_enabled() and menu is self.menu:
            self._sync_default_to_all_menus()

        return jsonify(result)

    async def _api_delete_dish(self):
        # 优先从 JSON body 获取，再尝试 URL query params（兼容 bridge SDK 的编码差异）
        payload = await self._get_request_payload()
        dish = payload.get("dish", "") or request.args.get("dish", "") or ""
        menu, _, _ = self._resolve_menu_context(payload)
        dish = dish.strip()
        if not dish:
            return jsonify({"success": False, "error": "dish is required"}), 400
        ok = menu.remove(dish)
        logger.info("[what_to_eat] 删除菜品: dish=%s, ok=%s, payload=%s", dish, ok, payload)
        return jsonify({"success": ok, "dish": dish})

    async def _api_clear_menu(self):
        payload = await self._get_request_payload()
        menu, _, _ = self._resolve_menu_context(payload)
        menu.clear()
        return jsonify({"success": True})

    async def _api_export_menu(self):
        payload = await self._get_request_payload()
        menu, _, _ = self._resolve_menu_context(payload)
        return jsonify({
            "menu": menu.get_all(),
            "json": menu.export_json(),
        })

    async def _api_import_menu(self):
        payload = await self._get_request_payload()
        menu, _, _ = self._resolve_menu_context(payload)
        data = payload.get("data", "")
        result = menu.import_data(data, max_items=self._max_items())
        return jsonify(result)

    def _config_snapshot(self) -> dict:
        return {
            "meal_max_items": self._max_items(),
            "meal_add_keywords": self._keyword_list("meal_add_keywords"),
            "meal_del_keywords": self._keyword_list("meal_del_keywords"),
            "meal_delall_keywords": self._keyword_list("meal_delall_keywords"),
            "meal_eat_keywords": self._keyword_list("meal_eat_keywords"),
            "meal_banquet_keywords": self._keyword_list("meal_banquet_keywords"),
            "meal_banquet_count": self._banquet_rate_limit(),
            "meal_banquet_cooldown_minutes": self._banquet_cooldown_minutes(),
            "feast_count": self._feast_count(),
            "random_reply_template": self.config.get("random_reply_template", "今天吃{dish}喵~"),
            "feast_reply_template": self.config.get("feast_reply_template", "🍽️ 今日宴席菜单：{dishes}"),
            "init_from_default": self._init_from_default_enabled(),
            "sync_default_to_all": self._sync_default_enabled(),
        }

    async def _api_get_config(self):
        return jsonify(self._config_snapshot())

    async def _api_save_config(self):
        payload = await request.get_json(force=True, silent=True) or {}
        allowed_keys = [
            "meal_max_items",
            "meal_add_keywords",
            "meal_del_keywords",
            "meal_delall_keywords",
            "meal_eat_keywords",
            "meal_banquet_keywords",
            "meal_banquet_count",
            "meal_banquet_cooldown_minutes",
            "feast_count",
            "random_reply_template",
            "feast_reply_template",
            "init_from_default",
            "sync_default_to_all",
        ]
        for key in allowed_keys:
            if key in payload:
                self.config[key] = payload[key]
        self.config.save_config()

        # 热重载：根据开关状态执行菜单同步
        self._sync_empty_menus_from_default()
        self._sync_default_to_all_menus()

        return jsonify({"success": True, "config": self._config_snapshot()})

    async def _api_random_pick(self):
        payload = await self._get_request_payload()
        menu, _, _ = self._resolve_menu_context(payload)
        feast_count = self._feast_count()
        count = request.args.get("count", 1, type=int)
        if count == 1:
            return jsonify({"dish": menu.random_one()})
        dishes = menu.random_n(min(count, feast_count))
        return jsonify({"dishes": dishes})

    async def _api_search_ids(self):
        """根据 scope 和 q 搜索已存在的群号/用户ID，最多返回 6 条匹配。"""
        scope = (request.args.get("scope", "") or "").strip().lower()
        q = (request.args.get("q", "") or "").strip().lower()

        if scope not in ("group", "private") or not q:
            return jsonify({"ids": []})

        prefix = f"{scope}_"
        matches = []
        for f in self._menu_store._menus_dir.iterdir():
            if f.suffix == '.json' and f.stem.startswith(prefix):
                id_part = f.stem[len(prefix):]
                if q in id_part.lower():
                    matches.append(id_part)

        matches.sort()
        return jsonify({"ids": matches[:6]})

    async def _api_log(self):
        """记录来自前端的日志到 AstrBot logger。"""
        payload = await self._get_request_payload()
        level = (payload.get("level", "info") or "").strip().lower()
        message = payload.get("message", "")
        if not message:
            return jsonify({"success": False, "error": "message is required"})
        log_fn = {"debug": logger.debug, "info": logger.info, "warn": logger.warning, "error": logger.error}.get(level, logger.info)
        log_fn("[what_to_eat WebUI] %s", message)
        return jsonify({"success": True})

    # ----------------- 可配置关键词指令 -----------------

    @filter.event_message_type(filter.EventMessageType.ALL, priority=5)
    async def on_meal_keyword(self, event: AstrMessageEvent):
        action, args = self._parse_keyword_command(event.message_str)
        if not action:
            return

        menu = self._menu_store.get_for_event(event)
        max_items = self._max_items()

        if action == "add":
            async for result in self._handle_add(event, menu, args, max_items):
                yield result
        elif action == "del":
            async for result in self._handle_remove(event, menu, args):
                yield result
        elif action == "delall":
            async for result in self._handle_clear(event, menu):
                yield result
        elif action == "eat":
            async for result in self._handle_random(event, menu):
                yield result
        elif action == "banquet":
            async for result in self._handle_feast(event, menu):
                yield result

        event.stop_event()

    async def _handle_add(
        self, event: AstrMessageEvent, menu: MenuManager, args: str, max_items: int
    ):
        if not args.strip():
            yield event.plain_result("请传入要添加的菜品，多个菜品用逗号分隔~")
            return
        dishes = [d.strip() for d in re.split(r"[,，]", args) if d.strip()]
        result = menu.add(dishes, max_items=max_items)

        # 如果操作的是默认菜单且启用了同步，同步至所有其他菜单
        if result["added"] and self._sync_default_enabled() and menu is self.menu:
            self._sync_default_to_all_menus()

        lines = []
        if result["added"]:
            lines.append(
                f"✅ 成功添加 {len(result['added'])} 个菜品：{', '.join(result['added'])}"
            )
            logger.info("[what_to_eat] 添加菜品: %s", result["added"])
        if result["skipped"]:
            lines.append(f"⏭️ 已跳过重复菜品：{', '.join(result['skipped'])}")
        if result["rejected"]:
            lines.append(
                f"⚠️ 菜单已达上限（{max_items} 道），以下菜品未添加：{', '.join(result['rejected'])}"
            )
        if not lines:
            lines.append("⚠️ 没有可添加的菜品喵~")
        yield self._make_reply_result(event, "\n".join(lines))

    async def _handle_remove(self, event: AstrMessageEvent, menu: MenuManager, dish: str):
        if not dish.strip():
            yield self._make_reply_result(event, "请指定要删除的菜品名称喵~")
            return
        dish = dish.strip()
        if menu.remove(dish):
            logger.info("[what_to_eat] 删除菜品: %s", dish)
            yield self._make_reply_result(event, f"✅ 已删除菜品「{dish}」喵~")
        else:
            logger.warning("[what_to_eat] 尝试删除不存在的菜品: %s", dish)
            yield self._make_reply_result(event, f"❌ 菜单中没有「{dish}」这道菜喵~")

    async def _handle_clear(self, event: AstrMessageEvent, menu: MenuManager):
        menu.clear()
        logger.info("[what_to_eat] 菜单已清空")
        yield event.plain_result("🗑️ 菜单已清空喵~")

    async def _handle_random(self, event: AstrMessageEvent, menu: MenuManager):
        dish = menu.random_one()
        if not dish:
            yield event.plain_result("📭 菜单是空的喵~ 先添加一些菜品吧！")
            return
        template = self.config.get("random_reply_template", "今天吃{dish}喵~")
        logger.info("[what_to_eat] 随机推荐: %s", dish)
        yield event.plain_result(template.format(dish=dish))

    async def _handle_feast(self, event: AstrMessageEvent, menu: MenuManager):
        allowed, msg = self._check_banquet_allowed(event)
        if not allowed:
            yield event.plain_result(msg)
            return

        feast_count = self._feast_count()
        cnt = menu.count()
        if cnt < feast_count:
            yield event.plain_result(
                f"📭 菜单里只有 {cnt} 道菜，不够开席（需要 {feast_count} 道）喵~ 多添加一些吧！"
            )
            return

        dishes = menu.random_n(feast_count)
        self._record_banquet(event)
        logger.info("[what_to_eat] 开席推荐: %s", dishes)

        template = self.config.get("feast_reply_template", "🍽️ 今日宴席菜单：{dishes}")
        dishes_text = "｜".join(f"{i + 1}. {d}" for i, d in enumerate(dishes))
        yield event.plain_result(template.format(dishes=dishes_text))

    # ----------------- 固定指令处理器 -----------------

    @filter.command("菜单统计")
    async def cmd_stats(self, event: AstrMessageEvent):
        menu = self._menu_store.get_for_event(event)
        cnt = menu.count()
        logger.info("[what_to_eat] 菜单统计查询，当前数量: %d", cnt)
        if cnt == 0:
            yield event.plain_result(
                "📭 当前菜单是空的喵~ 先用「添加菜单」加一些菜品吧！"
            )
        else:
            dishes_str = "、".join(menu.get_all())
            yield event.plain_result(f"📋 当前菜单共有 {cnt} 道菜品：{dishes_str}")

    @filter.command("导出菜单")
    async def cmd_export(self, event: AstrMessageEvent):
        menu = self._menu_store.get_for_event(event)
        cnt = menu.count()
        if cnt == 0:
            yield event.plain_result("📭 当前菜单是空的，没什么可导出的喵~")
            return
        json_text = menu.export_json()
        logger.info("[what_to_eat] 导出菜单，共 %d 道菜品", cnt)
        yield event.plain_result(f"📤 当前共 {cnt} 道菜品，请复制下面的内容保存：")
        yield event.plain_result(f"```json\n{json_text}\n```")

    @filter.command("导入菜单")
    async def cmd_import(self, event: AstrMessageEvent, args: str = ""):
        menu = self._menu_store.get_for_event(event)
        if not args.strip():
            yield self._make_reply_result(
                event,
                "请粘贴要导入的菜品数据喵~\n支持 JSON 数组或逗号分隔的文本。",
            )
            return
        result = menu.import_data(args.strip(), max_items=self._max_items())
        lines = []
        if result["added"]:
            lines.append(
                f"✅ 成功导入 {len(result['added'])} 个菜品：{', '.join(result['added'])}"
            )
            logger.info("[what_to_eat] 导入菜品: %s", result["added"])
        if result["skipped"]:
            lines.append(f"⏭️ 已跳过重复菜品：{', '.join(result['skipped'])}")
        if result["rejected"]:
            lines.append(
                f"⚠️ 菜单已达上限（{self._max_items()} 道），以下菜品未导入：{', '.join(result['rejected'])}"
            )
        if not result["added"] and not result["skipped"] and not result["rejected"]:
            lines.append("⚠️ 没有解析到任何有效菜品喵~")
            logger.warning("[what_to_eat] 导入未解析到有效菜品")
        yield self._make_reply_result(event, "\n".join(lines))

    async def terminate(self):
        logger.info("[what_to_eat] 插件已卸载")
