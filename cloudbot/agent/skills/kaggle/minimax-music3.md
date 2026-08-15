---
name: minimax-music3
description: Generate a song (44.1 kHz stereo wav, real sung vocals) from lyrics + a style prompt using MiniMax Music 3 on Kaggle GPU. Ask for it by name, or when ACE-Step quality is not enough; it costs several times more GPU time than ace-music.
---

# Song from lyrics + prompt (MiniMax Music 3)

Generates a `.wav` from a music description and lyrics with MiniMax Music 3 (an 8B
autoregressive language model plus a 2.4B flow-matching transformer and a DAC-style
vocoder) on Kaggle's free GPU. Output is 44.1 kHz stereo with sung vocals.

Slower and heavier than [ace-music]: 5-7 minutes of setup before a single frame is
generated (pip, 28.5 GB of weights, then placing them), and ~0.3 s of GPU per audio frame
at 25 frames per second. A 60 s song is about 14 minutes end to end (measured: 863 s). Use
ace-music when the user just wants a song fast.

## What to change, and what not to

Every cell is yours to edit - this is a notebook you are writing, not a fixed template.
Tune the music freely with the knobs below.

Six things are load-bearing. Each one is here because a real run failed without it, so
change them only if you have a reason better than tidiness:

1. `load_components()` after `from_pretrained()` - `from_pretrained` only builds component
   *specs*, so without it every component is `None` and the first block calls `None(text)`.
2. `dtype={'default': torch.float16, 'vocoder': torch.bfloat16}` - a T4 has no bf16 tensor
   cores, so all-bf16 is 4x slower; the vocoder is the one component that overflows in
   fp16 and makes the wav all-NaN.
3. Loading to CPU first, then placing - a `device_map` at load time materializes the full
   tensors on one card and OOMs.
4. The whole flow-matching half on `cuda:0`, only the language model split - the denoise
   blocks look for their components on `pipe._execution_device`.
5. The cross-device shims - the autoregressive loop calls submodules directly where
   accelerate's hooks do not reach.
6. Importing the diffusers classes *before* patching `torch.cat` - those modules are
   JIT-scripted and cannot be imported once a `*args` wrapper is in place.

## Knobs

The first three are the request; the rest are for when the first result is not what the
user wanted.

| knob | where | default | effect |
|---|---|---|---|
| `PROMPT` | cell 6 | - | genre, BPM, key, mood, **vocal gender and timbre**, arrangement |
| `LYRICS` | cell 6 | - | the words, with `[verse]`/`[chorus]` tags |
| `DURATION` | cell 6 | 60.0 | upper bound in seconds; the model may stop earlier |
| seed | cell 6 `manual_seed(7)` | 7 | a different seed is a different take of the same request |
| `num_inference_steps` | cell 6 | 24 (library default 30) | flow-matching steps per window; higher is cleaner and slower, and only affects the denoise stage, which is a small part of the run |
| guidance scale | `pipe.update_components(guider=ClassifierFreeGuidance(guidance_scale=x))` | 1.7 | how hard the flow-matching stage follows the conditioning |
| `_AR_SAMPLING_TOP_K`, `_AR_CFG_SCALE` | `diffusers.modular_pipelines.minimax_music3.encoders` | 50, 1.5 | sampling diversity and prompt adherence of the *singing* stage; patch the module attribute before generating |

The last two rows are documented from the source but were not exercised by the verified
runs - if you change them, say so when you report the result.

## Writing the prompt

Structure it as global metadata, then vocals, then arrangement:

> Genre: slow electric blues, twelve bar. BPM: 68. Key: E minor. Emotional progression:
> weary and smoky in the verse, opening into a pleading chorus. Vocals: gritty male lead,
> raspy and close-mic'd, with gospel-tinged backing voices in the chorus. Arrangement:
> walking upright bass, brushed drums with a heavy backbeat, warm Hammond organ, and an
> overdriven Telecaster answering every vocal line; live room sound, tape-saturated finish.

**Name the vocal gender and timbre explicitly** or the model drifts instrumental. Markdown
in the prompt is stripped by the model's own input cleaner, so bullets and bold do nothing.
The assembled prompt is capped at 5000 tokens and raises above that.

