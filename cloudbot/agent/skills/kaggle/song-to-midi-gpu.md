---
name: song-to-midi-gpu
description: Transcribe a real recording into a multi-track MIDI on Kaggle's GPU, in roughly one to two times the length of the song. Use when the midifier service is down, backed up, or too slow; the song-to-midi skill is the normal path.
---

# Clone a real song into MIDI, on a Kaggle GPU

Runs the same midifier pipeline the service runs, on a Kaggle T4 instead of the self-hosted
card: overlapping segment decode, lane identity carried across the joins, lanes that were one
part under two names folded together, and the decoder's usual defects repaired.

Cost tracks the number of notes produced, not the length of the audio, so speed depends on how
busy the song is: a full band mix measured **1.0x realtime** and a dense solo piano piece
**2.1x**. Budget two times the song's length and expect to beat it. Even a dense six-minute
song lands near half the session cap, so `timeout_s=1800` is enough for anything under the
six-minute limit.

**Prefer `song-to-midi`.** That one posts to the midifier service and needs no GPU quota. Come
here when the service is unreachable, its queue is long, or its ETA is past your budget — and
say which, so the user knows why this path was taken.

**Check the library first.** `kinesthesia_search_midi(q="<song>")`. A human-made MIDI beats a
transcription and is instant.

## Two fixed notebooks

- **`midifier-setup`** — one-time weight cache. Downloads the gated MuScriptor `large` and
  `medium` weights into `/kaggle/working/hf`. Needs a Kaggle secret; see the bottom.
- **`midifier-transcribe`** — the run. Always mount the cache:
  `kernel_sources=["h4kscom/midifier-setup"]`.

Check your notebook list first. If `midifier-setup` is COMPLETE, go straight to transcribing.
Only rebuild it if it is missing or errored.

`kernel_sources` mounts the last **published** version, and publishing several GB lags the run
finishing. Cell 1 prints what it actually mounted; if a rebuild you just ran is missing from
that list, the previous version was mounted and you should re-push rather than trust it.

## Get the audio first, outside Kaggle

**Kaggle cannot reach YouTube reliably** — it intermittently answers `Sign in to confirm you're
not a bot` or a bare 403. So download on this side and pass a plain link in:

1. `ytdl_media_info(url=...)` — check `duration` is under 360 seconds.
2. `ytdl_download_media(url=..., mode="audio", format="mp3")` — returns a public link.

Put that link in `AUDIO_URL` in cell 4. It must be publicly fetchable; the notebook does a
plain HTTP GET with no credentials.

## Transcribe

Call `kaggle_run_notebook` with `title="midifier-transcribe"`, `gpu=true`, `internet=true`
(pip install and the audio fetch need the network; the weights are mounted), `timeout_s=1800`,
`kernel_sources=["h4kscom/midifier-setup"]`, and the cells below. The run outlasts one
`wait_s`, so when you get a handle call `kaggle_wait_for_notebook`.

Change **only** `AUDIO_URL` and `SONG` in cell 4. Everything else is verified as written.

### Cell 1 (code) — accelerator and mounted weights

```python
import os, sys, time, json, subprocess, urllib.request
import torch

p = torch.cuda.get_device_properties(0)
print("device:", p.name, f"{p.total_memory/1e9:.1f}GB", "sm", f"{p.major}.{p.minor}")
assert p.major >= 7, "Kaggle's torch needs sm_70+; a P100 lands here without machine_shape=NvidiaTeslaT4"

HF = "/kaggle/input/notebooks/h4kscom/midifier-setup/hf"
assert os.path.isdir(HF), f"setup output not mounted at {HF} -- pass kernel_sources=['h4kscom/midifier-setup']"
print("weights mounted:", sorted(os.listdir(os.path.join(HF, "hub"))))

# The mount is read-only and that is fine: hf_hub_download resolves straight out of it.
os.environ["HF_HOME"] = HF
os.environ["HF_HUB_OFFLINE"] = "1"
```

### Cell 2 (code) — install the model, keeping Kaggle's CUDA torch

