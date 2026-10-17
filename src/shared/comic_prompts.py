"""漫画内置提示词目录，与 WebUI 共用同一份 JSON。"""

import json
from pathlib import Path

_catalog: object = json.loads(
    Path(__file__).with_name("comic_prompt_templates.json").read_text(encoding="utf-8")
)
if not isinstance(_catalog, dict):
    raise ValueError("漫画提示词目录必须是对象")

COMIC_STORYBOARD_PROMPTS: dict[str, str] = {}
for _key, _entry in _catalog.items():
    if (
        not isinstance(_key, str)
        or not isinstance(_entry, dict)
        or not isinstance(_entry.get("prompt"), str)
    ):
        raise ValueError("漫画提示词目录条目格式无效")
    COMIC_STORYBOARD_PROMPTS[_key] = _entry["prompt"]

DEFAULT_COMIC_STORYBOARD_PROMPT = COMIC_STORYBOARD_PROMPTS["classic"]
