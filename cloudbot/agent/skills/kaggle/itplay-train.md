---
name: itplay-train
description: Train an itplay game-training experiment on Kaggle's two T4 GPUs and put the result on the livestream. Use when asked to train a game, to train one faster, to improve an experiment, or to teach a game that has nothing trained on it yet.
---

# Train an itplay experiment on Kaggle

itplay trains policies for retro games and streams them live. The homelab does
about 64 steps a second; a Kaggle T4 does about 313, and there are two.

**Use the four cells below verbatim.** Only `NAME` in cell 1 and `VARIANTS` in
cell 4 are yours to change. The trainer itself is downloaded from itplay rather
than written out here, because the two things that matter are easy to get wrong
and both waste the whole session: it must save **safetensors** (itplay refuses
pickles, so `model.save(...)` throws the run away) and it must train **against
the clock** (the session is killed at a fixed wall time, so a step count is a
guess that has never once been right).

**This takes two invocations, and that is on purpose.** Training runs for half an
hour and you do not get half an hour: waiting on the notebook guarantees a
timeout. So decide which half you are doing before anything else.

Check with `kaggle_notebook_output` for `itplay-train-<experiment>`:

- **it errors, or there is no such notebook** — you are launching. Do steps 1 and
  2, say which variants you raced and that the run has started, and stop. Do not
  wait for it.
- **it returns files** — the run is over, however it ended. Skip to step 3.

Never call `kaggle_wait_for_notebook` for this. The plugin announces the run in
the channel when it finishes, which is the signal to come back and do step 3.

Nothing here needs arithmetic: the trainer stops itself on the clock and saves
after every chunk. **A cancelled session and a finished one are handled the same
way** — the candidates exist either way.

## 1. The experiment

`itplay_experiments` lists what exists. If the one you were asked for is not
there, create it with `itplay_new_experiment`: a name of lowercase letters and
dashes, the ALE game id such as `ALE/Kaboom-v5`, and `algo` of `ppo`.

Then call `itplay_bundle` for it. That answers with two links, and **they are
the only place these addresses come from** — do not write a host into the
notebook from memory, and do not guess one:

- `url` — the bundle: the declaration and whatever weights it has reached
- `trainer` — the training script to run against it

Both are public and read-only, so the notebook sends no key.

## 2. Run the notebook

`kaggle_run_notebook` with `title="itplay-train-<experiment>"`, `gpu=true`,
`internet=true`, `timeout_s=1800`. The session is capped at 1800 seconds however
much is asked for, which is why the trainer works to a deadline instead of a step
count.

Then **stop and report that it started**. Do not wait for it.

### Cell 1 — the experiment

Replace `NAME`.

Put the `url` from `itplay_bundle` in `BUNDLE_URL`.

```python
BUNDLE_URL = "<the url itplay_bundle gave you>"

import json, os, tarfile, urllib.request, glob
os.makedirs("/kaggle/working/experiment", exist_ok=True)
urllib.request.urlretrieve(BUNDLE_URL, "/kaggle/working/bundle.tar.gz")
with tarfile.open("/kaggle/working/bundle.tar.gz") as archive:
    archive.extractall("/kaggle/working/experiment")

spec_path = glob.glob("/kaggle/working/experiment/*/spec.json")[0]
HOME = os.path.dirname(spec_path)
SPEC = json.load(open(spec_path))
RESUME = os.path.join(HOME, "policy.zip")
print("experiment:", SPEC["name"], "| game:", SPEC["game"], "| algo:", SPEC["algo"])
print("resuming from weights:", os.path.exists(RESUME))
```

### Cell 2 — install

```python
!pip -q install "stable-baselines3[extra]==2.9.0" "ale-py==0.12.1" "gymnasium==1.3.0" safetensors 2>&1 | tail -1
import torch
print("gpus:", torch.cuda.device_count())
```

### Cell 3 — the trainer

Downloaded, never written. This is the script that saves the format itplay
accepts and stops before the session is killed, and both matter. Use the
`trainer` link from `itplay_bundle`.

```python
TRAINER_URL = "<the trainer link itplay_bundle gave you>"

import urllib.request
urllib.request.urlretrieve(TRAINER_URL, "/kaggle/working/train_one.py")
print(open("/kaggle/working/train_one.py").read()[:200])
```

### Cell 4 — both GPUs

Two GPUs means two independent runs, not one run twice as fast. Race two seeds,
two learning rates (2.5e-4 is the usual starting point), or ppo against a2c. Each
drops to about 206 steps/s because four CPUs step the emulator for both.

```python
VARIANTS = [
    {"seed": 1, "learning_rate": 2.5e-4},
    {"seed": 2, "learning_rate": 1e-4},
]

import subprocess, os, json

procs = []
for gpu, variant in enumerate(VARIANTS):
    out = f"/kaggle/working/candidate{gpu}.safetensors"
    cmd = ["python", "/kaggle/working/train_one.py",
           "--game", SPEC["game"], "--algo", SPEC["algo"], "--seconds", "1400",
           "--seed", str(variant["seed"]), "--learning-rate", str(variant["learning_rate"]),
           "--out", out, "--device", "cuda", "--envs", "8"]
    if os.path.exists(RESUME):
        cmd += ["--resume", RESUME]
    env = {**os.environ, "CUDA_VISIBLE_DEVICES": str(gpu)}
    # Inherited, not captured: piping means every RESULT line sits in the parent
    # until it finishes, and a cancelled session finishes never.
    procs.append((out, subprocess.Popen(cmd, env=env)))

for _out, proc in procs:
    proc.wait()

for out, _proc in procs:
    if os.path.exists(out + ".json"):
        print("trained:", json.load(open(out + ".json")))
```

## 3. Send it home

Do this however the run ended.

1. `kaggle_notebook_output(ref, share="candidate0.safetensors")` returns an
   `s.h4ks.com` link. Same again for `candidate1.safetensors`.
2. Add `?download=true` to each link, or s.h4ks.com serves an HTML page and
   itplay refuses it for not being safetensors.
3. `itplay_submit_weights` once per candidate, with the experiment name and the
   link. Submit both and leave `episodes` alone: itplay plays them against what
   it already holds, on the same seeded episodes, and keeps whichever is better.

## 4. Put it on the stream

`itplay_atari` with `options` of `{"experiment": "<name>"}` plays what the
experiment now holds. Give back the `watch` url from the answer.

## Report

itplay's verdicts with their scored and beat numbers, which variants you raced,
and the watch url. Never report the notebook's numbers: it does not measure
anything.

A refusal is a real result. It means the sitting did not beat what was there, so
the next one should change something rather than repeat itself.
