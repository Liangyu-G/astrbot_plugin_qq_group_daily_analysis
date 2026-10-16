"""词云普通发送先于相册归档，归档失败不引发重复消息。"""

from __future__ import annotations

import asyncio
import base64
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.application.handlers.wordcloud_command_handler import WordCloudCommandHandler
from src.application.services.wordcloud_delivery_service import WordCloudDeliveryService
from src.domain.value_objects.wordcloud import WordCloudData, WordFrequency
from src.infrastructure.config.config_manager import ConfigManager
from src.infrastructure.persistence.wordcloud_store import WordCloudStore


def setup_delivery(
    tmp_path, *, upload_config=None, image=b"wordcloud", send_result=True
):
    """建立生产发送用例，替换实际网络和渲染边界。"""
    config = object.__new__(ConfigManager)
    config.config = {
        "qq_group_upload": upload_config
        if upload_config is not None
        else {
            "enable_wordcloud_album_upload": True,
            "group_album_name": "群分析",
            "group_album_strict_mode": True,
        }
    }
    calls = []
    store = WordCloudStore(tmp_path / "wordcloud.db")

    async def send(*_args, **_kwargs):
        calls.append("send-image")
        if isinstance(send_result, Exception):
            raise send_result
        return send_result

    async def find(*_args):
        calls.append("find-album")
        return "album-id"

    async def upload(**_kwargs):
        # 相册调用前，普通消息成功已经持久化，之后即使归档失败也不自动重发。
        with sqlite3.connect(store.path) as conn:
            assert (
                conn.execute(
                    "SELECT status FROM wordcloud_delivery ORDER BY updated_at DESC LIMIT 1"
                ).fetchone()[0]
                == "sent"
            )
        calls.append("upload-album")
        return True

    adapter = SimpleNamespace(
        find_album_id=AsyncMock(side_effect=find),
        upload_group_album=AsyncMock(side_effect=upload),
        get_group_album_list=AsyncMock(return_value=[]),
    )
    bot_manager = SimpleNamespace(
        get_adapter=Mock(return_value=adapter), update_from_event=Mock()
    )
    sender = SimpleNamespace(
        bot_manager=bot_manager, send_image_smart=AsyncMock(side_effect=send)
    )
    renderer = SimpleNamespace(render=AsyncMock(return_value=image))
    service = WordCloudDeliveryService(config, renderer, store, sender)
    data = WordCloudData(
        (WordFrequency("高性能", 12, 8),), message_count=8, group_id="123"
    )
    return service, data, adapter, calls