## Writing the lyrics

The checkpoint's input contract rewrites the lyrics before they are sung, and it drops
text silently, so follow it:

- A structure tag must sit **alone on its line**. `[verse] Woke up this morning` loses the
  line - only `[verse]` survives.
- Tags are lowercased, and `[start]` is prepended for you.
- Recognised tags: `[intro]`, `[verse]`, `[pre-chorus]`, `[chorus]`, `[bridge]`,
  `[instrumental]`, `[solo]`, `[outro]`.
- `lyrics` must be non-empty - there is no instrumental-only mode. For an instrumental,
  the closest thing is a single `[instrumental]` tag.
- Write roughly 4 lines per 15 s of audio. Far more lyrics than the duration allows means
  the song is cut off mid-word at `DURATION`.

## Length and what it costs

Generation is ~0.3 s of GPU per frame at 25 frames per second, so:

```
total seconds ~= 400 (setup) + 7.5 * DURATION
```

60 s -> ~850 s (measured 863). 120 s -> ~1300 s. The `timeout_s` cap is 1800, which puts
the ceiling near 150 s of audio; ask for `timeout_s=1800` whatever the length. The
checkpoint's own limit is 9000 frames (6 minutes), reachable only outside this cap.

## Run it

Call `kaggle_run_notebook` with a title naming this request (e.g.
`minimax-music3-neon-rain`, not a fixed name - two requests under one title collide), plus
`gpu=true`, `internet=true`, `timeout_s=1800`, and the cells below. A 60 s song takes ~14
minutes, which outlasts a single `kaggle_wait_for_notebook` (600 s max), so expect to call
it two or three times in a row until it reports a terminal state. Never poll with
`kaggle_notebook_status`.

### Cell 1 (code) - environment

```python
import time, sys, os
os.environ['PYTORCH_ALLOC_CONF'] = 'expandable_segments:True'
T0 = time.time()
def mark(msg): print(f'[{time.time()-T0:7.1f}s] {msg}', flush=True)

import torch
mark(f'torch={torch.__version__} gpus={torch.cuda.device_count()}')
assert torch.cuda.device_count() == 2, 'needs the 2x T4 machine; the model does not fit one card'
for i in range(torch.cuda.device_count()):
    cap = torch.cuda.get_device_capability(i)
    mark(f'  cuda:{i} {torch.cuda.get_device_name(i)} sm_{cap[0]}{cap[1]}')
    assert cap >= (7, 0), f'cuda:{i} is sm_{cap[0]}{cap[1]}; torch 2.10 dropped sm_60 (P100)'
    (torch.zeros(4, device=f'cuda:{i}') + 1).sum().item()
mark('gpu smoke test ok')
```

### Cell 2 (code) - diffusers at the pinned commit

```python
!pip install -q 'git+https://github.com/huggingface/diffusers@9cdd65902a576493acea190d6bc115afb41d4709' soundfile
import diffusers, transformers
mark(f'diffusers={diffusers.__version__} transformers={transformers.__version__}')
```

### Cell 3 (code) - weights

```python
from huggingface_hub import snapshot_download

# Only what modular_model_index.json points at: qwen_7B/, flowmatching_vae.pth and
# dav.pth are original-format extras the diffusers pipeline never reads, and skipping
# them takes the download from 57.35 GB to 28.53 GB.
NEEDED = ['*.json', 'language_model/*', 'transformer/*', 'rvq_depth_decoder/*',
          'condition_encoder/*', 'vocoder/*', 'tokenizer/*', 'scheduler/*']
mark('downloading MiniMaxAI/MiniMax-Music3 (modular subset)')
path = snapshot_download('MiniMaxAI/MiniMax-Music3', allow_patterns=NEEDED)
mark(f'weights at {path}')
```

### Cell 4 (code) - load and place

