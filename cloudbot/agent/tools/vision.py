"""Vision model tool for describing images via a hosted multimodal API."""

import openai
from openai import AsyncOpenAI

from cloudbot.agent.registry import tool

_VISION_IMAGE_SIZE_LIMIT = 10 * 1024 * 1024
_VISION_MAX_TOKENS = 1024


def _resolve_vision_config(bot) -> tuple[str, str, str]:
    vision_cfg = (bot.config.get("plugins") or {}).get("agent", {}).get(
        "vision"
    ) or {}
    base_url = (
        vision_cfg.get("base_url") or "https://api.z.ai/api/coding/paas/v4"
    )
    model = vision_cfg.get("model") or "glm-5v-turbo"
    api_key_path = vision_cfg.get("api_key_config_path") or "z_ai"
    api_key = bot.config.get_api_key(api_key_path)
    return base_url, model, api_key


@tool(
    name="describe_image",
    description=(
        "Describe an image using the vision model. "
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
    base_url, model, api_key = _resolve_vision_config(bot)
    if not api_key:
        return "(error: api key not configured)"

    try:
        client = AsyncOpenAI(base_url=base_url, api_key=api_key)
        completion = await client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": url}},
                        {"type": "text", "text": question},
                    ],
                }
            ],
            max_tokens=_VISION_MAX_TOKENS,
        )
        content = completion.choices[0].message.content
        if content:
            return content
        rc = getattr(completion.choices[0].message, "reasoning_content", None)
        if rc:
            return rc
        return "(no description)"
    except openai.RateLimitError:
        return "(error: vision model rate-limited — try again in a moment)"
    except openai.APIError as e:
        return (
            f"(error calling vision model: {type(e).__name__}: {str(e)[:200]})"
        )
    except (OSError, ValueError, RuntimeError) as e:
        return f"(error calling vision model: {e})"
