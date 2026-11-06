"""Step 1 of UDIM atlas baking: list UsdPreviewSurface materials whose textures use <UDIM>.

Run with a Python that has pxr (e.g. the usdview bundle):
    set_usd_env.bat && python udim_dump_materials.py <stage.usd> <out.json> [--purpose full]

Writes [{"key": ..., "slots": {"diffuseColor": {"pattern": ..., "channel": "rgb"}, ...}}, ...] with one entry per
unique set of texture patterns. "key" is the normalized absolute pattern of the first textured slot in
KEY_ORDER; the Falcor importer computes the same key (see ConvertedInput / PreviewSurfaceConverter).
"""
import json, os, sys
from pxr import Usd, UsdShade

KEY_ORDER = ("diffuseColor", "roughness", "metallic", "normal", "opacity")


def norm(p):
    return os.path.normpath(p).replace("\\", "/").lower()


def texture_pattern(inp):
    """(normalized absolute file pattern, output channel) for an input connected to a UsdUVTexture, else None."""
    if not inp.HasConnectedSource():
        return None
    src = inp.GetConnectedSources()[0][0]
    sh = UsdShade.Shader(src.source.GetPrim())
    if sh.GetIdAttr().Get() != "UsdUVTexture":
        return None
    fi = sh.GetInput("file")
    if not fi:
        return None
    attr = fi.GetAttr()
    for spec in attr.GetPropertyStack():  # strongest opinion with a value, resolved relative to its own layer
        if spec.default is not None:
            ap = spec.default.path
            if "<UDIM>" not in ap:
                return None
            return norm(os.path.join(os.path.dirname(spec.layer.realPath), ap)), str(src.sourceName)
    return None


def main():
    stage_path, out = sys.argv[1], sys.argv[2]
    purpose = sys.argv[sys.argv.index("--purpose") + 1] if "--purpose" in sys.argv else "full"
    stage = Usd.Stage.Open(stage_path)
    seen = {}
    pred = Usd.TraverseInstanceProxies()
    nmat = 0
    for prim in stage.Traverse(pred):
        if not prim.IsA(UsdShade.Material):
            continue
        nmat += 1
        surf = UsdShade.Material(prim).ComputeSurfaceSource()[0]
        if not surf or surf.GetIdAttr().Get() != "UsdPreviewSurface":
            continue
        slots = {}
        for name in KEY_ORDER:
            inp = surf.GetInput(name)
            if inp:
                tp = texture_pattern(inp)
                if tp:
                    slots[name] = {"pattern": tp[0], "channel": tp[1]}
        if not slots:
            continue
        key = next(slots[n]["pattern"] for n in KEY_ORDER if n in slots)
        sig = json.dumps(slots, sort_keys=True)
        if key in seen and seen[key]["sig"] != sig:
            print(f"WARN key collision with different slots: {key}", file=sys.stderr)
        seen.setdefault(key, {"key": key, "slots": slots, "sig": sig, "example": str(prim.GetPath())})
    res = [{k: v for k, v in e.items() if k != "sig"} for e in seen.values()]
    with open(out, "w", encoding="utf-8") as f:
        json.dump(res, f, indent=1)
    print(f"{nmat} materials traversed, {len(res)} unique UDIM texture sets -> {out}")


if __name__ == "__main__":
    main()
