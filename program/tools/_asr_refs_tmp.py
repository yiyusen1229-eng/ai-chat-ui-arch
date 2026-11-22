# -*- coding: utf-8 -*-
import json, os, sys, io
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from faster_whisper import WhisperModel
spec = json.load(open(sys.argv[1], encoding="utf-8"))
d, files, out = spec["dir"], spec["files"], spec["out"]
m = WhisperModel("large-v3", device="cpu", compute_type="int8")
res = {}
for i, f in enumerate(files, 1):
    p = os.path.join(d, f)
    try:
        segs, _info = m.transcribe(p, language="ja", beam_size=5, vad_filter=False)
        res[f] = "".join(s.text for s in segs).strip()
    except Exception as e:
        res[f] = ""
    print("  [%d/%d] %s" % (i, len(files), (res[f] or "")[:40]), flush=True)
json.dump(res, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
