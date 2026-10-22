"""AstrBot HTML 渲染回调的会话配置兼容适配。"""

from __future__ import annotations

from functools import partial
from inspect import Parameter, signature
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

RenderResult = TypeVar("RenderResult")


def bind_group_html_renderer(
    render_func: Callable[..., Awaitable[RenderResult]] | None,
    group_id: str,
    platform_id: str | None,
) -> Callable[..., Awaitable[RenderResult]] | None:
    """按群聊绑定渲染配置，兼容不接受 umo 的旧版 AstrBot。

    Args:
        render_func: 宿主提供的 HTML 渲染回调。
        group_id: 目标群组标识。
        platform_id: 平台实例标识；缺失时沿用宿主默认配置。

    Returns:
        仅为本次报告绑定会话的回调；旧版或无法检查签名时返回原回调。
    """
    if render_func is None or not group_id or not platform_id:
        return render_func
    try:
        parameters = signature(render_func).parameters
    except (TypeError, ValueError):
        return render_func
    umo_parameter = parameters.get("umo")
    if (
        umo_parameter is not None
        and umo_parameter.kind
        in (Parameter.POSITIONAL_OR_KEYWORD, Parameter.KEYWORD_ONLY)
    ) or any(p.kind == Parameter.VAR_KEYWORD for p in parameters.values()):
        return partial(render_func, umo=f"{platform_id}:GroupMessage:{group_id}")
    return render_func
