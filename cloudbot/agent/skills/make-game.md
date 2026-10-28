---
name: make-game
description: Build a playable browser game with a plot, characters, bosses, sound and music, published as one HTML page. THE DEFAULT for ANY request to make, create or build a game, even a short or joking one ("make a game where X fights Y", "game with my character", "a game about the channel"). It also covers using 3D characters from workflows.h4ks.com jobs (rigged animated GLBs in a public bucket) or Sketchfab models in three.js, so read it whenever a game or interactive 3D scene uses GLB models.
---

# Make a game

A good game here is short, funny and finished: a title screen with a plot, a few chapters that
escalate, a boss or two, an ending. People share these in the channel, so it has to load, look
right and play on the first try. The steps below come from a game that people liked; keep its
structure and change the story, the cast and the attacks.

## 1. Gather the cast

- **workflows.h4ks.com job links** (`https://workflows.h4ks.com/jobs/<id>`): call
  `workflows_get_job(<id>)` for each. A finished `character-3d` job has a `.glb` in
  `result.files`: a rigged humanoid with animation clips that play in place. The URLs live in
  the public bucket `https://s3-api.t3ks.com/workflows/...`, which answers CORS for every
  origin, so the page loads them directly from anywhere. Never download or re-host them, and
  never `web_fetch` a GLB.
- **Check each job is the character the user named.** Users paste the wrong link. Compare the
  job's prompt with the role they gave it; when it does not match, say so, and look through
  `workflows_list_jobs(user=...)` for the right one (same kind of description). Pick the closest
  and tell the user which job you used.
- **Read each job's clip names** from `details` or the job's moves. They are the character's
  personality: a character with "Judo Throw" and "Hockey Slap Shot" gets those as boss attacks,
  one with "Lecture Point" gets them as dialogue gestures. Characters with only a few clips make
  good minions or NPCs; ones with the 15 basic moves (Idle, Walk, Run, Jump Start/Air/Land, Roll,
  Punch, Cast Spell, Hit, Death, Dance...) make good players and bosses.
- **No characters given?** Offer the `character-3d` job (see that skill) for the hero, and fill
  props and scenery with `sketchfab_search` and `sketchfab_download`, or simple three.js shapes.
  A 2D game needs no models at all: draw with canvas.

## 2. Write the plot

