---
name: itplay-train
description: Continue an itplay game-training experiment on Kaggle's GPU. Use when asked to train a game faster, or to train an itplay experiment somewhere better than the homelab. The homelab manages ~90 steps/s on CPU; a Kaggle T4 is far quicker.
---

# Train an itplay experiment on Kaggle GPU

itplay trains reinforcement-learning policies for retro games and streams them live.
It runs on homelab CPU at roughly 90 steps a second, so a million steps is a few
hours there. This moves one experiment onto a Kaggle T4 for the heavy part.

The cells below are complete and verified. Use them AS-IS: do not research
stable-baselines3 or ALE, do not rewrite them. The ONLY thing you change is
the experiment name inside `BUNDLE_URL` in cell 1.

## What itplay hands over

Every experiment is fetchable at a plain, public URL:

    https://itplay.t3ks.com/experiments/<experiment>/bundle

It holds the experiment's declaration and whatever weights it has already reached
— **data only, no code**. It is read-only and needs no key, so the notebook just
downloads it.

You do not need a tool for this: build the URL from the experiment name. To see
what experiments exist, fetch `https://itplay.t3ks.com/experiments` or ask the
main agent, which has the itplay tools.

## Running it

Call `kaggle_run_notebook` with:

- `title="itplay-train-<experiment>"` — one notebook per experiment. Do NOT use a
  single fixed title: two experiments would collide and the second would read the
  first's output.
- `gpu=true`, `internet=true` (it pip-installs and fetches the bundle)
- `timeout_s=1500`

The run outlasts one `wait_s`, so when you get a handle call
`kaggle_wait_for_notebook`.

### Cell 1 (code) — fetch the experiment

REQUIRED: replace `NAME` with the experiment you were asked to train. The value
below is a placeholder and trains the wrong thing.

```python
BUNDLE_URL = "https://itplay.t3ks.com/experiments/NAME/bundle"

import json, os, tarfile, urllib.request, glob
os.makedirs("/kaggle/working/experiment", exist_ok=True)
urllib.request.urlretrieve(BUNDLE_URL, "/kaggle/working/bundle.tar.gz")
with tarfile.open("/kaggle/working/bundle.tar.gz") as archive:
    archive.extractall("/kaggle/working/experiment")

spec_path = glob.glob("/kaggle/working/experiment/*/spec.json")[0]
HOME = os.path.dirname(spec_path)
SPEC = json.load(open(spec_path))
print("experiment:", SPEC["name"], "| game:", SPEC["game"], "| algo:", SPEC["algo"])
print("resuming from weights:", os.path.exists(os.path.join(HOME, "policy.zip")))
```

### Cell 2 (code) — install what trains it

```python
!pip -q install "stable-baselines3[extra]==2.9.0" "ale-py==0.12.1" "gymnasium==1.3.0" 2>&1 | tail -2
import torch
print("cuda:", torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else "")
```

### Cell 3 (code) — train

`STEPS` is how much further to train in this sitting. A T4 does roughly 1000 steps a
second here, so 1,000,000 is about twenty minutes. Keep it inside the notebook's
timeout.

```python
STEPS = 1_000_000

import os, ale_py, gymnasium as gym
gym.register_envs(ale_py)
from stable_baselines3 import A2C, PPO
from stable_baselines3.common.env_util import make_atari_env
from stable_baselines3.common.vec_env import VecFrameStack, SubprocVecEnv

BUILDER = {"ppo": PPO, "a2c": A2C}[SPEC["algo"]]
WEIGHTS = os.path.join(HOME, "policy.zip")

venv = VecFrameStack(
    make_atari_env(SPEC["game"], n_envs=8, seed=0, vec_env_cls=SubprocVecEnv), n_stack=4
)
model = BUILDER.load(WEIGHTS, env=venv, device="cuda") if os.path.exists(WEIGHTS) \
    else BUILDER("CnnPolicy", venv, device="cuda", verbose=0)

model.learn(total_timesteps=STEPS, reset_num_timesteps=False, progress_bar=False)
model.save("/kaggle/working/policy.zip")
print("saved /kaggle/working/policy.zip")
```

### Cell 4 (code) — say how good it got

```python
from stable_baselines3.common.evaluation import evaluate_policy
mean, std = evaluate_policy(model, venv, n_eval_episodes=10)
print(f"mean reward over 10 episodes: {mean:.1f} +/- {std:.1f}")
venv.close()
```

## Delivering the result

Report the mean reward and the notebook output link. The trained `policy.zip` is in
the notebook output — itplay does **not** accept uploads, so a human puts the
weights back if they want them. Say that rather than implying it synced.

Do not invent an itplay tool for uploading weights. There isn't one.
