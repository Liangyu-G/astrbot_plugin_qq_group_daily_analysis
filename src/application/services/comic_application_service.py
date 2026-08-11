import mimetypes
import random
from pathlib import Path

import httpx

from ...infrastructure.analysis.llm_analyzer import LLMAnalyzer
from ...infrastructure.config.config_manager import ConfigManager
from ...infrastructure.drawing.drawing_client import (
    DrawingClient,
    ImageDownloadFailedError,
)
from ...utils.logger import logger


class ComicApplicationService:
    """
    负责统筹每日群漫画的生成流程：
    1. 调用 LLMAnalyzer 生成一张包含所有金句的拼贴分镜提示词。
    2. 调用 DrawingClient 直接生成单张连环漫画长图。
    3. 返回图片数据供外部上传。
    """

    def __init__(
        self,
        llm_analyzer: LLMAnalyzer,
        drawing_client: DrawingClient,
        config_manager: ConfigManager,
    ):
        self.llm_analyzer = llm_analyzer
        self.drawing_client = drawing_client
        self.config_manager = config_manager

    async def generate_comic(
        self,
        topics: list[dict],
        group_id: str,
        umo: str | None = None,
    ) -> tuple[bytes | None, str | None]:
        """
        生成漫画并返回图片字节数据。

        Returns:
            (comic_bytes, fallback_url):
            - comic_bytes: 生成成功时为图片字节，失败时为 None。
            - fallback_url: 图片 API 返回了 URL 但下载失败时为该 URL，其他情况为 None。
        """
        if not self.config_manager.get_enable_daily_comic():
            return None, None

        logger.info(f"[Comic] 开始为群 {group_id} 生成每日漫画...")

        # 1. 提取分镜和金句
        storyboards, _ = await self.llm_analyzer.analyze_comic_storyboards(topics, umo)

        if not storyboards:
            logger.warning(
                f"[Comic] 群 {group_id} 未能提取到任何金句分镜，取消漫画生成。"
            )
            return None, None

        logger.info("[Comic] 成功提取到全景分镜提示词，开始调用绘画 API...")

        # 2. 直接生成一张图片
        scene_prompt = storyboards[0].get("scene", "")
        if not scene_prompt:
            logger.error("[Comic] 提取到的场景提示词为空，取消漫画生成。")
            return None, None

        logger.info(f"[Comic] 生成漫画 Prompt:\n{scene_prompt}")

        # 3. 处理参考图
        images_data = None
        ref_img_path_or_url = self.config_manager.get_drawing_reference_image()
        if ref_img_path_or_url:
            reference_image = await self._fetch_reference_image(ref_img_path_or_url)
            if reference_image:
                images_data = [reference_image]
                logger.info(f"[Comic] 成功加载参考图: {ref_img_path_or_url}")
            else:
                logger.warning(
                    f"[Comic] 无法加载参考图: {ref_img_path_or_url}，将不使用参考图。"
                )

        # 4. 调用绘图 API，捕获"有 URL 但下载失败"的情况
        fallback_url: str | None = None
        try:
            final_comic_bytes, last_error = await self.drawing_client.generate_image(
                scene_prompt, images_data=images_data
            )
        except ImageDownloadFailedError as exc:
            logger.warning(f"[Comic] 图片下载失败，保留 fallback URL: {exc.fallback_url}")
            return None, exc.fallback_url

        exception_keywords = (
            self.config_manager.get_drawing_output_exception_retry_keywords()
        )
        should_rewrite_prompt = bool(
            last_error
            and any(keyword in last_error for keyword in exception_keywords if keyword)
        )
        if not final_comic_bytes and last_error and should_rewrite_prompt:
            logger.info(
                f"[Comic] 画图重试已用尽，请求 LLM 分析报错并重写 Prompt: {last_error}"
            )
            new_prompt = await self.llm_analyzer.analyze_retry_prompt(
                scene_prompt, last_error, umo
            )
            if new_prompt:
                logger.info("[Comic] 获取到重写后的 Prompt，进行最后一次尝试...")
                try:
                    final_comic_bytes, _ = await self.drawing_client.generate_image(
                        new_prompt, images_data=images_data, disable_retry=True
                    )
                except ImageDownloadFailedError as exc:
                    logger.warning(
                        f"[Comic] 重写 Prompt 后图片下载仍失败，保留 fallback URL: {exc.fallback_url}"
                    )
                    return None, exc.fallback_url

        if final_comic_bytes:
            logger.info(f"[Comic] 漫画生成成功，大小: {len(final_comic_bytes)} bytes")
        else:
            logger.error("[Comic] 漫画生成最终失败。")

        return final_comic_bytes, fallback_url

    async def _fetch_reference_image(
        self, path_or_url: str
    ) -> tuple[bytes, str] | None:
        """从 URL 或本地路径获取图片数据及 MIME 类型。

        Args:
            path_or_url: 图片 URL、本地文件路径或本地目录路径。

        Returns:
            图片字节和 MIME 类型；加载失败时返回 None。
        """
        try:
            path_str = path_or_url.strip()
            if path_str.startswith("http://") or path_str.startswith("https://"):
                proxy = self.config_manager.get_drawing_download_proxy() or None
                async with httpx.AsyncClient(timeout=30.0, proxy=proxy) as client:
                    resp = await client.get(path_str)
                    resp.raise_for_status()
                    content_type = resp.headers.get("Content-Type", "")
                    mime_type = content_type.split(";", 1)[0].strip().lower()
                    if not mime_type.startswith("image/"):
                        guessed_type, _ = mimetypes.guess_type(path_str)
                        mime_type = guessed_type or "image/jpeg"
                    return resp.content, mime_type
            else:
                local_path = Path(path_str).expanduser()

                if local_path.is_file():
                    guessed_type, _ = mimetypes.guess_type(local_path.name)
                    return local_path.read_bytes(), guessed_type or "image/jpeg"
                elif local_path.is_dir():
                    valid_exts = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
                    files = [
                        path
                        for path in local_path.iterdir()
                        if path.is_file() and path.suffix.lower() in valid_exts
                    ]
                    if files:
                        chosen = random.choice(files)
                        guessed_type, _ = mimetypes.guess_type(chosen.name)
                        return chosen.read_bytes(), guessed_type or "image/jpeg"
                    else:
                        logger.warning(
                            f"[Comic] 参考图目录 {path_str} 中没有有效的图片文件。"
                        )
                        return None
                else:
                    logger.warning(f"[Comic] 找不到本地参考图: {path_str}")
                    return None
        except Exception as e:
            logger.error(f"[Comic] 获取参考图失败 {path_or_url}: {e}")
            return None
