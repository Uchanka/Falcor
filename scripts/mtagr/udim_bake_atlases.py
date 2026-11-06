"""Step 2 of UDIM atlas baking: pack each material's UDIM tiles into BC7 atlases Falcor can load directly.

    python -I udim_bake_atlases.py <materials.json> <out_dir> [--tile 2048] [--max-atlas 16384] [-j 8] [--dry-run]

Per texture set (materials.json from udim_dump_materials.py) it writes into <out_dir>:
    <id>_base.dds    BC7, sRGB-encoded base color (ACEScg -> linear Rec.709 -> sRGB), alpha = opacity (if any)
    <id>_spec.dds    BC7, linear (0, roughness, metallic, 1)  -- Falcor StandardMaterial metal-rough packing
    <id>_normal.dds  BC7, tangent-space normal as stored ([0,1] encoding)
and manifest.json: {key: {"cols": W, "rows": H, "tile": T, "base": ..., "spec": ..., "normal": ..., "alpha": bool}}.

Atlas layout: UDIM tile 1001 + u + 10*v goes to grid cell (u, v), row v counted from the BOTTOM of the image, so
atlas uv = (u / W, v / H) in USD's bottom-up convention. The importer scales texture coordinates by (1/W, 1/H).
Tile resolution = min(--tile, --max-atlas / max(W, H)), rounded down to a power of two.
"""
import argparse, glob, hashlib, json, os, re, subprocess, sys, tempfile, time
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np

NVCOMPRESS = r"C:\Users\grill\Desktop\Falcor\external\packman\nvtt\nvcompress.exe"
# ACES AP1 (ACEScg) -> linear Rec.709/sRGB primaries, D60 -> D65 (Bradford), as used by OCIO.
ACESCG_TO_REC709 = np.array([[1.70505, -0.62179, -0.08326],
                             [-0.13026, 1.14080, -0.01055],
                             [-0.02400, -0.12897, 1.15297]], np.float32)


def tiles_of(pattern):
    """{udim: path} for files matching a lower-cased '<udim>' pattern."""
    pat = re.sub(r"<udim>", "<UDIM>", pattern, flags=re.I)
    out = {}
    for f in glob.glob(pat.replace("<UDIM>", "1[0-9][0-9][0-9]")):
        m = re.search(r"\.(1\d{3})\.[^.]+$", f)
        if m:
            out[int(m.group(1))] = f
    return out


def read_exr(path, want):
    """Read base level as float32 HxWxC. want: 'rgb' or 'r'. Handles 'rgb.R'-style layered channel names."""
    import OpenEXR, Imath
    f = OpenEXR.InputFile(path)
    h = f.header()
    dw = h["dataWindow"]; W, H = dw.max.x - dw.min.x + 1, dw.max.y - dw.min.y + 1
    chans = list(h["channels"].keys())

    def pick(c):
        for cand in (c, c.lower(), f"rgb.{c}", f"rgba.{c}", f"RGB.{c}"):
            if cand in chans:
                return cand
        cs = [x for x in chans if x.split(".")[-1].upper() == c]
        return cs[0] if cs else None

    ft = Imath.PixelType(Imath.PixelType.FLOAT)
    names = [pick(c) for c in "RGB"] if want == "rgb" else [pick("R") or pick("Y") or chans[0]]
    if any(n is None for n in names):  # grayscale stored as single channel
        names = [names[0] or chans[0]] * 3 if want == "rgb" else [chans[0]]
    a = np.stack([np.frombuffer(f.channel(n, ft), np.float32).reshape(H, W) for n in names], -1)
    return a


def srgb_encode(x):
    x = np.clip(x, 0, 1)
    return np.where(x <= 0.0031308, 12.92 * x, 1.055 * np.power(x, 1 / 2.4) - 0.055)


