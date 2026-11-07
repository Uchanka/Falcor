#!/usr/bin/env bash
# usage: mtagr_probe.sh <name> <scene file (windows path)> [spp]
set -u
F=/c/Users/grill/Desktop/Falcor; OUT=$F/probe_out/$1; rm -rf "$OUT"; mkdir -p "$OUT"
cd $F/build/windows-vs2022/bin/Release
PROBE_SCENE="$2" PROBE_OUT=$(cygpath -w "$OUT") PROBE_SPP=${3:-256} timeout ${PROBE_TIMEOUT:-1800} ./Mogwai.exe --headless \
  --script 'C:\Users\grill\Desktop\Falcor\scripts\mtagr_scene_probe.py' -v 3 > "$OUT/mogwai.log" 2>&1
echo "exit $?"; grep -E "^PROBE" "$OUT/mogwai.log"
grep -E "\(Error\)|\(Warning\)" "$OUT/mogwai.log" | grep -v "Warnings in program" | sort | uniq -c | sort -rn | head -12
python -I $F/scripts/mtagr_probe_png.py "$OUT"