def test_album_config_is_default_off_and_kept_in_existing_upload_group():
    schema = json.loads(
        (Path(__file__).parents[1] / "_conf_schema.json").read_text(encoding="utf-8")
    )
    setting = schema["qq_group_upload"]["items"]["enable_wordcloud_album_upload"]
    assert setting["type"] == "bool"
    assert setting["default"] is False
    assert "enable_wordcloud_album_upload" not in schema["wordcloud"]["items"]
    config = object.__new__(ConfigManager)
    config.config = {
        "qq_group_upload": {
            "enable_group_album_upload": True,
            "enable_comic_album_upload": True,
        }
    }
    assert not config.get_enable_wordcloud_album_upload()


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["report", "standalone", "retry"])
async def test_all_wordcloud_entry_points_send_before_album(tmp_path, entry):
    service, data, adapter, calls = setup_delivery(tmp_path)
    if entry == "report":
        assert await service.send_with_report(data, "123", "bot")
    else:
        service.config_manager.is_group_allowed = lambda _umo: True
        analysis = SimpleNamespace(
            collect_wordcloud=AsyncMock(return_value=data),
            bot_manager=service.message_sender.bot_manager,
        )
        handler = WordCloudCommandHandler(analysis, service)
        event = SimpleNamespace(
            should_call_llm=Mock(),
            get_group_id=lambda: "123",
            get_platform_id=lambda: "bot",
            unified_msg_origin="bot:GroupMessage:123",
            plain_result=lambda value: value,
        )
        if entry == "retry":
            service.store.save(event.unified_msg_origin, "cached", data, "cached")
        result = [item async for item in handler.handle(event, retry=entry == "retry")]
        assert result == ([] if entry == "retry" else ["正在提取群聊词频并生成词云……"])
        assert analysis.collect_wordcloud.await_count == (0 if entry == "retry" else 1)
    assert calls == ["send-image", "find-album", "upload-album"]
    adapter.upload_group_album.assert_awaited_once_with(
        group_id="123",
        image_path="base64://" + base64.b64encode(b"wordcloud").decode(),
        album_id="album-id",
        album_name="群分析",
        strict_mode=True,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("send_result", [False, RuntimeError("发送失败")])
async def test_failed_image_send_never_uploads_album(tmp_path, send_result):
    service, data, adapter, calls = setup_delivery(tmp_path, send_result=send_result)
    assert not await service.send(data, "123", "bot")
    assert calls == ["send-image"]
    adapter.find_album_id.assert_not_awaited()
    adapter.upload_group_album.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("result", [False, RuntimeError("相册拒绝访问")])
async def test_album_failure_preserves_sent_state_and_prevents_automatic_resend(
    tmp_path, result
):
    service, data, adapter, _ = setup_delivery(tmp_path)
    adapter.upload_group_album.side_effect = (
        result if isinstance(result, Exception) else None
    )
    adapter.upload_group_album.return_value = result
    assert await service.send(data, "123", "bot")
    assert await service.send_with_report(data, "123", "bot")
    assert service.message_sender.send_image_smart.await_count == 1
    assert adapter.upload_group_album.await_count == 1
    with sqlite3.connect(service.store.path) as conn:
        assert conn.execute(
            "SELECT status,error FROM wordcloud_delivery"
        ).fetchone() == ("sent", "")


@pytest.mark.asyncio
async def test_cancelled_album_is_not_swallowed_or_marked_failed(tmp_path):
    service, data, adapter, _ = setup_delivery(tmp_path)
    adapter.upload_group_album.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await service.send(data, "123", "bot")
    assert await service.send(data, "123", "bot")
    assert service.message_sender.send_image_smart.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("album_name", ["", "不存在的相册"])
async def test_strict_mode_skips_unresolved_album_without_fallback(
    tmp_path, album_name
):
    service, data, adapter, _ = setup_delivery(
        tmp_path,
        upload_config={
            "enable_wordcloud_album_upload": True,
            "group_album_name": album_name,
            "group_album_strict_mode": True,
        },
    )
    adapter.find_album_id.side_effect = None
    adapter.find_album_id.return_value = None
    assert await service.send(data, "123", "bot")
    adapter.upload_group_album.assert_not_awaited()
    assert adapter.find_album_id.await_count == (1 if album_name else 0)


@pytest.mark.asyncio
async def test_non_strict_mode_preserves_existing_default_album_behavior(tmp_path):
    service, data, adapter, _ = setup_delivery(
        tmp_path,
        upload_config={
            "enable_wordcloud_album_upload": True,
            "group_album_name": "",
            "group_album_strict_mode": False,
        },
    )
    assert await service.send(data, "123", "bot")
    assert adapter.upload_group_album.call_args.kwargs["album_id"] is None
    assert adapter.upload_group_album.call_args.kwargs["album_name"] == ""
    assert adapter.upload_group_album.call_args.kwargs["strict_mode"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("disabled", [True, False])
async def test_disabled_or_unsupported_platform_skips_album(tmp_path, disabled):
    service, data, adapter, _ = setup_delivery(tmp_path)
    if disabled:
        service.config_manager.config["qq_group_upload"][
            "enable_wordcloud_album_upload"
        ] = False
    else:
        service.message_sender.bot_manager.get_adapter.return_value = SimpleNamespace(
            get_platform_name=lambda: "qq_official"
        )
    assert await service.send(data, "123", "bot")
    adapter.find_album_id.assert_not_awaited()
    adapter.upload_group_album.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["path", "url", "base64", "data"])
async def test_upload_reuses_rendered_image_and_retains_cached_file(tmp_path, source):
    cached = tmp_path / "rendered.png"
    cached.write_bytes(b"wordcloud")
    encoded = base64.b64encode(cached.read_bytes()).decode()
    image = {
        "path": str(cached),
        "url": "https://example.com/wordcloud.png",
        "base64": "base64://" + encoded,
        "data": "data:image/png;base64," + encoded,
    }[source]
    service, data, adapter, _ = setup_delivery(tmp_path, image=image)
    assert await service.send(data, "123", "bot")
    expected = "base64://" + encoded if source == "data" else image
    assert adapter.upload_group_album.call_args.kwargs["image_path"] == expected
    assert cached.read_bytes() == b"wordcloud"
    service.renderer.render.assert_awaited_once()


@pytest.mark.asyncio
async def test_explicit_retry_can_send_and_archive_again(tmp_path):
    service, data, adapter, calls = setup_delivery(tmp_path)
    assert await service.send(data, "123", "bot")
    assert await service.send(data, "123", "bot", force=True)
    assert calls == ["send-image", "find-album", "upload-album"] * 2
    assert adapter.upload_group_album.await_count == 2