```python
import psutil
from diffusers import ModularPipeline
from accelerate import dispatch_model, infer_auto_device_map

def memstat(tag):
    gpu = {i: f'{torch.cuda.memory_allocated(i)/1e9:.1f}GB' for i in range(torch.cuda.device_count())}
    mark(f'{tag}: gpu={gpu} host={psutil.virtual_memory().used/1e9:.1f}GB')

pipe = ModularPipeline.from_pretrained('MiniMaxAI/MiniMax-Music3')
# from_pretrained only builds component SPECS - every component is None until
# load_components runs, and the first block then calls None(text).
mark(f'specs built; unloaded: {pipe.null_component_names}')

# fp16, not bf16: a T4 is Turing and has no bf16 tensor cores, so bf16 GEMMs fall off the
# fast path (measured 1.94 vs 21.6 TFLOP/s) and every frame costs 4x more. The vocoder is
# the single component that overflows in fp16 - its inputs stay finite, its output does not,
# and the audio comes out all-NaN - so it alone stays bf16. It runs once per song.
#
# Load to CPU: device_map at load time materializes the full tensors on one card, which a
# 17 GB language model cannot survive on a 15.6 GB T4.
pipe.load_components(dtype={'default': torch.float16, 'vocoder': torch.bfloat16})
failed = [n for n, c in pipe.components.items() if c is None]
assert not failed, f'components failed to load: {failed}'   # load_components warns, never raises
memstat('after cpu load')

# The flow-matching half stays on cuda:0, where the denoise blocks look for it.
for name in ['rvq_depth_decoder', 'condition_encoder', 'vocoder', 'transformer']:
    getattr(pipe, name).to('cuda:0')

# The language model spans both cards. CPU group-offloading is the documented small-VRAM
# path but streams 17 GB per frame (~6 s/frame), which no session outlives. Sharding is
# 2.4x faster and its output is byte-identical, so do not "fix" this back to the docs.
# The two cards take turns rather than working at once - that is what a device map does -
# but a decode step is bandwidth bound, so the win here is that the weights sit in VRAM.
device_map = infer_auto_device_map(
    pipe.language_model,
    max_memory={0: '6GiB', 1: '13GiB'},
    no_split_module_classes=['Qwen3DecoderLayer'],
    dtype=torch.float16,
)
assert 'cpu' not in {str(v) for v in device_map.values()}, 'language model spilled to CPU'
dispatch_model(pipe.language_model, device_map=device_map)
memstat('after shard')
mark(f'ready; execution_device={pipe._execution_device} sampling_rate={pipe.sampling_rate}')
```

### Cell 5 (code) - cross-device shims

```python
import torch.nn.functional as F
from collections import Counter

# Resolve every lazily-imported diffusers module BEFORE patching torch.cat: they are
# JIT-scripted, a Python wrapper taking *args cannot be scripted, and importing one after
# the patch dies with NotSupportedError.
from diffusers import (MiniMaxMusic3Blocks, MiniMaxMusic3ConditionEncoder,
                       MiniMaxMusic3RVQDepthDecoder, MiniMaxMusic3Transformer1DModel,
                       MiniMaxMusic3Vocoder)

# The autoregressive loop calls submodules directly (embed_tokens, lm_head, the
# depth-decoder heads) where accelerate's hooks do not reach, so tensors meet weights on
# the other card. Each shim relocates one operand - same values, no semantic change.
_hits = Counter()
_orig_masked_fill = torch.Tensor.masked_fill
_orig_linear, _orig_embedding = F.linear, F.embedding
_orig_cat, _orig_stack = torch.cat, torch.stack

def _masked_fill(self, mask, value):
    if getattr(mask, 'device', self.device) != self.device:
        _hits['masked_fill'] += 1
        mask = mask.to(self.device)
    return _orig_masked_fill(self, mask, value)

def _linear(inp, weight, bias=None):
    if inp.device != weight.device:
        _hits['linear'] += 1
        inp = inp.to(weight.device)
    if bias is not None and bias.device != weight.device:
        bias = bias.to(weight.device)
    return _orig_linear(inp, weight, bias)

def _embedding(inp, weight, *args, **kwargs):
    if inp.device != weight.device:
        _hits['embedding'] += 1
        inp = inp.to(weight.device)
    return _orig_embedding(inp, weight, *args, **kwargs)

def _same_device(tensors, what):
    tensors = list(tensors)
    if tensors and any(t.device != tensors[0].device for t in tensors):
        _hits[what] += 1
        tensors = [t.to(tensors[0].device) for t in tensors]
    return tensors

def _cat(tensors, *args, **kwargs):
    return _orig_cat(_same_device(tensors, 'cat'), *args, **kwargs)

def _stack(tensors, *args, **kwargs):
    return _orig_stack(_same_device(tensors, 'stack'), *args, **kwargs)

torch.Tensor.masked_fill = _masked_fill
F.linear, F.embedding = _linear, _embedding
torch.cat, torch.stack = _cat, _stack
mark('shims installed')
```