Give every character a role that plays on who they are, and give the story a stake, a villain's
absurd plan and a punchline ending. Public figures can be satirized through their public persona;
keep it playful, never hateful, and never mock a private person's body or identity. Name the
chapters, write 3 to 6 short dialogue lines per scene, and tie each line to a clip ("Emphatic
Speaking" on a rant). Put the plot on the title screen in two short paragraphs.

## 3. Build it

One self-contained HTML page, three.js from a CDN with an import map. Start from the pieces
below; they are tested together with real workflows characters.

### Load and place characters

```js
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import * as SkeletonUtils from 'three/addons/utils/SkeletonUtils.js';
import { MeshoptDecoder } from 'three/addons/libs/meshopt_decoder.module.js';
```

- Every workflows GLB uses EXT_meshopt_compression: `new GLTFLoader().setMeshoptDecoder(MeshoptDecoder)`
  or loading fails.
- Load each model once (show a progress bar), then make each on-screen copy with
  `SkeletonUtils.clone(gltf.scene)`, so ten enemies share one download and still animate apart.
- **Measure the original, never the clone.** A fresh clone has no world matrices, and these
  meshes are quantized, so `Box3.setFromObject(clone)` returns a size near zero and the
  character ends up scaled 100000x and invisible. Measure `gltf.scene` once after
  `updateMatrixWorld(true)` and reuse that box.
- Wrap the clone in a `THREE.Group` that the game moves and turns; scale the clone to a height
  per role (hero 1.8, minions 1.2, bosses 2.6 so they loom) and lift it so its feet touch y=0.
- Set `castShadow` and `frustumCulled = false` on the meshes (skinned meshes get culled wrongly).
- The models face +Z: `group.rotation.y = Math.atan2(dx, dz)` faces a target.

```js
function makeCharacter(name) {
  const g = gltfs[name];
  if (!g.box) { g.scene.updateMatrixWorld(true); g.box = new THREE.Box3().setFromObject(g.scene); }
  const box = g.box, model = SkeletonUtils.clone(g.scene);
  model.traverse(o => { if (o.isMesh) { o.castShadow = true; o.frustumCulled = false; } });
  const s = HEIGHTS[name] / box.getSize(new THREE.Vector3()).y, c = box.getCenter(new THREE.Vector3());
  model.scale.setScalar(s);
  model.position.set(-c.x * s, -box.min.y * s, -c.z * s);
  const root = new THREE.Group(); root.add(model); scene.add(root);
  return { root, anim: new Anim(model, g.animations), height: HEIGHTS[name] };
}
```

### Animate with a one-shot lock

Looping clips (Idle, Run) must not cut off a one-shot (an attack, Hit, Jump Start). `busy`
blocks loops until the one-shot finishes:

```js
class Anim {
  constructor(model, clips) {
    this.mixer = new THREE.AnimationMixer(model); this.actions = {};
    for (const c of clips) this.actions[c.name] = this.mixer.clipAction(c);
    this.cur = null; this.busy = false; this.done = null;
    this.mixer.addEventListener('finished', e => {
      if (e.action !== this.cur) return;
      this.busy = false; const cb = this.done; this.done = null; cb && cb();
    });
  }
  loop(name, speed = 1, fade = .2) {
    const a = this.actions[name];
    if (this.busy || !a) return;
    a.timeScale = speed;
    if (a === this.cur) return;
    a.reset().setLoop(THREE.LoopRepeat).fadeIn(fade).play();
    if (this.cur) this.cur.fadeOut(fade);
    this.cur = a;
  }
  once(name, done, speed = 1, fade = .12) {
    const a = this.actions[name];
    if (!a) { done && done(); return; }
    a.reset(); a.setLoop(THREE.LoopOnce, 1); a.clampWhenFinished = true; a.timeScale = speed;
    a.fadeIn(fade).play();
    if (this.cur && this.cur !== a) this.cur.fadeOut(fade);
    this.cur = a; this.busy = true; this.done = done;
  }
  release() { this.busy = false; this.done = null; }
}
```

Land damage partway through an attack clip with a game-clock timer (`after(.55, ...)`) and check
the distance again then, so a dodge works.

### Camera and focus, like a real game

Players expect the mouse to steer the camera. Lock the pointer when they press Start, and pause
the whole game while it is unlocked:

- The Start button calls `canvas.requestPointerLock()`.
- On `pointerlockchange`, while the game runs and the pointer is unlocked, show a full-screen
  "CLICK TO PLAY" overlay (ESC to pause), freeze the simulation (`dt = 0`) and
  `audioContext.suspend()`; clicking the overlay locks again and resumes.
- A locked pointer sends every click to the canvas, so the canvas mousedown advances dialogue
  when a line is showing and attacks otherwise. Enter also advances.
- `mousemove` turns the camera: `yaw -= e.movementX * .0035`. Q/E turn too.
- Move relative to the camera: `fwd = (sin yaw, 0, cos yaw)`, `right = (-cos yaw, 0, sin yaw)`;
  turn the character smoothly toward its movement. The camera sits 7 behind and 3.4 up, lerps
  there, and looks 2 ahead of the hero at chest height.
- Keep everyone inside the arena and outside solid props with a radius clamp.

### Story runner

Write chapters as async functions and drive them from the game loop, so pausing pauses the story:
`after(sec, fn)` and `until(cond)` resolve inside the loop with the game's `dt`, and `sleep` is
`after` wrapped in a promise. `until` rejects with `'dead'` when the hero dies; the runner
catches it, shows "try again", resets the hero and enemies, and replays that chapter instead of
the whole game.

Dialogue: a bottom box with the speaker's name, a typewriter effect, a soft blip per few letters;
the first click finishes the line, the next one moves on. Each line plays its speaker's clip and
turns them toward the hero. Freeze enemies while anyone talks.

Pacing that worked: a tutorial lecture that teaches the controls in character, two waves of
minions with a heal between them, a boss whose attack cycle is built from its own clips (melee
when close, a ranged volley, a telegraphed charge the player can roll through), a second boss
that summons minions and fires text projectiles of its catchphrases, then a rescue and a dance
party ending with every character on `Dance`.

### Make it feel good

- Projectiles the hero throws should match the theme (glowing shapes with a label sprite).
- Burst particles, floating damage text, a red screen flash when hurt, a shockwave ring for the
  special move, a boss health bar with the boss's title, a banner per chapter.
- A charged special (fills on hits, one key) gives the player a power spike. Show its key as a
  badge beside the meter that lights up and pulses with "READY" when charged, so the player
  knows to press it.
- Minions die with a squash or pop, not by vanishing.

### Sound: synthesize it

Build all sound with the Web Audio API in the page, created on the Start click (browsers block
audio before a gesture). Route everything through a compressor, give it a shared reverb from a
generated impulse (2 s of decaying noise in a ConvolverNode) and build effects from two helpers:
`tone` (oscillator, optional pitch glide, detuned twin, sweeping lowpass, envelope) and `noise`
(looped noise buffer through a sweeping filter). Each effect layers 2 to 4 of them:

- throw: rising detuned saw through a filter opening from 600 Hz to 7 kHz, plus a quick sparkle
  chord and a hiss
- crack or impact: short bandpassed noise snap plus a sine thud gliding from 180 to 45 Hz
- hurt: falling detuned saw with a closing filter; heal: a bell arpeggio with lots of reverb
- boss entrance: low detuned saw chord opening slowly, over a long lowpassed noise rumble
- special move: noise riser sweeping up, then a deep sine boom

Add music with a small lookahead sequencer (a `setInterval` that schedules 16th notes ahead of
`currentTime`): a four-chord minor loop with a square arpeggio, a saw bass, a sine kick, a noise
snare and hats, on its own gain bus. Switch it to a faster boss variation with doubled kicks
during fights, and let M mute it.

## 4. Publish and check

1. `web_app` with the full HTML. Use `vibegame_upload` only when the user asks to save it on
   games.h4ks.com.
2. Open it with `browser_console` once. Log `models loaded` with each character's clip names after
   loading; any `[ERROR]` or a missing log means it is broken. Common causes: the meshopt decoder
   is missing, a URL is wrong, or a clip name is misspelled (look it up in the logged names).
3. Take one `browser_screenshot` after pressing Start and look at it: the hero and the NPCs must
   be visible and standing on the ground. Invisible characters almost always mean the model was
   measured after cloning. Fix the root cause and redeploy, at most twice.
4. Reply with the link, the plot in one line, and the controls.
