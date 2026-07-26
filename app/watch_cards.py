"""Interactive Feishu cards for managing the global model watch list."""

from __future__ import annotations

import logging
import math
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from app.models import OfoxModel
from app.replies import BotReply
from app.reports import price_per_million, sort_key_model_prices
from app.repository import ModelRepository


logger = logging.getLogger(__name__)
DEFAULT_PAGE_SIZE = 8
DEFAULT_CONTEXT_TTL_SECONDS = 30 * 60
QUICK_CONTEXT_TTL_SECONDS = 24 * 60 * 60
ALL_PROVIDERS = "__all__"
ACTION_HOME_PAGE = "home_page"
ACTION_OPEN_ADD = "open_add"
ACTION_FILTER = "filter"
ACTION_ADD_PAGE = "add_page"
ACTION_WATCH = "watch"
ACTION_UNWATCH = "unwatch"
ACTION_CLEAR = "clear"
ACTION_QUICK_PAGE = "quick_page"
ACTION_FIELDS = {
    "action",
    "context_id",
    "model_name",
    "provider",
    "query",
    "page",
}
SUPPORTED_ACTIONS = {
    ACTION_HOME_PAGE,
    ACTION_OPEN_ADD,
    ACTION_FILTER,
    ACTION_ADD_PAGE,
    ACTION_WATCH,
    ACTION_UNWATCH,
    ACTION_CLEAR,
    ACTION_QUICK_PAGE,
}


class ModelCatalogSource(Protocol):
    """Source capable of fetching the current normalized model catalog."""

    def fetch_models(self) -> list[OfoxModel]:
        """Fetches the current catalog."""

        ...


@dataclass(frozen=True, slots=True)
class CardActionResult:
    """Raw replacement card and optional toast for a Feishu card callback."""

    card: dict[str, Any]
    toast_type: str | None = None
    toast: str | None = None


@dataclass(slots=True)
class _CardContext:
    """Short-lived in-memory state for one interactive card."""

    context_id: str
    kind: Literal["management", "quick"]
    catalog: tuple[OfoxModel, ...]
    catalog_available: bool
    expires_at: float
    view: Literal["home", "add", "quick"]
    provider: str = ""
    query: str = ""
    page: int = 1


@dataclass(frozen=True, slots=True)
class _ActionValue:
    """Validated action fields supplied by a card component."""

    action: str
    context_id: str
    model_name: str
    provider: str
    query: str
    page: int