```python
t0 = time.time()
r = subprocess.run([sys.executable, "-m", "pip", "install", "--no-deps", "muscriptor"],
                   capture_output=True, text=True)
print("muscriptor rc", r.returncode, f"({time.time()-t0:.0f}s)")
if r.returncode: print(r.stderr[-1500:]); raise RuntimeError("muscriptor install failed")

t0 = time.time()
deps = ["einops", "huggingface-hub", "mido", "numpy", "packaging", "safetensors", "soundfile",
        "typer", "pydantic-settings", "pretty-midi"]
r = subprocess.run([sys.executable, "-m", "pip", "install", *deps], capture_output=True, text=True)
print("deps rc", r.returncode, f"({time.time()-t0:.0f}s)")
if r.returncode: print(r.stderr[-1500:]); raise RuntimeError("dependency install failed")
print("torch still cuda:", torch.version.cuda)
```

`--no-deps` matters: muscriptor pulls its own torch, which would replace Kaggle's CUDA build
with a CPU one and drop the run to unusable speed.

### Cell 3 (code) — the pipeline itself

```python
t0 = time.time()
r = subprocess.run(["git", "clone", "--depth", "1",
                    "https://github.com/matheusfillipe/midifier.git", "/tmp/midifier"],
                   capture_output=True, text=True)
print("clone rc", r.returncode, f"({time.time()-t0:.0f}s)")
if r.returncode: print(r.stderr[-1000:]); raise RuntimeError("clone failed")

# An editable install writes a .pth hook that is only read at interpreter startup, so a
# running kernel never sees it. The package is pure Python; the path is what works.
sys.path.insert(0, "/tmp/midifier/src")
import midifier
print("midifier from", midifier.__file__)
```

### Cell 4 (code) — the audio

```python
# REQUIRED: replace with the link ytdl_download_media returned, and a name for the song.
AUDIO_URL = "<the public audio link>"
SONG = "transcription"

t0 = time.time()
req = urllib.request.Request(AUDIO_URL, headers={"User-Agent": "midifier"})
with urllib.request.urlopen(req, timeout=120) as resp, open("/tmp/input_audio", "wb") as fh:
    fh.write(resp.read())
print(f"downloaded {os.path.getsize('/tmp/input_audio')/1e6:.1f}MB in {time.time()-t0:.0f}s")

probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", "/tmp/input_audio"], capture_output=True, text=True)
DURATION = float(probe.stdout.strip())
print(f"duration {DURATION:.1f}s")
assert DURATION <= 400, f"{DURATION:.0f}s is too long; keep songs under ~6 minutes"
```

### Cell 5 (code) — transcribe

```python
os.environ["MIDIFIER_MODEL_SIZE"] = "large"
os.environ["MIDIFIER_DEVICE"] = "cuda"
os.environ["MIDIFIER_STORAGE_BACKEND"] = "local"

from pathlib import Path
from midifier.config import Settings
from midifier.transcribe import transcribe

settings = Settings()
print("model", settings.model_size, "| device", settings.device, "| segment", settings.segment_seconds)

start = time.time()
def progress(done, total):
    print(f"  segment {done}/{total}  (elapsed {time.time()-start:.0f}s)", flush=True)

result = transcribe(Path("/tmp/input_audio"), settings, progress=progress)
WALL = time.time() - start
print(f"\ntranscribed {DURATION:.0f}s of audio in {WALL:.0f}s -> {WALL/DURATION:.2f}x realtime")
```

Segment times vary a lot within one song — decode cost tracks the number of notes produced, so
a sparse intro is quick and a dense chorus is not. A segment taking three times its neighbour
is normal, not a hang. The first segment also carries the one-time read of the 5.5GB weights
off the mount, so it can run well over double the ones after it.

### Cell 6 (code) — write the MIDI

```python
notes = sum(t.note_count for t in result.tracks)
print(f"tracks {len(result.tracks)} | notes {notes}")
for t in result.tracks:
    print(f"  {t.name:32} program {t.program:3} drum {str(t.is_drum):5} notes {t.note_count}")
if result.dropped:
    print("folded lanes:", result.dropped)

out = f"/kaggle/working/{SONG}.mid"
with open(out, "wb") as fh:
    fh.write(result.midi)
print(f"wrote {out} {os.path.getsize(out)/1000:.1f}KB")
assert os.path.getsize(out) > 200, "MIDI is suspiciously small"
print("MIDI_OK")
print("=== DONE ===")
```

