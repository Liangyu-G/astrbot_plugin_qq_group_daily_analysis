"""词云独立发送用例，统计缓存与日报结果隔离。"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from typing import TYPE_CHECKING

from ...domain.repositories.platform_adapter_repository import GroupAlbumSupportProtocol
from ...shared.trace_context import TraceContext
from ...utils.logger import logger

if TYPE_CHECKING:
    from ...domain.value_objects.wordcloud import WordCloudData
    from ...infrastructure.config.config_manager import ConfigManager
    from ...infrastructure.messaging.message_sender import MessageSender
    from ...infrastructure.persistence.wordcloud_store import WordCloudStore
    from ...infrastructure.reporting.wordcloud_renderer import WordCloudRenderer


class WordCloudDeliveryService:
    """协调缓存、出图和发送；失败不改变已发送日报的结果。"""

    def __init__(
        self,
        config_manager: ConfigManager,
        renderer: WordCloudRenderer,
        store: WordCloudStore,
        message_sender: MessageSender,
    ) -> None:
        self.config_manager = config_manager
        self.renderer = renderer
        self.store = store
        self.message_sender = message_sender
        self._send_lock = asyncio.Lock()

    async def send(
        self,
        data: WordCloudData,
        group_id: str,
        platform_id: str,
        *,
        force: bool = False,
        umo: str = "",
    ) -> bool:
        """发送词云并独立持久化结果。

        Args:
            data: 完整词频快照。
            group_id: 目标群号。
            platform_id: 目标平台实例。
            force: 显式重发时忽略成功记录。
            umo: 完整群会话标识，用于群配置及框架 T2I 选择。

        Returns:
            是否发送成功，或同一快照此前已发送成功。
        """
        umo = umo or f"{platform_id}:GroupMessage:{group_id}"
        options = self.config_manager.get_wordcloud_options(umo)
        fingerprint = hashlib.sha256(
            json.dumps(
                {"data": data.to_dict(), "options": options.to_dict()},
                ensure_ascii=False,
                sort_keys=True,
            ).encode()
        ).hexdigest()
        async with self._send_lock:
            if not force and self.store.is_sent(umo, fingerprint):
                return True
            self.store.save(umo, fingerprint, data, "pending")
            trace = TraceContext.current()
            try:
                if not options.enabled:
                    raise ValueError("词云功能已关闭")
                image = await self.renderer.render(data, umo=umo, options=options)
                image_url = (
                    "base64://" + base64.b64encode(image).decode()
                    if isinstance(image, bytes)
                    else image
                )
                sent = await self.message_sender.send_image_smart(
                    group_id,
                    image_url,
                    caption=(
                        TraceContext.make_report_caption("wordcloud")
                        if self.config_manager.get_show_report_caption()
                        else ""
                    ),
                    platform_id=platform_id,
                )
                if not sent:
                    raise RuntimeError("平台未成功发送词云图片")
                self.store.save(umo, fingerprint, data, "sent")
                if trace:
                    trace.metadata["wordcloud"] = {
                        "status": "sent",
                        "fingerprint": fingerprint,
                    }
            except Exception as exc:
                self.store.save(umo, fingerprint, data, "failed", str(exc))
                if trace:
                    trace.metadata["wordcloud"] = {
                        "status": "failed",
                        "error": str(exc),
                        "fingerprint": fingerprint,
                    }
                logger.warning(
                    f"群 {group_id} 词云发送失败，可使用 /重发词云 重试：{exc}"
                )
                return False
            # 发送成功状态先落库；相册只是附加归档，不能让失败导致群里重复发图。
            await self._try_upload_album(group_id, image_url, platform_id)
            return True

    async def _try_upload_album(
        self, group_id: str, image_url: str, platform_id: str
    ) -> None:
        """在普通图片发送成功后，复用平台群相册能力尝试归档。

        Args:
            group_id: 已成功发送图片的目标群。
            image_url: 同一张词云的本地路径、URL 或 Base64 数据。
            platform_id: 发送时使用的平台实例。
        """
        try:
            if not self.config_manager.get_enable_wordcloud_album_upload():
                return
            adapter = self.message_sender.bot_manager.get_adapter(platform_id)
            if not isinstance(adapter, GroupAlbumSupportProtocol):
                return
            album_name = self.config_manager.get_wordcloud_album_name()
            strict_mode = self.config_manager.get_group_album_strict_mode()
            album_id = None
            if album_name:
                album_id = await adapter.find_album_id(group_id, album_name)
                if not album_id and strict_mode:
                    logger.info(
                        f"词云群相册严格模式开启：在群 {group_id} 中未找到名为 '{album_name}' 的相册，停止上传。"
                    )
                    return
            elif strict_mode:
                logger.info(
                    f"词云群相册严格模式开启：未设置目标相册名称，停止上传以防止操作群 {group_id} 的默认相册。"
                )
                return
            # OneBot 现有传输策略支持路径、URL 和 base64://，不另存或删除词云缓存。
            if image_url.startswith("data:image/") and ";base64," in image_url:
                image_url = "base64://" + image_url.split(";base64,", 1)[1]
            uploaded = await adapter.upload_group_album(
                group_id=group_id,
                image_path=image_url,
                album_id=album_id,
                album_name=album_name,
                strict_mode=strict_mode,
            )
            if not uploaded:
                logger.warning(
                    f"词云群相册上传失败 (群 {group_id})：平台未确认上传成功，不影响已发送的词云"
                )
        except Exception as exc:
            logger.warning(
                f"词云群相册上传失败 (群 {group_id})，不影响已发送的词云：{exc}"
            )

    async def send_with_report(
        self, data: WordCloudData | None, group_id: str, platform_id: str
    ) -> bool:
        """日报确认发送成功后，按配置附送词云。"""
        if data is None:
            return False
        try:
            options = self.config_manager.get_wordcloud_options(
                f"{platform_id}:GroupMessage:{group_id}"
            )
            if not options.enabled or not options.send_with_report:
                if options.enabled:
                    fingerprint = hashlib.sha256(
                        json.dumps(data.to_dict(), sort_keys=True).encode()
                    ).hexdigest()
                    self.store.save(
                        f"{platform_id}:GroupMessage:{group_id}",
                        fingerprint,
                        data,
                        "cached",
                    )
                return False
            return await self.send(data, group_id, platform_id)
        except Exception as exc:
            logger.warning(f"群 {group_id} 词云附送异常：{exc}")
            return False