### Cell 6 (code) - generate

This is the cell you write for the request. Everything below is a placeholder.

```python
# REQUIRED: replace PROMPT, LYRICS and DURATION with the user's request.
PROMPT = ("Genre: <genre>. BPM: <n>. Key: <key>. "
          "Emotional progression: <mood, and how it changes>. "
          "Vocals: <gender, timbre, close/airy, harmonies>. "
          "Arrangement: <instruments, drums, production finish>.")

LYRICS = """[verse]
<the user's lyrics, or write fitting ones>

[chorus]
<...>"""

DURATION = 60.0        # seconds of audio; ~7.5 s of GPU each
SEED = 7               # change for a different take of the same request
STEPS = 24             # flow-matching steps per window

# Optional: how hard the flow-matching stage follows the conditioning (default 1.7).
# from diffusers.guiders import ClassifierFreeGuidance
# pipe.update_components(guider=ClassifierFreeGuidance(guidance_scale=2.5))

mark(f'generating {DURATION:.0f}s ({DURATION*pipe.frame_rate:.0f} AR frames)')
audio = pipe(
    prompt=PROMPT,
    lyrics=LYRICS,
    audio_duration=DURATION,
    num_inference_steps=STEPS,
    generator=torch.Generator('cuda:0').manual_seed(SEED),
    output='audios',
)[0]
mark(f'generation done - shim hits: {dict(_hits)}')
```

### Cell 7 (code) - save and verify

```python
import numpy as np, soundfile as sf, wave

wave_np = np.asarray(audio, dtype=np.float32)
assert wave_np.ndim == 2 and wave_np.shape[0] == 2, f'expected (2, samples), got {wave_np.shape}'
out = '/kaggle/working/song.wav'
sf.write(out, wave_np.T, pipe.sampling_rate)

with wave.open(out) as w:
    secs = w.getnframes() / w.getframerate()
    mark(f'{out}: {w.getnchannels()}ch {w.getframerate()}Hz {secs:.1f}s {os.path.getsize(out)/1e6:.2f}MB')
assert secs > 5, f'suspiciously short: {secs:.1f}s'
assert np.abs(wave_np).max() > 0.01, 'silent output'
print('AUDIO_OK')
print('=== DONE ===')
```

## Check it worked

The log must end with `AUDIO_OK` and `=== DONE ===`. `state == complete` alone is not a
success signal. Verify the wav with the `wave` module as above - `torchaudio.info` does not
exist in this image and crashing on it has faked a failure on a perfectly good wav before.

`shim hits` naming `linear`, `embedding`, `cat` and `stack` is normal and expected - it is
the sharded language model talking to the flow-matching half.

## When it fails

| symptom | cause | fix |
|---|---|---|
| `'NoneType' object is not callable` in the tokenize block | `load_components()` missing or a component failed | keep the assert in cell 4; read which component is `None` |
| audio is all NaN | vocoder ran in fp16 | keep `'vocoder': torch.bfloat16` |
| `NotSupportedError: Compiled functions can't take variable number of arguments` | `torch.cat` patched before diffusers imported | keep the imports at the top of cell 5 |
| `does not support device_map='balanced'` | a component without `_no_split_modules` | only the language model gets a device map |
| OOM while placing | too much on `cuda:0` | lower `max_memory={0: ...}` so more layers land on card 1 |
| run killed at the cap | `DURATION` too long | 400 + 7.5 * DURATION must stay under `timeout_s` |

## Deliver

The artifact is `song.wav`. Call `kaggle_notebook_output(ref, share="song.wav")` - it
returns an `s.h4ks.com` link. Give the user that link to listen or download.
