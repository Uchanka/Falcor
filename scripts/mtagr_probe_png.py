"""Turn a probe output dir's EXRs into small PNG previews + a coverage report.
    python -I mtagr_probe_png.py <probe_out_dir>
"""
import glob, os, sys
import numpy as np
import OpenEXR, Imath
from PIL import Image


def read(f):
    e = OpenEXR.InputFile(f); h = e.header(); dw = h["dataWindow"]
    W, H = dw.max.x - dw.min.x + 1, dw.max.y - dw.min.y + 1
    ch = [c for c in "RGB" if c in h["channels"]] or list(h["channels"])
    return np.stack([np.frombuffer(e.channel(c, Imath.PixelType(Imath.PixelType.FLOAT)), np.float32).reshape(H, W) for c in ch], -1)


d = sys.argv[1]
for f in sorted(glob.glob(os.path.join(d, "*.exr"))):
    a = read(f); name = os.path.basename(f)
    if "depth" in name:
        z = a[..., 0]; hit = z < 1.0
        print(f"{name}: hit {hit.mean() * 100:.1f}% of pixels")
        v = np.where(hit, z, z[hit].max() if hit.any() else 1)
        lo, hi = (np.percentile(v[hit], 1), np.percentile(v[hit], 99)) if hit.any() else (0, 1)
        img = 1 - np.clip((v - lo) / max(hi - lo, 1e-9), 0, 1); img = np.repeat(img[..., None], 3, -1)
    elif "mvec" in name:
        print(f"{name}: |mvec| max {np.abs(a[..., :2]).max():.4f}")
        img = np.clip(a[..., :2] * 20 + 0.5, 0, 1); img = np.concatenate([img, np.zeros_like(img[..., :1])], -1)
    elif "Normal" in name:
        img = np.clip(a[..., :3] * 0.5 + 0.5, 0, 1)
    else:
        print(f"{name}: mean {a[..., :3].mean():.4f} max {a[..., :3].max():.3f}")
        x = a[..., :3]; x = x / (1 + x) if "Accumulate" in name else x  # Reinhard for radiance
        img = np.clip(x, 0, 1) ** (1 / 2.2)
    Image.fromarray((img * 255).astype(np.uint8)).resize((960, 540)).save(os.path.splitext(f)[0] + ".png")