class WatchCardService:
    """Builds watch cards and handles their callbacks without calling Ofox."""

    def __init__(
        self,
        source: ModelCatalogSource,
        repository: ModelRepository,
        *,
        page_size: int = DEFAULT_PAGE_SIZE,
        context_ttl_seconds: int = DEFAULT_CONTEXT_TTL_SECONDS,
        quick_context_ttl_seconds: int = QUICK_CONTEXT_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        context_id_factory: Callable[[], str] | None = None,
    ) -> None:
        """Initializes the card service and its thread-safe context cache."""

        if page_size < 1:
            raise ValueError("page_size must be positive")
        if context_ttl_seconds < 1 or quick_context_ttl_seconds < 1:
            raise ValueError("context TTLs must be positive")

        self.source = source
        self.repository = repository
        self.page_size = page_size
        self.context_ttl_seconds = context_ttl_seconds
        self.quick_context_ttl_seconds = quick_context_ttl_seconds
        self._clock = clock
        self._context_id_factory = context_id_factory or (lambda: uuid.uuid4().hex)
        self._contexts: dict[str, _CardContext] = {}
        self._catalog: tuple[OfoxModel, ...] = ()
        self._lock = threading.Lock()
        self._action_lock = threading.Lock()

    def open_management_card(self) -> BotReply:
        """Refreshes the catalog and opens the global watch management card."""

        try:
            catalog = tuple(self.source.fetch_models())
            catalog_available = True
        except Exception:
            # The existing watch list remains usable when Ofox is unavailable.
            logger.exception("Refresh watch-card catalog failed")
            catalog = ()
            catalog_available = False

        context = self._create_context(
            "management",
            catalog,
            catalog_available=catalog_available,
            ttl_seconds=self.context_ttl_seconds,
        )
        return BotReply.interactive(self._build_management_home(context))

    def build_new_models_card(self, models: list[OfoxModel]) -> BotReply:
        """Builds the 24-hour quick-action card appended to a new-model report."""

        context = self._create_context(
            "quick",
            tuple(models),
            catalog_available=True,
            ttl_seconds=self.quick_context_ttl_seconds,
        )
        return BotReply.interactive(self._build_quick_card(context))

    def handle_action(
        self,
        value: Any,
        *,
        form_value: Any = None,
    ) -> CardActionResult:
        """Validates and executes one card action using only memory and SQLite."""

        parsed, error = self._parse_action_value(value, form_value)
        if parsed is None:
            return self._invalid_result(error or "操作参数无效。")

        with self._action_lock:
            return self._handle_parsed_action(parsed)

    def _handle_parsed_action(self, parsed: _ActionValue) -> CardActionResult:
        """Executes one validated action while card context state is serialized."""

        context = self._get_context(parsed.context_id)
        if context is None:
            return self._handle_expired_context(parsed)

        result = self._validate_context_action(context, parsed)
        if result is not None:
            return result

        if parsed.action == ACTION_HOME_PAGE:
            context.view = "home"
            context.provider = ""
            context.query = ""
            context.page = parsed.page
            return self._result(self._build_management_home(context))
        if parsed.action in {ACTION_OPEN_ADD, ACTION_FILTER, ACTION_ADD_PAGE}:
            context.view = "add"
            context.provider = parsed.provider
            context.query = parsed.query
            context.page = parsed.page
            card = self._build_add_card(context)
            if parsed.action == ACTION_ADD_PAGE:
                return self._result(card)
            return self._result(
                card,
                "info",
                "已更新筛选结果。",
            )
        if parsed.action == ACTION_QUICK_PAGE:
            context.page = parsed.page
            return self._result(self._build_quick_card(context))
        if parsed.action == ACTION_WATCH:
            inserted = self.repository.add_watched_model(parsed.model_name)
            return self._result(
                self._build_current_card(context),
                "success" if inserted else "info",
                (
                    f"已关注：{parsed.model_name}"
                    if inserted
                    else f"已在关注列表中：{parsed.model_name}"
                ),
            )
        if parsed.action == ACTION_UNWATCH:
            removed = self.repository.remove_watched_model(parsed.model_name)
            return self._result(
                self._build_current_card(context),
                "success" if removed else "warning",
                (
                    f"已取消关注：{parsed.model_name}"
                    if removed
                    else f"状态已变化，当前未关注：{parsed.model_name}"
                ),
            )

        removed_count = self.repository.clear_watched_models()
        context.page = 1
        return self._result(
            self._build_management_home(context),
            "success" if removed_count else "info",
            (
                f"已清空全部关注，共移除 {removed_count} 个模型。"
                if removed_count
                else "关注列表已经为空。"
            ),
        )

    def render_duplicate_action(
        self,
        value: Any,
        *,
        form_value: Any = None,
    ) -> CardActionResult:
        """Renders current state for a duplicate event without changing SQLite."""

        parsed, error = self._parse_action_value(value, form_value)
        if parsed is None:
            return self._invalid_result(error or "重复事件参数无效。")
        with self._action_lock:
            context = self._get_context(parsed.context_id)
            if context is None:
                return self._invalid_result(
                    "卡片上下文已过期，请从机器人菜单重新打开。"
                )
            return self._result(
                self._build_current_card(context),
                "info",
                "该操作已处理，请勿重复点击。",
            )

    def _create_context(
        self,
        kind: Literal["management", "quick"],
        catalog: tuple[OfoxModel, ...],
        *,
        catalog_available: bool,
        ttl_seconds: int,
    ) -> _CardContext:
        """Stores a new context and refreshes the shared in-memory catalog."""

        prefix = "manage" if kind == "management" else "quick"
        context = _CardContext(
            context_id=f"{prefix}:{self._context_id_factory()}",
            kind=kind,
            catalog=catalog,
            catalog_available=catalog_available,
            expires_at=self._clock() + ttl_seconds,
            view="home" if kind == "management" else "quick",
        )
        with self._lock:
            self._remove_expired_locked()
            self._contexts[context.context_id] = context
            if catalog_available:
                self._catalog = catalog
        return context

    def _get_context(self, context_id: str) -> _CardContext | None:
        """Returns one unexpired context from the thread-safe cache."""

        with self._lock:
            self._remove_expired_locked()
            return self._contexts.get(context_id)

    def _remove_expired_locked(self) -> None:
        """Removes expired contexts while the cache lock is held."""

        now = self._clock()
        expired = [
            context_id
            for context_id, context in self._contexts.items()
            if context.expires_at <= now
        ]
        for context_id in expired:
            del self._contexts[context_id]

    def _parse_action_value(
        self,
        value: Any,
        form_value: Any,
    ) -> tuple[_ActionValue | None, str | None]:
        """Validates the fixed action payload and optional form overrides."""

        if not isinstance(value, dict) or set(value) != ACTION_FIELDS:
            return None, "操作参数缺失或包含未知字段。"

        action = value.get("action")
        context_id = value.get("context_id")
        model_name = value.get("model_name")
        provider = value.get("provider")
        query = value.get("query")
        page = value.get("page")
        if (
            not isinstance(action, str)
            or action not in SUPPORTED_ACTIONS
            or not isinstance(context_id, str)
            or not context_id
            or len(context_id) > 128
            or not isinstance(model_name, str)
            or len(model_name) > 500
            or not isinstance(provider, str)
            or len(provider) > 200
            or not isinstance(query, str)
            or len(query) > 200
            or isinstance(page, bool)
            or not isinstance(page, int)
            or page < 1
        ):
            return None, "操作参数格式无效。"

        if action == ACTION_FILTER:
            if not isinstance(form_value, dict):
                return None, "未收到筛选表单内容。"
            form_provider = form_value.get("provider", provider)
            form_query = form_value.get("query", query)
            if not isinstance(form_provider, str) or not isinstance(form_query, str):
                return None, "筛选条件格式无效。"
            provider = "" if form_provider == ALL_PROVIDERS else form_provider.strip()
            query = form_query.strip()
            if len(provider) > 200 or len(query) > 200:
                return None, "筛选条件过长。"
            page = 1
        else:
            provider = provider.strip()
            query = query.strip()

        model_name = model_name.strip()
        if action in {ACTION_WATCH, ACTION_UNWATCH} and not model_name:
            return None, "缺少模型名称，未修改关注列表。"
        if action not in {ACTION_WATCH, ACTION_UNWATCH} and model_name:
            return None, "当前操作不接受模型名称。"

        return (
            _ActionValue(
                action=action,
                context_id=context_id,
                model_name=model_name,
                provider=provider,
                query=query,
                page=page,
            ),
            None,
        )

    def _validate_context_action(
        self,
        context: _CardContext,
        action: _ActionValue,
    ) -> CardActionResult | None:
        """Validates context kind, filters, page boundaries, and model names."""

        if context.kind == "quick":
            if action.action not in {
                ACTION_WATCH,
                ACTION_UNWATCH,
                ACTION_QUICK_PAGE,
            }:
                return self._invalid_result("该操作不适用于新增模型快捷卡片。")
            total = len(self._unique_models(context.catalog))
            if not self._is_valid_page(action.page, total):
                return self._invalid_result("页码超出范围，未修改关注列表。")
            if action.action in {
                ACTION_WATCH,
                ACTION_UNWATCH,
            } and action.model_name not in {model.name for model in context.catalog}:
                return self._invalid_result("模型不属于当前新增模型卡片。")
            return None

        if action.action == ACTION_QUICK_PAGE:
            return self._invalid_result("该操作不适用于关注管理卡片。")
        if action.action == ACTION_CLEAR:
            if context.view != "home":
                return self._invalid_result("请返回关注管理首页后再清空。")
            return None
        if action.action == ACTION_HOME_PAGE:
            total = len(self.repository.list_watched_models())
            if not self._is_valid_page(action.page, total):
                return self._invalid_result("页码超出范围，未修改关注列表。")
            return None
        if action.action in {ACTION_OPEN_ADD, ACTION_FILTER, ACTION_ADD_PAGE}:
            if not context.catalog_available:
                return self._result(
                    self._build_management_home(context),
                    "warning",
                    "模型目录暂不可用，请稍后从机器人菜单重新打开。",
                )
            filtered = self._filter_catalog(
                context.catalog,
                action.provider,
                action.query,
            )
            if not self._is_valid_page(action.page, len(filtered)):
                return self._invalid_result("页码超出范围，未修改关注列表。")
            return None

        if action.provider != context.provider or action.query != context.query:
            return self._invalid_result("卡片筛选状态已变化，请使用最新卡片操作。")
        if context.view == "home":
            total = len(self.repository.list_watched_models())
        elif context.view == "add":
            total = len(
                self._filter_catalog(
                    context.catalog,
                    context.provider,
                    context.query,
                )
            )
        else:
            return self._invalid_result("卡片状态无效，请从机器人菜单重新打开。")
        if not self._is_valid_page(action.page, total):
            return self._invalid_result("页码超出范围，未修改关注列表。")

        watched_names = set(self.repository.list_watched_models())
        catalog_names = {model.name for model in context.catalog}
        if action.action == ACTION_WATCH:
            if context.view != "add" or action.model_name not in catalog_names:
                return self._invalid_result("模型不属于当前添加结果。")
        elif action.model_name not in watched_names and context.view == "home":
            # A concurrent removal remains a valid idempotent operation.
            return None
        elif context.view == "add" and action.model_name not in catalog_names:
            return self._invalid_result("模型不属于当前添加结果。")
        return None

    def _handle_expired_context(self, action: _ActionValue) -> CardActionResult:
        """Keeps explicit quick-card model actions idempotent after expiry."""

        if action.context_id.startswith("quick:") and action.action in {
            ACTION_WATCH,
            ACTION_UNWATCH,
        }:
            if action.action == ACTION_WATCH:
                self.repository.add_watched_model(action.model_name)
            else:
                self.repository.remove_watched_model(action.model_name)
            return self._result(
                self._expired_card(),
                "warning",
                "操作已按模型名称处理；卡片上下文已过期，请从机器人菜单重新打开。",
            )
        return self._invalid_result(
            "卡片上下文已过期，请从机器人菜单重新打开，未修改关注列表。"
        )

    def _build_current_card(self, context: _CardContext) -> dict[str, Any]:
        """Renders the active view and clamps pages after a state mutation."""

        if context.kind == "quick":
            context.page = self._clamp_page(
                context.page,
                len(self._unique_models(context.catalog)),
            )
            return self._build_quick_card(context)
        if context.view == "add":
            context.page = self._clamp_page(
                context.page,
                len(
                    self._filter_catalog(
                        context.catalog,
                        context.provider,
                        context.query,
                    )
                ),
            )
            return self._build_add_card(context)

        context.page = self._clamp_page(
            context.page,
            len(self.repository.list_watched_models()),
        )
        return self._build_management_home(context)

    def _build_management_home(self, context: _CardContext) -> dict[str, Any]:
        """Builds the paginated management home card."""

        watched_names = self.repository.list_watched_models()
        context.page = self._clamp_page(context.page, len(watched_names))
        start = (context.page - 1) * self.page_size
        shown_names = watched_names[start : start + self.page_size]
        catalog_by_name = {model.name: model for model in context.catalog}
        if context.catalog_available:
            catalog_text = f"当前目录：{len(context.catalog)} 个模型"
        else:
            catalog_text = "当前目录暂不可用"
        elements: list[dict[str, Any]] = [
            _markdown(f"**全局关注：{len(watched_names)} 个** · {catalog_text}")
        ]
        if not context.catalog_available:
            elements.append(
                _markdown(
                    "模型目录暂不可用。仍可取消或清空已有关注；添加功能已禁用，"
                    "请稍后从机器人菜单重新打开。"
                )
            )
        if not shown_names:
            elements.append(_markdown("暂无关注模型。点击“添加模型”开始关注。"))
        else:
            elements.extend(
                self._model_row(
                    model_name,
                    catalog_by_name.get(model_name),
                    watched=True,
                    context=context,
                )
                for model_name in shown_names
            )

        elements.extend(
            self._pagination(
                context,
                action=ACTION_HOME_PAGE,
                page=context.page,
                total=len(watched_names),
            )
        )
        elements.append(
            _button_row(
                [
                    _button(
                        "添加模型",
                        self._action_value(ACTION_OPEN_ADD, context, page=1),
                        type_="primary_filled",
                        disabled=not context.catalog_available,
                        disabled_tips="模型目录暂不可用，请稍后重新打开关注管理。",
                    ),
                    _button(
                        "清空全部",
                        self._action_value(ACTION_CLEAR, context, page=context.page),
                        type_="danger",
                        disabled=not watched_names,
                        confirm={
                            "title": _plain_text("确认清空全部关注？"),
                            "text": _plain_text(
                                "此操作会移除全局共享关注列表中的所有模型。"
                            ),
                        },
                    ),
                ]
            )
        )
        return _card("关注管理", elements)

    def _build_add_card(self, context: _CardContext) -> dict[str, Any]:
        """Builds provider/keyword filters and paginated catalog results."""

        filtered = self._filter_catalog(
            context.catalog,
            context.provider,
            context.query,
        )
        context.page = self._clamp_page(context.page, len(filtered))
        start = (context.page - 1) * self.page_size
        shown_models = filtered[start : start + self.page_size]
        watched_names = set(self.repository.list_watched_models())
        providers = sorted(
            {model.provider for model in context.catalog},
            key=lambda value: (value.casefold(), value),
        )
        provider_value = context.provider or ALL_PROVIDERS
        form = {
            "tag": "form",
            "name": "watch_filters",
            "elements": [
                {
                    "tag": "select_static",
                    "name": "provider",
                    "placeholder": _plain_text("筛选提供商"),
                    "options": [
                        {"text": _plain_text("全部提供商"), "value": ALL_PROVIDERS},
                        *[
                            {"text": _plain_text(provider), "value": provider}
                            for provider in providers
                        ],
                    ],
                    "initial_option": provider_value,
                    "width": "fill",
                },
                {
                    "tag": "input",
                    "name": "query",
                    "required": False,
                    "placeholder": _plain_text("搜索模型名称、ID 或提供商"),
                    "default_value": context.query,
                    "width": "fill",
                    "max_length": 200,
                    "fallback": {
                        "tag": "fallback_text",
                        "text": _plain_text(
                            "当前客户端版本不支持关键词输入，请升级飞书客户端。"
                        ),
                    },
                },
                _button(
                    "筛选",
                    self._action_value(
                        ACTION_FILTER,
                        context,
                        provider=context.provider,
                        query=context.query,
                        page=1,
                    ),
                    type_="primary_filled",
                    name="submit_filters",
                    form_action_type="submit",
                ),
            ],
        }
        elements: list[dict[str, Any]] = [
            _markdown(
                f"找到 **{len(filtered)}** 个模型。关键词匹配名称、ID 和提供商，"
                "忽略大小写。"
            ),
            form,
        ]
        if not shown_models:
            elements.append(_markdown("没有符合当前筛选条件的模型。"))
        else:
            elements.extend(
                self._model_row(
                    model.name,
                    model,
                    watched=model.name in watched_names,
                    context=context,
                )
                for model in shown_models
            )
        elements.extend(
            self._pagination(
                context,
                action=ACTION_ADD_PAGE,
                page=context.page,
                total=len(filtered),
                provider=context.provider,
                query=context.query,
            )
        )
        elements.append(
            _button_row(
                [
                    _button(
                        "返回关注管理",
                        self._action_value(ACTION_HOME_PAGE, context, page=1),
                    )
                ]
            )
        )
        return _card("添加关注模型", elements)

    def _build_quick_card(self, context: _CardContext) -> dict[str, Any]:
        """Builds the paginated per-model actions for newly discovered models."""

        models = self._unique_models(context.catalog)
        context.page = self._clamp_page(context.page, len(models))
        start = (context.page - 1) * self.page_size
        shown_models = models[start : start + self.page_size]
        watched_names = set(self.repository.list_watched_models())
        elements: list[dict[str, Any]] = [
            _markdown(f"本次发现 **{len(models)}** 个新增模型。请逐项选择是否关注。")
        ]
        elements.extend(
            self._model_row(
                model.name,
                model,
                watched=model.name in watched_names,
                context=context,
            )
            for model in shown_models
        )
        elements.extend(
            self._pagination(
                context,
                action=ACTION_QUICK_PAGE,
                page=context.page,
                total=len(models),
            )
        )
        return _card("新增模型快捷关注", elements, template="green")

    def _model_row(
        self,
        model_name: str,
        model: OfoxModel | None,
        *,
        watched: bool,
        context: _CardContext,
    ) -> dict[str, Any]:
        """Builds one model description with a state-aware action button."""

        if model is None:
            description = f"**{_escape_markdown(model_name)}**\n目录中暂缺"
        else:
            display_name = model.name or model.id
            id_line = (
                f"\n`{_escape_markdown(model.id)}`" if model.id != display_name else ""
            )
            description = (
                f"**{_escape_markdown(display_name)}**{id_line}\n"
                f"{_escape_markdown(model.provider)} · "
                f"输入 {price_per_million(model.input_price)} · "
                f"输出 {price_per_million(model.output_price)}"
            )
        action = ACTION_UNWATCH if watched else ACTION_WATCH
        return {
            "tag": "column_set",
            "flex_mode": "none",
            "horizontal_spacing": "8px",
            "columns": [
                {
                    "tag": "column",
                    "width": "weighted",
                    "weight": 1,
                    "elements": [_markdown(description)],
                },
                {
                    "tag": "column",
                    "width": "auto",
                    "vertical_align": "center",
                    "elements": [
                        _button(
                            "取消关注" if watched else "关注",
                            self._action_value(
                                action,
                                context,
                                model_name=model_name,
                                provider=context.provider,
                                query=context.query,
                                page=context.page,
                            ),
                            type_="danger" if watched else "primary",
                        )
                    ],
                },
            ],
        }

    def _pagination(
        self,
        context: _CardContext,
        *,
        action: str,
        page: int,
        total: int,
        provider: str = "",
        query: str = "",
    ) -> list[dict[str, Any]]:
        """Builds page status plus enabled previous/next buttons."""

        page_count = max(1, math.ceil(total / self.page_size))
        return [
            _markdown(f"第 **{page}/{page_count}** 页 · 每页 {self.page_size} 条"),
            _button_row(
                [
                    _button(
                        "上一页",
                        self._action_value(
                            action,
                            context,
                            provider=provider,
                            query=query,
                            page=max(1, page - 1),
                        ),
                        disabled=page <= 1,
                    ),
                    _button(
                        "下一页",
                        self._action_value(
                            action,
                            context,
                            provider=provider,
                            query=query,
                            page=min(page_count, page + 1),
                        ),
                        disabled=page >= page_count,
                    ),
                ]
            ),
        ]

    @staticmethod
    def _filter_catalog(
        catalog: tuple[OfoxModel, ...],
        provider: str,
        query: str,
    ) -> list[OfoxModel]:
        """Filters case-insensitively and applies the report price ordering."""

        provider_key = provider.casefold()
        query_key = query.casefold()
        matched = [
            model
            for model in catalog
            if (not provider_key or model.provider.casefold() == provider_key)
            and (
                not query_key
                or query_key in model.name.casefold()
                or query_key in model.id.casefold()
                or query_key in model.provider.casefold()
            )
        ]
        matched.sort(key=sort_key_model_prices)
        return WatchCardService._unique_models(matched)

    @staticmethod
    def _unique_models(
        models: tuple[OfoxModel, ...] | list[OfoxModel],
    ) -> list[OfoxModel]:
        """Deduplicates repository-keyed model names after price sorting."""

        ordered = sorted(models, key=sort_key_model_prices)
        unique: dict[str, OfoxModel] = {}
        for model in ordered:
            unique.setdefault(model.name, model)
        return list(unique.values())

    def _action_value(
        self,
        action: str,
        context: _CardContext,
        *,
        model_name: str = "",
        provider: str = "",
        query: str = "",
        page: int,
    ) -> dict[str, Any]:
        """Builds the fixed, validated callback value shape."""

        return {
            "action": action,
            "context_id": context.context_id,
            "model_name": model_name,
            "provider": provider,
            "query": query,
            "page": page,
        }

    def _is_valid_page(self, page: int, total: int) -> bool:
        """Checks a one-based page against the requested result set."""

        return page <= max(1, math.ceil(total / self.page_size))

    def _clamp_page(self, page: int, total: int) -> int:
        """Clamps a page after a concurrent watch-list change."""

        return min(max(1, page), max(1, math.ceil(total / self.page_size)))

    @staticmethod
    def _result(
        card: dict[str, Any],
        toast_type: str | None = None,
        toast: str | None = None,
    ) -> CardActionResult:
        """Creates a callback result with an optional toast."""

        return CardActionResult(card=card, toast_type=toast_type, toast=toast)

    def _invalid_result(self, message: str) -> CardActionResult:
        """Returns an error card without changing state."""

        return self._result(self._expired_card(), "error", message)

    @staticmethod
    def _expired_card() -> dict[str, Any]:
        """Builds a safe replacement when action context cannot be used."""

        return _card(
            "关注管理",
            [_markdown("此卡片无法继续刷新。请从机器人菜单重新打开“关注管理”后操作。")],
            template="orange",
        )


