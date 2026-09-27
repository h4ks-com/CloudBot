---
name: workflows
description: Run paid jobs on workflows.h4ks.com (songs, covers, voices, images, sprite sheets, 3D models, animated 3D characters, karaoke, podcasts, parodies, MIDI) and build on their results. Use it for ANY request that makes media for a user, and for anything that reuses a finished job's files, like "show that model", "play that song in a page", "make a game with my character", "put the sprite sheet in a game", "what did job 87 make". Read it before touching a workflows result, even when the request looks simple.
---

# workflows.h4ks.com

workflows.h4ks.com sells generation jobs for credits. The user's credits pay for them, one job
runs at a time on the homelab GPU, and the bot announces each job's start and result in the
channel by itself, so you never poll or wait for a job.

## Run a job

1. Check for a job skill first (`character-3d`, `song-to-midi`); it knows that job's fields.
2. Otherwise call `workflows_list_job_types` and read the job's params schema. Pass the user's
   links (songs, pictures, videos) straight into the params: the job downloads and reads them
   itself, so never download, describe or research them first. That only burns time.
3. `workflows_submit_job(type, params)`. It submits as the asker when their IRC account is
   linked, and otherwise returns a filled form link. Give the user the job link and price, or the
   form link with the reason it returned.

Users can also cover or continue what just played on the radio with `.wf cover [seconds] [style]`
and `.wf continue [seconds] [idea]`; point them there for radio requests.

## Find a finished result

`workflows_get_job(job_id)` or `workflows_list_jobs(user=...)`. A finished job's `result.files`
lists each file's `url`, `name`, `mime` and, on newer jobs, `details`: what the file holds and
how to use it (clip names, sizes, frame layout, tracks). Trust `details` over guessing.

The files live in a public bucket at https://s3-api.t3ks.com/workflows/... and web pages on
s.h4ks.com (what `web_app` publishes) may load them directly. Use the URL as it is.

Never `web_fetch` a result file. GLB, WAV, MP4, PNG and MIDI are binary, the fetch fails or
returns noise, and the metadata `.json` beside them only repeats the title and URL. Everything
you need is in `get_job`.

## What each result is

- **character.glb** (character-3d): a rigged humanoid, one skinned mesh with a baked texture,
  22 VRoid bones, animation clips that play in place (the game moves the character). Clip names
  come from the job's basic moves (Idle, Walk, Run, Sprint, Jump Start, Jump Air, Jump Land,
  Crouch Walk, Roll, Punch, Sword Attack, Cast Spell, Shoot, Hit, Death, Dance, Sit) plus its AI
  moves; older jobs name two of them `Spell_Simple_Shoot` and `Pistol_Shoot`.
  Read the real names from `gltf.animations` at runtime.
- **model.glb** (model-3d): a static textured model, no rig.
- Every GLB is compressed with EXT_meshopt_compression, so three.js must call
  `loader.setMeshoptDecoder(MeshoptDecoder)` before loading or it fails with "setMeshoptDecoder
  must be called before loading compressed files".
- **Songs, covers, voices, parodies, podcasts**: WAV audio; play them with an `<audio>` element
  or `new Audio(url)`.
- **Images and sprite sheets**: PNG. A sprite sheet holds its frames side by side in one row;
  `details` gives the frame count and size.
- **MIDI**: a .mid file with a kinesthesia player link in the job's `links`.
- **Karaoke**: an MP4 with the lyrics burned in.

## Build a page or a game on a result

Use `web_app` with one self-contained HTML page. For anything with a GLB, start from the core
below, which is tested with a real character: it loads the GLB from the bucket, puts the
character on a lit ground at 1.8 m, crossfades between clips, and runs a third-person game with
WASD or arrows to walk, Shift to run and Space to jump. Keep what it already does and build the
request on top of it: a viewer adds buttons over `Object.keys(clips)`; a game adds goals,
obstacles, pickups, enemies, a score and sound. Make games feel like games, not demos.

Then open the URL with `browser_console` once and read it honestly: the page logs
`model loaded` with the clip names when the GLB loads, and any `[ERROR]` line or missing
`model loaded` means it is broken. Fix the root cause and redeploy at most twice. Tell the user
the controls with the link.

