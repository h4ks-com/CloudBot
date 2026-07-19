---
name: hunyuan3d-texture
description: Turn a single image into a textured 3D model (GLB) using Hunyuan3D-2 on Kaggle GPU, and give the user a link to view it.
---

# 3D model from an image (Hunyuan3D-2)

Generates a textured `.glb` from one input image: it builds the geometry, paints a
texture onto it, and exports a GLB with the texture baked in. Runs on Kaggle's free
GPU via `kaggle_run_notebook`. About 3 minutes of GPU compute plus ~5-8 minutes of
one-time setup (~10 minutes wall-clock).

Use this whenever someone wants a 3D model / 3D mesh / GLB generated from a picture.

The cells below are complete and verified. Use them AS-IS: do not research Hunyuan3D or
rewrite them. The only value you change is `INPUT_URL` in cell 2 — set it to the user's
image URL, or leave the demo default if they gave no image.

## Fixed notebook name

Always use the title **`hunyuan3d-2-turbo`**. This is a create-or-update slug: pushing
the same title makes a new version of the same notebook, so it stays findable.

Before running, check your notebook list (it is in your context, or call
`kaggle_list_notebooks`):
- If `hunyuan3d-2-turbo` already exists and its last run is COMPLETE and the user wants
  the SAME model that produced it, do NOT re-run — just re-share its output (see Deliver).
- To make a model from a DIFFERENT image, or if it has never run, push the notebook below
  (with `INPUT_URL` set to the wanted image). Same title = new version.

## Run it

Call `kaggle_run_notebook` with `title="hunyuan3d-2-turbo"`, `gpu=true`,
`internet=true`, `timeout_s=1800`, and the cells below in order (GPU is auto-assigned a
T4 — required, the current Kaggle torch has no P100 support). The run outlasts one
`wait_s`, so when the tool returns a handle, call `kaggle_wait_for_notebook` to block
until it finishes.

To use the user's own image instead of the demo, set `INPUT_URL` in cell 2 to a public
image URL (the notebook fetches it; `internet=true` is why it can).

### Cell 1 (code) — environment

```python
import os, shutil, subprocess, sys, importlib, time, json

def run(cmd):
    print(f'$ {cmd}')
    return subprocess.run(cmd, shell=True).returncode

subprocess.run(['nvidia-smi', '--query-gpu=name,memory.total', '--format=csv,noheader'])
import numpy, torch, torchvision, scipy
print(f'devices={torch.cuda.device_count()} torch {torch.__version__} numpy {numpy.__version__}')

# Pin the pre-installed torch/torchvision so the pip installs below don't upgrade
# them; numpy must stay 2.0.2 (pre-installed scipy breaks on 2.4.x).
with open('/tmp/constraints.txt', 'w') as f:
    f.write('numpy==2.0.2\n')
    f.write(f'torch=={torch.__version__}\n')
    f.write(f'torchvision=={torchvision.__version__}\n')
run('pip install --no-cache-dir -c /tmp/constraints.txt trimesh pymeshlab xatlas rembg '
    'onnxruntime ninja pybind11 opencv-python einops omegaconf pygltflib diffusers '
    'transformers accelerate 2>&1 | tail -4')
run('pip install --no-cache-dir "numpy==2.0.2" --force-reinstall --no-deps 2>&1 | tail -2')
importlib.reload(numpy)
import scipy.spatial
cap = torch.cuda.get_device_capability()
print(f'GPU sm_{cap[0]}{cap[1]} cuda {torch.version.cuda}')
_ = (torch.zeros(4, device='cuda') + 1).sum().item()  # fails fast if the GPU arch is unsupported
print('env OK')
```

### Cell 2 (code) — clone, build the CUDA rasterizer, fetch the image

