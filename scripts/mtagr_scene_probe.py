# Mogwai probe: load a scene, path trace N accumulated frames at 1080p, capture color, albedo,
# normals, motion vectors and depth (EXR), print scene stats, exit.
#   set PROBE_SCENE=<file> & Mogwai.exe --headless --script scripts/mtagr_scene_probe.py
# Env: PROBE_SCENE (scene file), PROBE_OUT (output dir), PROBE_SPP (accumulated frames, default 256),
#      PROBE_ENV (env map used when the scene has no lights / env map; default Arcade HDR from Falcor media)
import os, time
from falcor import *

here = os.path.dirname(os.path.abspath(__file__))
out = os.environ.get("PROBE_OUT", "probe_out")
spp = int(os.environ.get("PROBE_SPP", "256"))
env = os.environ.get("PROBE_ENV", os.path.join(here, "..", "media", "Arcade", "20050806-03_hd.hdr"))
os.makedirs(out, exist_ok=True)

g = RenderGraph("Probe")
vb = createPass("VBufferRT", {"samplePattern": "Center", "sampleCount": 1, "useAlphaTest": True})
pt = createPass("PathTracer", {"samplesPerPixel": 1})
acc = createPass("AccumulatePass", {"enabled": True, "precisionMode": "Single"})
g.addPass(vb, "VBufferRT"); g.addPass(pt, "PathTracer"); g.addPass(acc, "Accumulate")
g.addEdge("VBufferRT.vbuffer", "PathTracer.vbuffer")
g.addEdge("VBufferRT.viewW", "PathTracer.viewW")
g.addEdge("VBufferRT.mvec", "PathTracer.mvec")
g.addEdge("PathTracer.color", "Accumulate.input")
for o in ("Accumulate.output", "PathTracer.albedo", "PathTracer.guideNormal", "VBufferRT.mvec", "VBufferRT.depth"):
    g.markOutput(o)

t0 = time.time()
m.loadScene(os.environ["PROBE_SCENE"])
print(f"PROBE_LOAD {time.time() - t0:.1f} s")
s = m.scene
st = s.stats
if st.get("activeLightCount", 0) == 0 and st.get("envMapMemoryInBytes", 0) == 0 and st.get("emissiveMemoryInBytes", 0) == 0:
    s.setEnvMap(env)
    print("PROBE_ENV added", env)
cam = os.environ.get("PROBE_CAM")  # "px,py,pz,tx,ty,tz" (scene units after import, i.e. meters for USD)
if cam:
    v = [float(x) for x in cam.split(",")]
    s.camera.position = float3(v[0], v[1], v[2])
    s.camera.target = float3(v[3], v[4], v[5])
    print("PROBE_CAM override", v)
m.addGraph(g)
m.resizeFrameBuffer(1920, 1080)
keys = ("meshInstanceCount", "uniqueTriangleCount", "instancedTriangleCount", "curveInstanceCount", "materialCount",
        "textureCount", "textureMemoryInBytes", "activeLightCount", "gridVolumeCount")
print("PROBE_STATS", {k: s.stats.get(k) for k in keys})
print("PROBE_CAMERA", s.camera.position, s.camera.target, "near/far", s.camera.nearPlane, s.camera.farPlane)
print("PROBE_BOUNDS", s.bounds.min_point, s.bounds.max_point)

m.clock.pause()
ptime = os.environ.get("PROBE_TIME")  # seconds; if set, accumulate at this time then step one frame (1/24 s) so the
if ptime:                             # captured motion vectors show animation between the two times
    m.clock.time = float(ptime)
t0 = time.time()
for i in range(spp):
    m.renderFrame()
if ptime:
    m.clock.time = float(ptime) + 1.0 / 24.0
    m.renderFrame()
    print("PROBE_TIME", ptime, "->", m.clock.time)
dt = time.time() - t0
print(f"PROBE_RENDER {spp} frames in {dt:.2f} s ({dt / spp * 1000:.1f} ms/frame)")
m.frameCapture.outputDir = out
m.frameCapture.baseFilename = "probe"
m.frameCapture.capture()
print("PROBE_DONE", out)
exit()
