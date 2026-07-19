---
name: ace-music
description: Generate a song (wav) from lyrics + a style prompt using the open ACE-Step model on Kaggle GPU. Use for explicit ACE-Step requests or an open-model/self-hosted song; for a quick song the main path is Suno.
---

# Song from lyrics + prompt (ACE-Step)

Generates a `.wav` from a style prompt and optional lyrics with ACE-Step (an open
text-to-music model) on Kaggle's free GPU. A style prompt alone gives an instrumental;
add lyrics for vocals. ~30s track takes about 3 minutes of GPU (plus one-time setup).

The cells below are complete and verified. Use them AS-IS: do not research ACE-Step, do not
rewrite them. The ONLY thing you change is cell 4, where you MUST replace `PROMPT` and
`LYRICS` with the user's actual request — the example values are placeholders and running
them produces the wrong song.

Two knobs map to the request: `PROMPT` (genre/instruments/mood, comma-separated) and
`LYRICS` (empty string for an instrumental; otherwise the words, with `[verse]` / `[chorus]`
section tags). `DURATION` is seconds.

## Two fixed notebooks

- **`ace-step-setup`** — one-time cache: clones the repo and downloads the 8.3 GB
  checkpoints to `/kaggle/working`. Slow (~10-15 min), run ONCE. The generation notebook
  mounts its output, so you never re-download.
- **`ace-step-music`** — the generation run. Always mount the setup:
  `kernel_sources=["h4kscom/ace-step-setup"]`.

Check your notebook list first. If `ace-step-setup` is COMPLETE, skip straight to
generation. Only if it is missing or errored, (re)create it from the setup cells below.

## Generate (the usual path)

Call `kaggle_run_notebook` with `title="ace-step-music"`, `gpu=true`, `internet=true`
(pip install needs the network; the checkpoints are mounted, not downloaded),
`timeout_s=1500`, `kernel_sources=["h4kscom/ace-step-setup"]`, and the cells below. Set
`PROMPT`/`LYRICS`/`DURATION` in cell 4 to the request. The run outlasts one `wait_s`, so
when you get a handle, call `kaggle_wait_for_notebook`.

If the push warns that `ace-step-setup` is an invalid source, the cache notebook is gone —
create it (see bottom) and run this again.

### Cell 1 (code) — locate the cached repo + checkpoints

```python
import os, sys, time, shutil, subprocess
SETUP = "/kaggle/input/notebooks/h4kscom/ace-step-setup"
CKPT = os.path.join(SETUP, "ace_checkpoints")
REPO_SRC = os.path.join(SETUP, "ACE-Step")
print("setup mounted:", os.path.exists(SETUP), "| ckpt:", os.path.exists(CKPT))
for d in ["music_dcae_f8c8", "music_vocoder", "ace_step_transformer", "umt5-base"]:
    print(f"  {d}: {os.path.exists(os.path.join(CKPT, d))}")
```

### Cell 2 (code) — copy repo to writable /tmp, install, cuDNN fix

```python
REPO = "/tmp/ACE-Step"
if os.path.exists(REPO):
    shutil.rmtree(REPO)
shutil.copytree(REPO_SRC, REPO, ignore=shutil.ignore_patterns(".git"))
print("pip installing acestep...")
t0 = time.time()
r = subprocess.run([sys.executable, "-m", "pip", "install", REPO],
                   capture_output=True, text=True)
print("returncode", r.returncode, f"({time.time()-t0:.0f}s)")
if r.returncode != 0:
    print("STDERR", r.stderr[-1500:]); raise SystemExit("pip install failed")

import torch
print("torch", torch.__version__, "cuda", torch.version.cuda, "dev", torch.cuda.get_device_name(0))
# cuDNN's conv engine crashes on the bf16 Conv2d in the DCAE/vocoder on a T4;
# disabling it falls back to PyTorch's native conv kernel.
torch.backends.cudnn.enabled = False
```