```python
# Everything heavy goes in /tmp so /kaggle/working only holds the final artifacts.
REPO = '/tmp/Hunyuan3D-2'
if not os.path.exists(REPO):
    run(f'git clone --depth 1 https://github.com/Tencent/Hunyuan3D-2.git {REPO}')
sys.path.insert(0, REPO)
CR = os.path.join(REPO, 'hy3dgen/texgen/custom_rasterizer')
os.chdir(CR); run('python setup.py install 2>&1 | tail -6'); os.chdir('/kaggle/working')
import custom_rasterizer
os.environ['HF_HOME'] = '/tmp/hf'
os.environ['HF_HUB_CACHE'] = '/tmp/hf'
os.environ['HY3DGEN_MODELS'] = '/tmp/hf'
import urllib.request
from PIL import Image
INPUT_URL = 'https://raw.githubusercontent.com/Tencent/Hunyuan3D-2/main/assets/demo.png'
# Browser UA: many image hosts (Wikimedia etc.) 403 urllib's default agent.
_req = urllib.request.Request(INPUT_URL, headers={'User-Agent': 'Mozilla/5.0'})
with urllib.request.urlopen(_req) as _r, open('/kaggle/working/input.png', 'wb') as _f:
    _f.write(_r.read())
print('build OK', Image.open('/kaggle/working/input.png').size)
```

### Cell 3 (code) — shape (turbo) then decimate

```python
from hy3dgen.rembg import BackgroundRemover
from hy3dgen.shapegen import Hunyuan3DDiTFlowMatchingPipeline
from PIL import Image

image = BackgroundRemover()(Image.open('/kaggle/working/input.png').convert('RGBA'))
image.save('/kaggle/working/input_rgba.png')

t = time.time()
sg = Hunyuan3DDiTFlowMatchingPipeline.from_pretrained(
    'tencent/Hunyuan3D-2', subfolder='hunyuan3d-dit-v2-0-turbo', use_safetensors=True)
sg.enable_flashvdm()
load_shape = time.time() - t

t = time.time()
mesh = sg(image=image, num_inference_steps=5, octree_resolution=300,
          num_chunks=200000, generator=torch.manual_seed(12345),
          output_type='trimesh')[0]
shape_t = time.time() - t
print(f'SHAPE load {load_shape:.0f}s gen {shape_t:.0f}s -> {len(mesh.vertices)} verts')
del sg; torch.cuda.empty_cache()

# Paint cost is O(faces) (xatlas UV-unwrap + CPU bake). The raw mesh is ~600k faces;
# decimating to 40k is what makes paint ~3 min instead of ~20 min, with no visible
# quality loss (the texture carries the detail).
from hy3dgen.shapegen import FloaterRemover, DegenerateFaceRemover, FaceReducer
t = time.time()
mesh = FloaterRemover()(mesh)
mesh = DegenerateFaceRemover()(mesh)
mesh = FaceReducer()(mesh, max_facenum=40000)
print(f'DECIMATE {time.time()-t:.0f}s -> {len(mesh.vertices)} verts {len(mesh.faces)} faces')
```

### Cell 4 (code) — paint (turbo), export, verify

```python
from hy3dgen.texgen import Hunyuan3DPaintPipeline

def load_paint(offload):
    tp = Hunyuan3DPaintPipeline.from_pretrained(
        'tencent/Hunyuan3D-2', subfolder='hunyuan3d-paint-v2-0-turbo')
    tp.config.render_size = 1024
    tp.config.texture_size = 1024
    tp.render.set_default_render_resolution(1024)
    tp.render.set_default_texture_resolution(1024)
    if offload:
        tp.enable_model_cpu_offload()
    return tp

t = time.time()
try:
    tp = load_paint(False); mode = 'gpu-resident'
    textured = tp(mesh, image=image)
except torch.cuda.OutOfMemoryError:
    torch.cuda.empty_cache()
    tp = load_paint(True); mode = 'cpu-offload'
    textured = tp(mesh, image=image)
paint_t = time.time() - t
print(f'PAINT {mode} {paint_t:.0f}s')

GLB = '/kaggle/working/turbo_output.glb'
textured.export(GLB)
print(f'exported {os.path.getsize(GLB)/1e6:.1f} MB, {len(textured.vertices)} verts')

# Ground truth: reload the GLB and confirm the texture survived the export.
import struct
data = open(GLB, 'rb').read()
clen = struct.unpack_from('<I', data, 12)[0]
g = json.loads(data[20:20 + clen])
imgs, texs, mats = len(g.get('images', [])), len(g.get('textures', [])), len(g.get('materials', []))
attrs = list(g.get('meshes', [{}])[0].get('primitives', [{}])[0].get('attributes', {}))
print(f'GLB images={imgs} textures={texs} materials={mats} attrs={attrs}')
print('TEXTURED:', 'YES' if imgs and texs and 'TEXCOORD_0' in attrs else 'NO')
print(f'TOTAL shape+paint {shape_t + paint_t:.0f}s')
print('=== DONE ===')
```