def plan(entry, tile_max, atlas_max):
    tiles = set()
    per_slot = {}
    for name, s in entry["slots"].items():
        t = tiles_of(s["pattern"])
        per_slot[name] = t
        tiles |= set(t)
    if not tiles:
        return None
    us = [(t - 1001) % 10 for t in tiles]; vs = [(t - 1001) // 10 for t in tiles]
    W, H = max(us) + 1, max(vs) + 1
    T = min(tile_max, atlas_max // max(W, H))
    T = 1 << (T.bit_length() - 1)
    return {"W": W, "H": H, "T": T, "tiles": sorted(tiles), "per_slot": per_slot}


def compress(png, dds, flags):
    r = subprocess.run([NVCOMPRESS, "-silent", "-repeat", "-bc7", *flags, png, dds], capture_output=True, text=True)
    if r.returncode or not os.path.exists(dds):
        raise RuntimeError(f"nvcompress failed {r.returncode}: {r.stdout[-300:]} {r.stderr[-300:]}")


def bake(entry, out_dir, tile_max, atlas_max):
    import cv2
    p = plan(entry, tile_max, atlas_max)
    if p is None:
        return entry["key"], None, "no tiles on disk"
    W, H, T = p["W"], p["H"], p["T"]
    sid = hashlib.sha1(entry["key"].encode()).hexdigest()[:16]
    names = {k: os.path.join(out_dir, f"{sid}_{k}.dds") for k in ("base", "spec", "normal")}
    has_alpha = "opacity" in p["per_slot"] and bool(p["per_slot"]["opacity"])
    rec = {"cols": W, "rows": H, "tile": T, "alpha": has_alpha, "base": names["base"],
           "spec": names["spec"] if "roughness" in p["per_slot"] or "metallic" in p["per_slot"] else None,
           "normal": names["normal"] if p["per_slot"].get("normal") else None, "tiles": p["tiles"]}
    if all(v is None or os.path.exists(v) for k, v in rec.items() if k in ("base", "spec", "normal")):
        return entry["key"], rec, "cached"

    q = lambda a: (np.clip(a, 0, 1) * 255 + 0.5).astype(np.uint8)  # atlases kept 8-bit (16K float32 = 4 GB each)
    base = np.zeros((H * T, W * T, 4), np.uint8); base[..., :3] = q(srgb_encode(np.float32(0.18))); base[..., 3] = 255
    spec = np.zeros((H * T, W * T, 4), np.uint8); spec[..., 1] = 128; spec[..., 3] = 255
    nrm = np.zeros((H * T, W * T, 3), np.uint8); nrm[...] = (128, 128, 255)

    def cell(t):
        u, v = (t - 1001) % 10, (t - 1001) // 10
        y0 = (H - 1 - v) * T
        return slice(y0, y0 + T), slice(u * T, (u + 1) * T)

    def load(slot, t, want):
        f = p["per_slot"].get(slot, {}).get(t)
        if not f:
            return None
        a = read_exr(f, want)
        return cv2.resize(a, (T, T), interpolation=cv2.INTER_AREA) if a.shape[0] != T else a

    for t in p["tiles"]:
        ys, xs = cell(t)
        c = load("diffuseColor", t, "rgb")
        if c is not None:
            c = c.reshape(-1, 3) @ ACESCG_TO_REC709.T
            base[ys, xs, :3] = q(srgb_encode(c.reshape(T, T, 3)))
        o = load("opacity", t, "r")
        if o is not None:
            base[ys, xs, 3] = q(o.reshape(T, T))
        r = load("roughness", t, "r")
        if r is not None:
            spec[ys, xs, 1] = q(r.reshape(T, T))
        m = load("metallic", t, "r")
        if m is not None:
            spec[ys, xs, 2] = q(m.reshape(T, T))
        n = load("normal", t, "rgb")
        if n is not None:
            nrm[ys, xs] = q(n)

    with tempfile.TemporaryDirectory(dir=out_dir) as td:
        def png(name, arr):
            fn = os.path.join(td, name + ".png")
            a8 = arr
            cv2.imwrite(fn, cv2.cvtColor(a8, cv2.COLOR_RGBA2BGRA if a8.shape[2] == 4 else cv2.COLOR_RGB2BGR))
            return fn
        compress(png("base", base), names["base"], ["-alpha"] if has_alpha else ["-noalpha"])
        if rec["spec"]:
            compress(png("spec", spec), names["spec"], ["-noalpha"])
        if rec["normal"]:
            compress(png("normal", nrm), names["normal"], ["-normal"])
    return entry["key"], rec, f"baked {W}x{H} tiles @ {T}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("materials"); ap.add_argument("out_dir")
    ap.add_argument("--tile", type=int, default=2048)
    ap.add_argument("--max-atlas", type=int, default=16384)
    ap.add_argument("-j", type=int, default=6)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    mats = json.load(open(a.materials, encoding="utf-8"))
    if a.limit:
        mats = mats[:a.limit]
    os.makedirs(a.out_dir, exist_ok=True)
    if a.dry_run:
        tot = 0; big = []
        for e in mats:
            p = plan(e, a.tile, a.max_atlas)
            if not p:
                continue
            nt = 1 + ("roughness" in e["slots"] or "metallic" in e["slots"]) + ("normal" in e["slots"])
            b = p["W"] * p["H"] * p["T"] ** 2 * 1.0 * 4 / 3 * nt  # BC7 = 1 byte/texel, + mips
            tot += b; big.append((b, p["W"], p["H"], p["T"], e["key"].split("/")[-1]))
        big.sort(reverse=True)
        print(f"{len(mats)} sets, estimated GPU texture memory {tot / 1e9:.1f} GB")
        for b, W, H, T, k in big[:8]:
            print(f"  {b / 1e9:5.2f} GB  {W}x{H} tiles @ {T}  {k}")
        return
    man_p = os.path.join(a.out_dir, "manifest.json")
    man = json.load(open(man_p)) if os.path.exists(man_p) else {}
    t0 = time.time()
    with ProcessPoolExecutor(a.j) as ex:
        futs = [ex.submit(bake, e, a.out_dir, a.tile, a.max_atlas) for e in mats]
        for i, f in enumerate(as_completed(futs), 1):
            try:
                key, rec, msg = f.result()
            except Exception as e:  # noqa: BLE001
                print(f"[{i}/{len(mats)}] ERROR {e!r}", flush=True); continue
            if rec:
                man[key] = rec
            print(f"[{i}/{len(mats)}] {msg}  {key.split('/')[-1]}  ({time.time() - t0:.0f} s)", flush=True)
            if i % 10 == 0:
                json.dump(man, open(man_p, "w"), indent=1)
    json.dump(man, open(man_p, "w"), indent=1)
    print(f"done: {len(man)} atlases in manifest {man_p}")


if __name__ == "__main__":
    main()
