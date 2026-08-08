import json
from pathlib import Path
from typing import Any

from app.handlers import (
    EventDeduplicator,
    handle_card_action_payload,
)
from app.repository import ModelRepository
from app.watch_cards import (
    ACTION_ADD_PAGE,
    ACTION_CLEAR,
    ACTION_CLOSE,
    ACTION_FILTER,
    ACTION_QUICK_PAGE,
    ACTION_UNWATCH,
    ACTION_WATCH,
    WatchCardService,
)

from tests.helpers import model


class FakeCatalogSource:
    def __init__(self, models, *, error: Exception | None = None) -> None:
        self.models = list(models)
        self.error = error
        self.calls = 0

    def fetch_models(self):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return list(self.models)


class MutableClock:
    def __init__(self, now: float = 100.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def make_service(
    tmp_path: Path,
    models,
    *,
    source_error: Exception | None = None,
    clock=None,
    page_size: int = 8,
):
    source = FakeCatalogSource(models, error=source_error)
    repository = ModelRepository(tmp_path / "watch-cards.sqlite3")
    ids = iter(["context-1", "context-2", "context-3"])
    cards = WatchCardService(
        source,
        repository,
        page_size=page_size,
        clock=clock or MutableClock(),
        context_id_factory=lambda: next(ids),
    )
    return cards, repository, source


def walk_json(value: Any):
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_json(child)


def buttons(card: dict[str, Any], text: str | None = None) -> list[dict[str, Any]]:
    found = [
        item
        for item in walk_json(card)
        if isinstance(item, dict) and item.get("tag") == "button"
    ]
    if text is None:
        return found
    return [button for button in found if button.get("text", {}).get("content") == text]


def callback_value(button: dict[str, Any]) -> dict[str, Any]:
    return button["behaviors"][0]["value"]


def markdown_contents(card: dict[str, Any]) -> list[str]:
    return [
        item["content"]
        for item in walk_json(card)
        if isinstance(item, dict) and item.get("tag") == "markdown"
    ]


def assert_forwarding_disabled(card: dict[str, Any]) -> None:
    assert card["config"]["update_multi"] is True
    assert card["config"]["enable_forward"] is False
    assert card["config"]["enable_forward_interaction"] is False


def test_management_home_empty_missing_pagination_and_clear_confirm(
    tmp_path: Path,
) -> None:
    models = [model(f"openai/model-{index}") for index in range(9)]
    cards, repository, source = make_service(tmp_path, models)
    for item in models:
        repository.add_watched_model(item.name)
    repository.add_watched_model("retired/missing")

    reply = cards.open_management_card()
    card = reply.content

    assert source.calls == 1
    assert reply.msg_type == "interactive"
    assert card["schema"] == "2.0"
    assert "全局关注：10 个" in "\n".join(markdown_contents(card))
    assert len(buttons(card, "取消关注")) == 8
    assert buttons(card, "下一页")[0]["disabled"] is False
    clear = buttons(card, "清空全部")[0]
    assert callback_value(clear)["action"] == ACTION_CLEAR
    assert clear["confirm"]["title"]["content"] == "确认清空全部关注？"
    assert len(buttons(card, "关闭卡片")) == 1
    assert callback_value(buttons(card, "关闭卡片")[0])["action"] == ACTION_CLOSE
    assert "confirm" not in buttons(card, "关闭卡片")[0]
    assert_forwarding_disabled(card)

    next_value = callback_value(buttons(card, "下一页")[0])
    next_page = cards.handle_action(next_value)
    assert "retired/missing" in json.dumps(next_page.card, ensure_ascii=False)
    assert next_page.toast is None

    response = handle_card_action_payload(
        {"event": {"action": {"value": next_value}}},
        cards,
    )
    assert response.toast is None


def test_add_page_combines_provider_keyword_sorting_and_pagination(
    tmp_path: Path,
) -> None:
    models = [
        model(
            "openai/gpt-expensive",
            output_price="0.000009",
            input_price="0.000001",
        ),
        model(
            "openai/gpt-cheap",
            output_price="0.000001",
            input_price="0.000009",
        ),
        model("openai/o1"),
        model("anthropic/gpt-named"),
        *[model(f"openai/gpt-page-{index}") for index in range(8)],
    ]
    cards, _, _ = make_service(tmp_path, models)
    home = cards.open_management_card().content

    add = cards.handle_action(callback_value(buttons(home, "添加模型")[0]))
    form = next(
        item
        for item in walk_json(add.card)
        if isinstance(item, dict) and item.get("tag") == "form"
    )
    query_input = next(
        item
        for item in walk_json(form)
        if isinstance(item, dict) and item.get("tag") == "input"
    )
    assert "升级飞书客户端" in query_input["fallback"]["text"]["content"]

    filter_button = buttons(add.card, "筛选")[0]
    assert len(buttons(add.card, "关闭卡片")) == 1
    assert callback_value(buttons(add.card, "关闭卡片")[0])["action"] == ACTION_CLOSE
    assert_forwarding_disabled(add.card)
    filtered = cards.handle_action(
        callback_value(filter_button),
        form_value={"provider": "openai", "query": "GPT"},
    )
    text = "\n".join(markdown_contents(filtered.card))

    assert "anthropic/gpt-named" not in text
    assert "openai/o1" not in text
    assert "openai/gpt-cheap" in text
    assert callback_value(buttons(filtered.card, "下一页")[0])["action"] == (
        ACTION_ADD_PAGE
    )
    assert buttons(filtered.card, "下一页")[0]["disabled"] is False

    second_page = cards.handle_action(
        callback_value(buttons(filtered.card, "下一页")[0])
    )
    second_text = "\n".join(markdown_contents(second_page.card))
    assert "openai/gpt-expensive" in second_text
    assert "openai/gpt-cheap" not in second_text
    assert second_page.toast is None


def test_watch_unwatch_duplicate_concurrent_change_and_invalid_actions(
    tmp_path: Path,
) -> None:
    cards, repository, source = make_service(
        tmp_path,
        [model("openai/gpt-4.1")],
    )
    home = cards.open_management_card().content
    assert "暂无关注模型" in json.dumps(home, ensure_ascii=False)
    add = cards.handle_action(callback_value(buttons(home, "添加模型")[0]))
    watch_value = callback_value(buttons(add.card, "关注")[0])

    invalid = dict(watch_value, action="unknown")
    invalid_result = cards.handle_action(invalid)
    assert invalid_result.toast_type == "error"
    assert repository.list_watched_models() == []

    invalid_page = dict(watch_value, page=99)
    page_result = cards.handle_action(invalid_page)
    assert page_result.toast_type == "error"
    assert repository.list_watched_models() == []

    added = cards.handle_action(watch_value)
    duplicate = cards.handle_action(watch_value)
    assert added.toast_type == "success"
    assert "已关注" in added.toast
    assert duplicate.toast_type == "info"
    assert "已在关注列表中" in duplicate.toast
    assert source.calls == 1

    unwatch_value = callback_value(buttons(duplicate.card, "取消关注")[0])
    repository.remove_watched_model("openai/gpt-4.1")
    changed = cards.handle_action(unwatch_value)
    assert changed.toast_type == "warning"
    assert "状态已变化" in changed.toast
    assert repository.list_watched_models() == []


def test_clear_and_catalog_unavailable_keep_existing_management_actions(
    tmp_path: Path,
) -> None:
    cards, repository, source = make_service(
        tmp_path,
        [],
        source_error=RuntimeError("catalog down"),
    )
    repository.add_watched_model("openai/gpt-4.1")

    home = cards.open_management_card().content

    assert source.calls == 1
    text = json.dumps(home, ensure_ascii=False)
    assert "模型目录暂不可用" in text
    assert buttons(home, "添加模型")[0]["disabled"] is True

    remove = cards.handle_action(callback_value(buttons(home, "取消关注")[0]))
    assert remove.toast_type == "success"
    assert repository.list_watched_models() == []
    assert source.calls == 1

    repository.add_watched_model("openai/gpt-4.1")
    fresh_home = cards.open_management_card().content
    clear_value = callback_value(buttons(fresh_home, "清空全部")[0])
    cleared = cards.handle_action(clear_value)
    cleared_again = cards.handle_action(clear_value)
    assert "共移除 1 个模型" in cleared.toast
    assert cleared_again.toast == "关注列表已经为空。"
    assert repository.list_watched_models() == []


def test_quick_card_has_only_per_model_actions_and_survives_context_expiry(
    tmp_path: Path,
) -> None:
    clock = MutableClock()
    models = [model(f"openai/new-{index}") for index in range(10)]
    cards, repository, source = make_service(
        tmp_path,
        [],
        clock=clock,
    )
    repository.add_watched_model(models[0].name)

    card = cards.build_new_models_card(models).content
    action_values = [callback_value(button) for button in buttons(card)]

    assert source.calls == 0
    assert len(buttons(card, "关注")) == 7
    assert len(buttons(card, "取消关注")) == 1
    assert "全部关注" not in json.dumps(card, ensure_ascii=False)
    assert not any(
        item.get("tag") == "checker"
        for item in walk_json(card)
        if isinstance(item, dict)
    )
    assert {value["action"] for value in action_values} <= {
        ACTION_WATCH,
        ACTION_UNWATCH,
        ACTION_QUICK_PAGE,
        ACTION_CLOSE,
    }
    assert len(buttons(card, "关闭卡片")) == 1
    assert_forwarding_disabled(card)

    next_page = cards.handle_action(callback_value(buttons(card, "下一页")[0]))
    assert next_page.toast is None

    watch_value = callback_value(buttons(card, "关注")[0])
    clock.now += 24 * 60 * 60 + 1
    expired = cards.handle_action(watch_value)

    assert expired.toast_type == "warning"
    assert "上下文已过期" in expired.toast
    assert watch_value["model_name"] in repository.list_watched_models()


def test_close_from_each_view_is_idempotent_and_blocks_old_actions(
    tmp_path: Path,
) -> None:
    cards, repository, _ = make_service(
        tmp_path,
        [model("openai/existing"), model("openai/new")],
    )
    repository.add_watched_model("openai/existing")

    home = cards.open_management_card().content
    clear_value = callback_value(buttons(home, "清空全部")[0])
    home_close_value = callback_value(buttons(home, "关闭卡片")[0])
    closed_home = cards.handle_action(home_close_value)
    closed_home_again = cards.handle_action(home_close_value)
    old_home_action = cards.handle_action(clear_value)

    assert closed_home.toast_type == "success"
    assert closed_home.toast == "卡片已关闭"
    assert closed_home.card["header"]["template"] == "grey"
    assert markdown_contents(closed_home.card) == [
        "关注管理卡片已关闭，可从机器人菜单重新打开。"
    ]
    assert buttons(closed_home.card) == []
    assert closed_home_again.card == closed_home.card
    assert old_home_action.card == closed_home.card
    assert repository.list_watched_models() == ["openai/existing"]

    second_home = cards.open_management_card().content
    add = cards.handle_action(callback_value(buttons(second_home, "添加模型")[0]))
    watch_value = callback_value(buttons(add.card, "关注")[0])
    add_close_value = callback_value(buttons(add.card, "关闭卡片")[0])
    closed_add = cards.handle_action(add_close_value)
    duplicate_old_add_action = cards.render_duplicate_action(watch_value)

    assert markdown_contents(closed_add.card) == [
        "关注管理卡片已关闭，可从机器人菜单重新打开。"
    ]
    assert buttons(closed_add.card) == []
    assert duplicate_old_add_action.card == closed_add.card
    assert repository.list_watched_models() == ["openai/existing"]

    quick = cards.build_new_models_card([model("openai/new")]).content
    quick_watch_value = callback_value(buttons(quick, "关注")[0])
    quick_close_value = callback_value(buttons(quick, "关闭卡片")[0])
    closed_quick = cards.handle_action(quick_close_value)
    old_quick_action = cards.handle_action(quick_watch_value)

    assert closed_quick.toast_type == "success"
    assert closed_quick.toast == "卡片已关闭"
    assert closed_quick.card["header"]["template"] == "grey"
    assert markdown_contents(closed_quick.card) == ["新增模型快捷关注卡片已关闭。"]
    assert buttons(closed_quick.card) == []
    assert old_quick_action.card == closed_quick.card
    assert_forwarding_disabled(closed_quick.card)
    assert repository.list_watched_models() == ["openai/existing"]


def test_expired_context_can_close_and_then_rejects_old_actions(tmp_path: Path) -> None:
    clock = MutableClock()
    cards, repository, _ = make_service(
        tmp_path,
        [model("openai/new")],
        clock=clock,
    )
    repository.add_watched_model("openai/existing")

    home = cards.open_management_card().content
    clear_value = callback_value(buttons(home, "清空全部")[0])
    home_close_value = callback_value(buttons(home, "关闭卡片")[0])
    quick = cards.build_new_models_card([model("openai/new")]).content
    watch_value = callback_value(buttons(quick, "关注")[0])
    quick_close_value = callback_value(buttons(quick, "关闭卡片")[0])

    clock.now += 24 * 60 * 60 + 1
    expired_home_close = cards.handle_action(home_close_value)
    expired_quick_close = cards.handle_action(quick_close_value)

    assert expired_home_close.toast == "卡片已关闭"
    assert markdown_contents(expired_home_close.card) == [
        "关注管理卡片已关闭，可从机器人菜单重新打开。"
    ]
    assert expired_quick_close.toast == "卡片已关闭"
    assert markdown_contents(expired_quick_close.card) == [
        "新增模型快捷关注卡片已关闭。"
    ]
    assert cards.handle_action(clear_value).card == expired_home_close.card
    assert cards.handle_action(watch_value).card == expired_quick_close.card
    assert repository.list_watched_models() == ["openai/existing"]


def test_card_action_callback_returns_raw_card_and_deduplicates_event(
    tmp_path: Path,
) -> None:
    cards, repository, _ = make_service(
        tmp_path,
        [model("openai/gpt-4.1")],
    )
    home = cards.open_management_card().content
    add = cards.handle_action(callback_value(buttons(home, "添加模型")[0]))
    watch_value = callback_value(buttons(add.card, "关注")[0])
    payload = {
        "header": {"event_id": "same-event"},
        "event": {"action": {"value": watch_value}},
    }
    deduplicator = EventDeduplicator(60)

    first = handle_card_action_payload(payload, cards, deduplicator=deduplicator)
    second = handle_card_action_payload(payload, cards, deduplicator=deduplicator)

    assert first.card.type == "raw"
    assert first.card.data["schema"] == "2.0"
    assert first.toast.type == "success"
    assert "已处理" in second.toast.content
    assert repository.list_watched_models() == ["openai/gpt-4.1"]


def test_filter_action_requires_complete_validated_fields(tmp_path: Path) -> None:
    cards, repository, _ = make_service(
        tmp_path,
        [model("openai/gpt-4.1")],
    )
    home = cards.open_management_card().content
    add = cards.handle_action(callback_value(buttons(home, "添加模型")[0]))
    value = callback_value(buttons(add.card, "筛选")[0])

    missing = dict(value)
    del missing["query"]
    extra = dict(value, unexpected=True)
    no_form = cards.handle_action(value)

    assert cards.handle_action(missing).toast_type == "error"
    assert cards.handle_action(extra).toast_type == "error"
    assert no_form.toast_type == "error"
    assert callback_value(buttons(add.card, "筛选")[0])["action"] == ACTION_FILTER
    assert repository.list_watched_models() == []
