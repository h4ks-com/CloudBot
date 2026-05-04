"""Vision model tool for describing images via a hosted multimodal API."""

import base64

import openai
import requests
from openai import AsyncOpenAI

from cloudbot.agent.common import run_in_executor
from cloudbot.agent.registry import tool

_VISION_IMAGE_SIZE_LIMIT = 10 * 1024 * 1024  # 10 MB
_VISION_MAX_TOKENS = 512


@tool(
    name="describe_image",
    description=(
        "Download an image URL and describe its contents using a vision model. "
        "Use when a user shares an image link and asks what's in it, or when visual "
        "context would help answer a question. Accepts jpg/png/gif/webp URLs. "
        "Optionally accepts a specific question to answer about the image."
    ),
    wrap_errors=True,
    schema={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Direct URL to the image"},
            "question": {
                "type": "string",
                "description": "Specific question to answer about the image (default: describe it)",
            },
        },
        "required": ["url"],
    },
)
async def describe_image(ctx, data):
    url = str(data.get("url") or "").strip()
    question = str(
        data.get("question") or "Describe this image in detail."
    ).strip()

    if not url:
        return "(error: url required)"
    if not url.startswith(("http://", "https://")):
        return "(error: url must start with http:// or https://)"

    bot = ctx.context.bot
    vision_cfg = (bot.config.get("plugins") or {}).get("agent", {}).get(
        "vision"
    ) or {}
    base_url = vision_cfg.get("base_url") or "https://api.z.ai/api/paas/v4"
    model = vision_cfg.get("model") or "glm-4.6v-flash"
    api_key_path = vision_cfg.get("api_key_config_path") or "z_ai"
    api_key = bot.config.get_api_key(api_key_path)
    if not api_key:
        return f"(error: api key '{api_key_path}' not configured)"

    try:
        resp = await run_in_executor(
            requests.get,
            url,
            timeout=15,
            headers={"User-Agent": "CloudBot/1.0"},
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        return f"(error downloading image: {e})"

    if len(resp.content) > _VISION_IMAGE_SIZE_LIMIT:
        return f"(error: image too large, max {_VISION_IMAGE_SIZE_LIMIT // 1024 // 1024} MB)"

    content_type = (
        resp.headers.get("content-type", "image/jpeg").split(";")[0].strip()
    )
    if not content_type.startswith("image/"):
        content_type = "image/jpeg"

    data_uri = (
        f"data:{content_type};base64,{base64.b64encode(resp.content).decode()}"
    )

    try:
        client = AsyncOpenAI(base_url=base_url, api_key=api_key)
        completion = await client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data_uri}},
                        {"type": "text", "text": question},
                    ],
                }
            ],
            max_tokens=_VISION_MAX_TOKENS,
        )
        return completion.choices[0].message.content or "(no description)"
    except openai.RateLimitError:
        return "(error: vision model rate-limited — try again in a moment)"
    except openai.APIError as e:
        return (
            f"(error calling vision model: {type(e).__name__}: {str(e)[:200]})"
        )
    except (requests.RequestException, OSError, ValueError, RuntimeError) as e:
        return f"(error calling vision model: {e})"