def _card(
    title: str,
    elements: list[dict[str, Any]],
    *,
    template: str = "blue",
) -> dict[str, Any]:
    """Wraps elements in a Feishu Card JSON 2.0 document."""

    return {
        "schema": "2.0",
        "config": {
            "update_multi": True,
            "enable_forward_interaction": True,
            "width_mode": "fill",
        },
        "header": {
            "title": _plain_text(title),
            "template": template,
        },
        "body": {
            "direction": "vertical",
            "vertical_spacing": "8px",
            "elements": elements,
        },
    }


def _plain_text(content: str) -> dict[str, str]:
    """Builds a Feishu plain-text object."""

    return {"tag": "plain_text", "content": content}


def _markdown(content: str) -> dict[str, str]:
    """Builds a Feishu Card JSON 2.0 markdown component."""

    return {"tag": "markdown", "content": content}


def _button(
    text: str,
    value: dict[str, Any],
    *,
    type_: str = "default",
    disabled: bool = False,
    disabled_tips: str = "",
    confirm: dict[str, Any] | None = None,
    name: str | None = None,
    form_action_type: str | None = None,
) -> dict[str, Any]:
    """Builds a callback button using the Card JSON 2.0 behavior shape."""

    button: dict[str, Any] = {
        "tag": "button",
        "text": _plain_text(text),
        "type": type_,
        "size": "medium",
        "disabled": disabled,
        "behaviors": [{"type": "callback", "value": value}],
    }
    if disabled_tips:
        button["disabled_tips"] = _plain_text(disabled_tips)
    if confirm is not None:
        button["confirm"] = confirm
    if name is not None:
        button["name"] = name
    if form_action_type is not None:
        button["form_action_type"] = form_action_type
    return button


def _button_row(buttons: list[dict[str, Any]]) -> dict[str, Any]:
    """Builds a compact row of buttons."""

    return {
        "tag": "column_set",
        "flex_mode": "none",
        "horizontal_spacing": "8px",
        "columns": [
            {
                "tag": "column",
                "width": "auto",
                "elements": [button],
            }
            for button in buttons
        ],
    }


def _escape_markdown(value: str) -> str:
    """Escapes model metadata used in card markdown."""

    escaped = value
    for character in ("\\", "`", "*", "_", "~", "[", "]"):
        escaped = escaped.replace(character, f"\\{character}")
    return escaped
