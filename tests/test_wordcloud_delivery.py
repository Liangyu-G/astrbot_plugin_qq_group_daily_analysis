"""词云发送与日报隔离、重试及会话隔离回归。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.application.services.wordcloud_delivery_service import WordCloudDeliveryService
from src.domain.value_objects.wordcloud import WordCloudData, WordCloudOptions, WordFrequency
from src.infrastructure.config.config_manager import ConfigManager
from src.infrastructure.persistence.wordcloud_store import WordCloudStore
from src.infrastructure.reporting.dispatcher import ReportDispatcher


def config(values=None):
    manager = object.__new__(ConfigManager)
    manager.config = values or {}
    return manager


def test_config_global_group_and_defaults():
    manager = config({"wordcloud": {"word_spacing": 9, "group_overrides": {
        "a:GroupMessage:1": {"word_spacing": 3, "font": "serif"}
    }}})
    assert manager.get_wordcloud_options().word_spacing == 9
    assert manager.get_wordcloud_options().max_words == 80
    assert manager.get_wordcloud_options("a:GroupMessage:1").word_spacing == 3
    assert manager.get_wordcloud_options("b:GroupMessage:1").word_spacing == 9
    assert manager.get_wordcloud_options("a:GroupMessage:1").font == "serif"


@pytest.mark.asyncio
async def test_failed_wordcloud_can_retry_without_report_or_refetch(tmp_path):
    renderer = SimpleNamespace(render=AsyncMock(side_effect=[RuntimeError("字体不可用"), b"image"]))
    sender = SimpleNamespace(send_image_smart=AsyncMock(return_value=True))
    store = WordCloudStore(tmp_path / "wordcloud.db")
    delivery = WordCloudDeliveryService(config(), renderer, store, sender)
    data = WordCloudData((WordFrequency("高性能", 10, 5),), message_count=5, group_id="1")
    assert not await delivery.send(data, "1", "a")
    cached = store.latest("a:GroupMessage:1")
    assert cached == data
    assert store.latest("b:GroupMessage:1") is None
    assert await delivery.send(cached, "1", "a", force=True)
    assert sender.send_image_smart.await_count == 1
    assert renderer.render.call_args.kwargs["umo"] == "a:GroupMessage:1"
    assert await delivery.send(data, "1", "a")
    assert sender.send_image_smart.await_count == 1


@pytest.mark.asyncio
async def test_concurrent_duplicate_snapshot_sent_once(tmp_path):
    import asyncio
    renderer = SimpleNamespace(render=AsyncMock(return_value=b"image"))
    sender = SimpleNamespace(send_image_smart=AsyncMock(return_value=True))
    service = WordCloudDeliveryService(config(), renderer, WordCloudStore(tmp_path / "w.db"), sender)
    data = WordCloudData((WordFrequency("高性能", 10, 5),), message_count=5)
    assert all(await asyncio.gather(service.send(data, "1", "p"), service.send(data, "1", "p")))
    assert sender.send_image_smart.await_count == 1


@pytest.mark.asyncio
async def test_report_success_survives_wordcloud_failure_and_order():
    calls = []
    async def report(*args):
        calls.append("report")
        return True
    async def cloud(*args):
        calls.append("wordcloud")
        return False
    manager = SimpleNamespace(get_output_format=lambda: ["text"])
    sender = SimpleNamespace(bot_manager=SimpleNamespace(get_adapter=lambda _pid: None))
    dispatcher = ReportDispatcher(manager, None, sender)
    dispatcher._dispatch_text = report
    dispatcher.wordcloud_delivery = SimpleNamespace(send_with_report=cloud)
    assert await dispatcher.dispatch("1", {}, "p")
    assert calls == ["report", "wordcloud"]


@pytest.mark.asyncio
async def test_failed_report_does_not_send_wordcloud():
    dispatcher = ReportDispatcher(SimpleNamespace(get_output_format=lambda: ["text"]), None, None)
    dispatcher._dispatch_text = AsyncMock(return_value=False)
    dispatcher.wordcloud_delivery = SimpleNamespace(send_with_report=AsyncMock())
    assert not await dispatcher.dispatch("1", {}, "p")
    dispatcher.wordcloud_delivery.send_with_report.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_uses_current_appearance(tmp_path):
    manager = config({"wordcloud": {"word_spacing": 4}})
    renderer = SimpleNamespace(render=AsyncMock(return_value=b"image"))
    sender = SimpleNamespace(send_image_smart=AsyncMock(return_value=True))
    service = WordCloudDeliveryService(manager, renderer, WordCloudStore(tmp_path / "w.db"), sender)
    data = WordCloudData((WordFrequency("高性能", 10, 5),))
    await service.send(data, "1", "p")
    manager.config["wordcloud"]["word_spacing"] = 20
    await service.send(service.store.latest("p:GroupMessage:1"), "1", "p", force=True)
    assert renderer.render.call_args.kwargs["options"].word_spacing == 20
    assert renderer.render.call_args.args[0] == data


@pytest.mark.parametrize("bad", [{"word_spacing": -1}, {"word_spacing": 41}, {"min_font_size": 100, "max_font_size": 40}, {"font": "https://bad/font"}])
def test_invalid_settings_rejected(bad):
    with pytest.raises(ValueError):
        WordCloudOptions.from_mapping(bad)
