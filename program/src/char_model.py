# -*- coding: utf-8 -*-
"""角色级模型配置（多角色/多模型那套的落点）

每个角色可以有自己的聊天模型与语音模型：
    characters/<id>/model.json
        {
          "chat":  {"endpoint": "...", "key": "...", "model": "...", "connected": false},
          "voice": {"endpoint": "...", "key": "...", "model": "...", "connected": false}
        }

接口（main.py 里挂）：
    GET  /api/char/model               读当前角色的配置（key 打码）
    POST /api/char/model               写字段 {kind, endpoint, key}
    POST /api/char/model/connect       测试连接并自动探测模型名（/models）
    POST /api/char/model/disconnect    断开
    POST /api/char/model/switch        在「角色配置」与「全局默认」之间切换
"""
import io
import json
import os
import re
import urllib.request

KINDS = ("chat", "voice", "director")


def _char_dir():
    try:
        import characters
        fn = getattr(characters, "active_dir", None)
        if callable(fn):
            return fn() or ""
    except Exception:
        pass
    return ""


def _path():
    d = _char_dir()
    return os.path.join(d, "model.json") if d else ""


def _load_raw():
    p = _path()
    if not p or not os.path.isfile(p):
        return {}
    try:
        d = json.loads(io.open(p, encoding="utf-8").read())
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save_raw(d):
    p = _path()
    if not p:
        return False
    try:
        io.open(p, "w", encoding="utf-8").write(json.dumps(d, ensure_ascii=False, indent=2))
        return True
    except Exception:
        return False


def _mask(key):
    k = (key or "").strip()
    if not k:
        return ""
    if len(k) <= 8:
        return "****"
    return k[:3] + "****" + k[-4:]


def _global_default(kind):
    """系统设置里那套全局模型配置（拿不到就给空）"""
    try:
        import main
        cfg = main.model_cfg_all() if hasattr(main, "model_cfg_all") else {}
    except Exception:
        cfg = {}
    sec = (cfg or {}).get(kind) or {}
    return {"endpoint": sec.get("endpoint") or "",
            "key": sec.get("key") or "",
            "model": sec.get("model") or ""}


def get_state():
    raw = _load_raw()
    out = {"ok": True, "char": os.path.basename(_char_dir() or ""), "kinds": {}}
    for k in KINDS:
        sec = raw.get(k) or {}
        g = _global_default(k)
        out["kinds"][k] = {
            "endpoint": sec.get("endpoint") or "",
            "keyMask": _mask(sec.get("key") or ""),
            "hasKey": bool(sec.get("key")),
            "model": sec.get("model") or "",
            "connected": bool(sec.get("connected")),
            # 没配过就显示全局默认，界面上能看出「现在实际用的是什么」
            "usingGlobal": not (sec.get("endpoint") or sec.get("key")),
            "globalModel": g.get("model") or "",
            "globalEndpoint": g.get("endpoint") or ""
        }
    return out


def save_fields(kind, endpoint=None, key=None):
    if kind not in KINDS:
        return {"ok": False, "error": "bad kind"}
    raw = _load_raw()
    sec = raw.get(kind) or {}
    if endpoint is not None:
        sec["endpoint"] = str(endpoint).strip()
    if key is not None and str(key).strip():
        sec["key"] = str(key).strip()
    raw[kind] = sec
    if not _save_raw(raw):
        return {"ok": False, "error": "save_failed"}
    return {"ok": True}


def _probe(endpoint, key):
    """连一下 /models，返回 (ok, model_name, error)"""
    base = (endpoint or "").strip().rstrip("/")
    if not base:
        return False, "", "没填端点"
    if not re.match(r"^https?://", base):
        base = "http://" + base
    url = base + ("/models" if base.endswith("/v1") else "/v1/models")
    req = urllib.request.Request(url)
    if key:
        req.add_header("Authorization", "Bearer " + key.strip())
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            d = json.loads(r.read().decode("utf-8"))
        items = d.get("data") if isinstance(d, dict) else None
        if isinstance(items, list) and items:
            name = items[0].get("id") or items[0].get("name") or ""
            return True, name, ""
        return True, "", ""
    except Exception as e:
        return False, "", str(e)


def connect(kind):
    if kind not in KINDS:
        return {"ok": False, "error": "bad kind"}
    raw = _load_raw()
    sec = raw.get(kind) or {}
    g = _global_default(kind)
    endpoint = sec.get("endpoint") or g.get("endpoint") or ""
    key = sec.get("key") or g.get("key") or ""
    ok, model, err = _probe(endpoint, key)
    if not ok:
        return {"ok": False, "error": err or "连接失败"}
    sec["connected"] = True
    if model:
        sec["model"] = model
    raw[kind] = sec
    _save_raw(raw)
    return {"ok": True, "model": model, "endpoint": endpoint}


def disconnect(kind):
    if kind not in KINDS:
        return {"ok": False, "error": "bad kind"}
    raw = _load_raw()
    sec = raw.get(kind) or {}
    sec["connected"] = False
    raw[kind] = sec
    _save_raw(raw)
    return {"ok": True}


def switch(kind):
    """切换连接 = 清空这一类的配置，准备重新输入端点/API 连新的模型。

    （2026-11-09 用户澄清：不是切回全局默认，而是清空重填）
    """
    if kind not in KINDS:
        return {"ok": False, "error": "bad kind"}
    raw = _load_raw()
    raw[kind] = {"endpoint": "", "key": "", "model": "", "connected": False}
    ok = _save_raw(raw)
    return {"ok": ok, "cleared": True}