### Cell 3 (code) — build the pipeline (bf16 fits the T4's 15 GB)

```python
from acestep.pipeline_ace_step import ACEStepPipeline
t0 = time.time()
model = ACEStepPipeline(checkpoint_dir=CKPT, dtype="bfloat16", torch_compile=False)
print(f"pipeline built in {time.time()-t0:.0f}s")
```

### Cell 4 (code) — generate

```python
# REQUIRED: replace PROMPT and LYRICS with the user's request. The values below are
# only an example of the shape — do NOT run them as-is. Empty LYRICS = instrumental.
PROMPT = "<genre, instruments, vocal type, mood — from the user's request>"
LYRICS = "[verse]\n<the user's lyrics, or write fitting ones>\n[chorus]\n<...>"
DURATION = 30.0

OUT = "/kaggle/working/song.wav"
t0 = time.time()
result = model(
    audio_duration=DURATION, prompt=PROMPT, lyrics=LYRICS,
    infer_step=40, guidance_scale=15.0, scheduler_type="euler", cfg_type="apg",
    omega_scale=10.0, manual_seeds="42", guidance_interval=0.5,
    guidance_interval_decay=0.0, min_guidance_scale=3.0,
    use_erg_tag=True, use_erg_lyric=True, use_erg_diffusion=True,
    oss_steps="", guidance_scale_text=0.0, guidance_scale_lyric=0.0,
    save_path=OUT,
)
print(f"generated in {time.time()-t0:.0f}s | result={result}")
```

### Cell 5 (code) — verify the wav

```python
import wave
out = result if (isinstance(result, str) and os.path.exists(result)) else OUT
if not os.path.exists(out):
    print("NO OUTPUT:", os.listdir('/kaggle/working')); raise SystemExit("no wav")
mb = os.path.getsize(out) / 1e6
try:
    with wave.open(out, 'rb') as w:
        sr, ch, n = w.getframerate(), w.getnchannels(), w.getnframes()
    print(f"WAV {out} {mb:.2f}MB {sr}Hz ch{ch} dur{n/sr:.1f}s")
except wave.Error as e:
    print(f"WAV {out} {mb:.2f}MB (header unread: {e})")
print("AUDIO_OK" if mb > 0.05 else "AUDIO_EMPTY")
print("=== DONE ===")
```

## Check it worked

The log must end with `AUDIO_OK` and `=== DONE ===`. Do NOT verify the wav with
`torchaudio.info` — that torchaudio version has no `.info` and it was the one bug that made
this look broken while the wav was fine. Use the `wave` check above.

## Deliver

The artifact is `song.wav`. Call `kaggle_notebook_output(ref, share="song.wav")` — it
returns an `s.h4ks.com` link. Give the user that link to listen/download.

## One-time setup notebook (only if `ace-step-setup` is missing)

Push with `title="ace-step-setup"`, `gpu=false`, `internet=true`, `timeout_s=1800`, cells:

```python
import os, subprocess, sys
os.chdir("/kaggle/working")
subprocess.run(["git", "clone", "--depth", "1", "https://github.com/ace-step/ACE-Step.git"], check=True)
subprocess.run([sys.executable, "-m", "pip", "install", "-q", "./ACE-Step"], check=True)
from huggingface_hub import snapshot_download
snapshot_download("ACE-Step/ACE-Step-v1-3.5B", local_dir="/kaggle/working/ace_checkpoints")
total = sum(os.path.getsize(os.path.join(r, f))
            for r, _, fs in os.walk("/kaggle/working/ace_checkpoints") for f in fs)
print(f"checkpoints {total/1e9:.2f} GB")
for d in ["music_dcae_f8c8", "music_vocoder", "ace_step_transformer", "umt5-base"]:
    print(d, os.path.isdir(f"/kaggle/working/ace_checkpoints/{d}"))
print("=== DONE ===")
```

Wait for it to complete before running the generation notebook.