## Check it worked

The log must end with `MIDI_OK` and `=== DONE ===`.

Judge the track list against the recording, not against a number. One track is the right answer
for a solo piano piece and the wrong one for a full band, so the check is whether the parts
named match what the song actually has.

## Deliver

`kaggle_notebook_output(ref, share="<SONG>.mid")` returns an `s.h4ks.com` link. Then
`kinesthesia_import_project(url="<that link>", name="<song>")`, and hand back
`kinesthesia_player_link(...)` for the modes the user wants, plus the raw MIDI link.

Report the track list — it shows which instruments were heard, which is the interesting part.
If `folded lanes` appeared, mention it in a clause: those were one part the model renamed
partway through, and folding them is the result being tidier.

Be honest about quality. A dense mix transcribes imperfectly, there is no expression or
dynamics, and some notes are wrong. It is a real transcription of the real recording, not an
arrangement.

## One-time setup notebook (only if `midifier-setup` is missing)

The weights are gated, so this needs a Kaggle secret labelled exactly `HF_TOKEN`, holding a
token from an account that accepted the MuScriptor licence. **Secrets attach per notebook
through Add-ons -> Secrets in the web UI** — there is no API for it, so a human has to do this
once. Ask the user rather than trying to automate it.

Push with `title="midifier-setup"`, `gpu=false`, `internet=true`, `timeout_s=1800`:

```python
import os, time, shutil, subprocess, sys
from kaggle_secrets import UserSecretsClient
from huggingface_hub import snapshot_download, HfApi

HF_TOKEN = UserSecretsClient().get_secret("HF_TOKEN")
os.environ["HF_TOKEN"] = HF_TOKEN

# medium is cached alongside large because midifier drops to a smaller model when a decode
# keeps failing. With only large present that ladder cannot resolve offline and turns a
# recoverable failure into a confusing LocalEntryNotFoundError.
REPOS = ["MuScriptor/muscriptor-large", "MuScriptor/muscriptor-medium"]
STAGE = "/kaggle/working/_download"

for REPO in REPOS:
    t0 = time.time()
    snapshot_download(REPO, token=HF_TOKEN, local_dir=STAGE)
    sha = HfApi().model_info(REPO, token=HF_TOKEN).sha
    print(f"{REPO} downloaded in {time.time()-t0:.0f}s, commit {sha}")

    # A default cache points snapshots/ at blobs/ through symlinks, and Kaggle drops those
    # when publishing output: the mount arrives with blobs/ and refs/ but no snapshots/, and
    # nothing resolves. Real files in snapshots/<commit>/ survive the round trip.
    root = f"/kaggle/working/hf/hub/models--{REPO.replace('/', '--')}"
    os.makedirs(f"{root}/snapshots/{sha}", exist_ok=True)
    os.makedirs(f"{root}/refs", exist_ok=True)
    with open(f"{root}/refs/main", "w") as fh:
        fh.write(sha)
    for name in os.listdir(STAGE):
        src = os.path.join(STAGE, name)
        if os.path.isfile(src):
            os.replace(src, f"{root}/snapshots/{sha}/{name}")
    shutil.rmtree(STAGE, ignore_errors=True)

for REPO in REPOS:
    check = subprocess.run(
        [sys.executable, "-c",
         "import sys; from huggingface_hub import hf_hub_download;"
         "print(hf_hub_download(repo_id=sys.argv[1], filename='model.safetensors'))", REPO],
        capture_output=True, text=True,
        env={**os.environ, "HF_HOME": "/kaggle/working/hf", "HF_HUB_OFFLINE": "1"},
    )
    print(f"{REPO} offline resolve rc {check.returncode}")
    print(check.stdout or check.stderr[-800:])
print("=== DONE ===")
```

The offline check at the end is the point: a cache that cannot resolve fails here, on free CPU,
instead of after burning GPU quota.

Wait for it to complete before transcribing.
