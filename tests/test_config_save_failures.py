from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.infrastructure.config.config_manager import ConfigManager
from src.infrastructure.webui.routes.config_routes import ConfigRoutes


class ConfigWithSave(dict):
    def __init__(self):
        super().__init__()
        self.save_config = MagicMock()


@pytest.fixture
def manager():
    instance = object.__new__(ConfigManager)
    instance.config = ConfigWithSave()
    return instance


@pytest.mark.parametrize("error", [PermissionError("没有写入权限"), OSError("磁盘空间不足")])
def test_save_propagates_original_error_without_success_log(manager, error):
    manager.config.save_config.side_effect = error
    with patch("src.infrastructure.config.config_manager.logger") as logger:
        with pytest.raises(type(error)) as raised:
            manager.save_config()
    assert raised.value is error
    manager.config.save_config.assert_called_once_with()
    logger.info.assert_not_called()


def test_successful_save_still_delegates_to_astrbot(manager):
    with patch("src.infrastructure.config.config_manager.logger") as logger:
        manager.save_config()
    manager.config.save_config.assert_called_once_with()
    logger.error.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [PermissionError("没有写入权限"), OSError("磁盘空间不足")])
async def test_webui_returns_failure_and_skips_scheduler_when_write_fails(manager, error):
    manager.config.save_config.side_effect = error
    scheduler = MagicMock()
    scheduler.incremental_trigger.refresh_target_states = AsyncMock()
    routes = ConfigRoutes(
        MagicMock(config_manager=manager), context=MagicMock(), auto_scheduler=scheduler
    )
    with patch("src.infrastructure.webui.routes.config_routes.request") as request:
        request.json = AsyncMock(return_value={"config": {"basic": {"max_topics": 5}}})
        response = await routes.api_save_config()
    assert response["status_code"] == 500
    assert response["message"] == str(error)
    assert "持久化生效" not in str(response)
    scheduler.schedule_jobs.assert_not_called()
    scheduler.incremental_trigger.refresh_target_states.assert_not_awaited()


@pytest.mark.asyncio
async def test_webui_returns_success_and_refreshes_scheduler_after_write(manager):
    scheduler = MagicMock()
    scheduler.incremental_trigger.refresh_target_states = AsyncMock()
    routes = ConfigRoutes(
        MagicMock(config_manager=manager), context=MagicMock(), auto_scheduler=scheduler
    )
    with patch("src.infrastructure.webui.routes.config_routes.request") as request:
        request.json = AsyncMock(return_value={"config": {"basic": {"max_topics": 5}}})
        response = await routes.api_save_config()
    assert response["status_code"] == 200
    assert response["data"]["status"] == "ok"
    assert manager.config["basic"]["max_topics"] == 5
    manager.config.save_config.assert_called_once_with()
    scheduler.schedule_jobs.assert_called_once_with(routes.context)
    scheduler.incremental_trigger.refresh_target_states.assert_awaited_once_with()