```html
<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>html,body{margin:0;height:100%;overflow:hidden;font-family:sans-serif}#hud{position:fixed;left:12px;top:12px;color:#fff;text-shadow:0 1px 2px #000}</style></head><body>
<div id="hud">WASD or arrows to move, Shift to run, Space to jump</div>
<script type="importmap">{"imports":{"three":"https://cdn.jsdelivr.net/npm/three@0.170.0/build/three.module.js","three/addons/":"https://cdn.jsdelivr.net/npm/three@0.170.0/examples/jsm/"}}</script>
<script type="module">
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { MeshoptDecoder } from "three/addons/libs/meshopt_decoder.module.js";

const GLB = "https://s3-api.t3ks.com/workflows/character/....glb"; // the job's character.glb url
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x87a8c8);
scene.fog = new THREE.Fog(0x87a8c8, 20, 60);
const camera = new THREE.PerspectiveCamera(50, innerWidth / innerHeight, 0.05, 200);
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setSize(innerWidth, innerHeight);
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.shadowMap.enabled = true;
document.body.appendChild(renderer.domElement);
scene.add(new THREE.HemisphereLight(0xffffff, 0x556644, 2));
const sun = new THREE.DirectionalLight(0xffffff, 2.5);
sun.position.set(5, 10, 4);
sun.castShadow = true;
scene.add(sun);
const ground = new THREE.Mesh(new THREE.PlaneGeometry(200, 200), new THREE.MeshStandardMaterial({ color: 0x5a7d4a }));
ground.rotation.x = -Math.PI / 2;
ground.receiveShadow = true;
scene.add(ground);

const loader = new GLTFLoader();
loader.setMeshoptDecoder(MeshoptDecoder);
const clock = new THREE.Clock();
const keys = {};
addEventListener("keydown", (event) => { keys[event.code] = true; });
addEventListener("keyup", (event) => { keys[event.code] = false; });
let player, mixer, height = 1.8, clips = {}, current, action;

function play(name, fade = 0.2) {
  const clip = clips[name];
  if (!clip || current === name) return;
  const next = mixer.clipAction(clip).reset().play();
  if (action) next.crossFadeFrom(action, fade, false);
  action = next;
  current = name;
}

loader.load(GLB, (gltf) => {
  const model = gltf.scene;
  model.traverse((node) => { if (node.isMesh) node.castShadow = true; });
  const box = new THREE.Box3().setFromObject(model);
  model.scale.setScalar(height / (box.max.y - box.min.y));
  model.position.y = -box.min.y * model.scale.y;
  player = new THREE.Group();
  player.add(model);
  scene.add(player);
  mixer = new THREE.AnimationMixer(model);
  for (const clip of gltf.animations) clips[clip.name] = clip;
  play(clips.Idle ? "Idle" : gltf.animations[0]?.name);
  console.log("model loaded", Object.keys(clips));
}, undefined, (error) => { document.getElementById("hud").textContent = "could not load the model: " + error.message; console.error(error); });

const up = new THREE.Vector3(0, 1, 0);
const velocity = new THREE.Vector3();
let jumping = false, vertical = 0;
renderer.setAnimationLoop(() => {
  const delta = Math.min(clock.getDelta(), 0.05);
  if (player) {
    const forward = (keys.KeyW || keys.ArrowUp ? 1 : 0) - (keys.KeyS || keys.ArrowDown ? 1 : 0);
    const turn = (keys.KeyA || keys.ArrowLeft ? 1 : 0) - (keys.KeyD || keys.ArrowRight ? 1 : 0);
    const running = keys.ShiftLeft || keys.ShiftRight;
    player.rotation.y += turn * 2.5 * delta;
    velocity.set(0, 0, forward * (running ? 5 : 2)).applyAxisAngle(up, player.rotation.y);
    player.position.addScaledVector(velocity, delta);
    if (keys.Space && !jumping) { jumping = true; vertical = 5; }
    if (jumping) {
      vertical -= 12 * delta;
      player.position.y = Math.max(0, player.position.y + vertical * delta);
      if (player.position.y === 0 && vertical < 0) jumping = false;
    }
    if (jumping) play(clips["Jump Air"] ? "Jump Air" : "Idle", 0.1);
    else if (forward) play(running && clips.Run ? "Run" : "Walk");
    else play("Idle");
    const behind = new THREE.Vector3(0, height * 1.1, -height * 2.6).applyAxisAngle(up, player.rotation.y);
    camera.position.lerp(player.position.clone().add(behind), 0.1);
    camera.lookAt(player.position.x, player.position.y + height * 0.6, player.position.z);
  }
  if (mixer) mixer.update(delta);
  renderer.render(scene, camera);
});
addEventListener("resize", () => {
  camera.aspect = innerWidth / innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight);
});
</script></body></html>
```