## Check it worked

The log must end with `TEXTURED: YES` and `=== DONE ===`. `complete` alone is not enough
(the notebook can finish with `TEXTURED: NO` if the export dropped the texture). If it
errored or says `NO`, read the log and fix before reporting success.

## Deliver

The artifact is `turbo_output.glb`. Call
`kaggle_notebook_output(ref, share="turbo_output.glb")` — it returns an `s.h4ks.com`
link. Give the user that link.

If you have the `web_app` tool, also build a viewer and return its URL — this is the nice
result ("let me see"). Use the HTML below, substituting the shared GLB link for `GLB_URL`.
Two gotchas baked in: the GLB must be loaded with `?download=true` (s.h4ks.com otherwise
returns an HTML redirect page), and the material metalness is forced to 0 with ACES
tonemapping (Hunyuan exports a metallic material that renders near-black otherwise).

```html
<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>3D model</title>
<style>html,body{margin:0;height:100%;background:#0a0d14;overflow:hidden}</style>
<script type="importmap">{"imports":{
"three":"https://unpkg.com/three@0.160.0/build/three.module.js",
"three/addons/":"https://unpkg.com/three@0.160.0/examples/jsm/"}}</script></head>
<body><script type="module">
import * as THREE from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
import {RoomEnvironment} from 'three/addons/environments/RoomEnvironment.js';
const MODEL='GLB_URL?download=true';
const r=new THREE.WebGLRenderer({antialias:true});
r.setPixelRatio(Math.min(devicePixelRatio,2));r.setSize(innerWidth,innerHeight);
r.outputColorSpace=THREE.SRGBColorSpace;r.toneMapping=THREE.ACESFilmicToneMapping;
r.toneMappingExposure=1.15;document.body.appendChild(r.domElement);
const s=new THREE.Scene();s.background=new THREE.Color(0x0a0d14);
const pm=new THREE.PMREMGenerator(r);s.environment=pm.fromScene(new RoomEnvironment(),0.04).texture;
const cam=new THREE.PerspectiveCamera(45,innerWidth/innerHeight,0.01,100);
const c=new OrbitControls(cam,r.domElement);c.enableDamping=true;c.autoRotate=true;
const k=new THREE.DirectionalLight(0xffffff,2.6);k.position.set(3,5,4);
s.add(k,new THREE.HemisphereLight(0xffffff,0x30343c,1.1));
new GLTFLoader().load(MODEL,g=>{const m=g.scene;
m.traverse(o=>{if(o.isMesh&&o.material){o.material.metalness=0;o.material.roughness=1;o.material.envMapIntensity=1.4;}});
const b=new THREE.Box3().setFromObject(m),sz=b.getSize(new THREE.Vector3()).length();
m.position.sub(b.getCenter(new THREE.Vector3()));s.add(m);
cam.position.set(0,sz*0.15,sz*0.9);cam.near=sz/100;cam.far=sz*10;cam.updateProjectionMatrix();
c.maxDistance=sz*4;c.update();});
addEventListener('resize',()=>{cam.aspect=innerWidth/innerHeight;cam.updateProjectionMatrix();r.setSize(innerWidth,innerHeight);});
r.setAnimationLoop(()=>{c.update();r.render(s,cam);});
</script></body></html>
```
