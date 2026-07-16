"""Speech tools for the main agent — text-to-speech and speech-to-text.

Thin wrappers over the LocalAI helpers in ``plugins.ollama`` (the same code the
``.tts`` / ``.stt`` IRC commands use). The plugin is imported lazily in the tool
body: importing it at module load would pull ``plugins.ollama`` while this tools
package is still being assembled, risking a cycle.
"""

from cloudbot.agent.common import run_in_executor
from cloudbot.agent.registry import tool


@tool(
    name="text_to_speech",
    description=(
        "Speak text aloud: synthesize it to audio (LocalAI TTS) and return a public "
        "audio URL. Optionally name a voice. Use to read something out or make a voice clip."
    ),
    schema={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Text to speak"},
            "voice": {
                "type": "string",
                "description": "Optional voice/model name; omit for the default",
            },
        },
        "required": ["text"],
    },
)
async def text_to_speech(ctx, data):
    text = str(data.get("text") or "").strip()
    if not text:
        return "(error: text required)"
    voice = str(data.get("voice") or "").strip()
    # pylint: disable=import-outside-toplevel
    from plugins.ollama import synthesize_speech

    return await run_in_executor(
        synthesize_speech, ctx.context.bot, f"{voice} {text}" if voice else text
    )


@tool(
    name="speech_to_text",
    description=(
        "Transcribe spoken audio at a public URL to text (LocalAI Whisper). Use to "
        "read back / quote what an audio clip says."
    ),
    schema={
        "type": "object",
        "properties": {
            "audio_url": {
                "type": "string",
                "description": "Public http(s) URL of the audio to transcribe",
            },
            "model": {
                "type": "string",
                "description": "Optional STT model name; omit for the default",
            },
        },
        "required": ["audio_url"],
    },
)
async def speech_to_text(ctx, data):
    audio_url = str(data.get("audio_url") or "").strip()
    if not audio_url.startswith(("http://", "https://")):
        return "(error: audio_url must be a public http(s) URL)"
    model = str(data.get("model") or "").strip()
    # pylint: disable=import-outside-toplevel
    from plugins.ollama import transcribe_audio

    return await run_in_executor(
        transcribe_audio,
        ctx.context.bot,
        f"{model} {audio_url}" if model else audio_url,
    )
