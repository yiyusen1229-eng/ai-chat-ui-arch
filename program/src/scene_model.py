# -*- coding: utf-8 -*-
"""场景级配置（导演模型）：不属于任何角色，换角色不会丢。

存 <characters>/director.json：
    {"director": {"endpoint": "...", "key": "...", "model": "...", "connected": true}}
"""
import io
import json
import os
import urllib.request

KIND = "director"


def _path():
    try:
        import characters
        return os.path.join(characters.root(), "director.json")
    except Exception:
        return ""


def _load():
    p = _path()
    if not p or not os.path.isfile(p):
        return {}
    try:
        d = json.loads(io.open(p, encoding="utf-8").read())
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save(d):
    p = _path()
    if not p:
        return False
    try:
        io.open(p, "w", encoding="utf-8").write(json.dumps(d, ensure_ascii=False, indent=2))
        return True
    except Exception:
        return False


def _mask(k):
    k = (k or "").strip()
    if not k:
        return ""
    return "****" if len(k) <= 8 else k[:3] + "****" + k[-4:]


def _global_default():
    try:
        import main
        g = (main.model_cfg_all() or {}).get("chat") or {}
        return {"endpoint": g.get("endpoint") or "", "key": g.get("key") or "",
                "model": g.get("model") or ""}
    except Exception:
        return {}


def _probe(endpoint, key):
    base = (endpoint or "").rstrip("/")
    if not base:
        return False, "", "端点为空"
    try:
        rq = urllib.request.Request(base + "/v1/models",
                                    headers={"Authorization": "Bearer " + (key or "")})
        with urllib.request.urlopen(rq, timeout=8) as r:
            d = json.loads(r.read().decode("utf-8"))
        items = d.get("data") or []
        if items:
            return True, items[0].get("id") or "", ""
        return True, "", ""
    except Exception as e:
        return False, "", str(e)[:60]


def state():
    sec = _load().get(KIND) or {}
    g = _global_default()
    ep = sec.get("endpoint") or g.get("endpoint") or ""
    # 本机端点不显示云端的 key（界面上看到"有 key"会让人以为在打云端）
    key = sec.get("key") or ("" if _local_ep(ep) else (g.get("key") or ""))
    return {"kind": KIND,
            "endpoint": ep,
            "key": _mask(key),
            "hasKey": bool(key),
            "model": sec.get("model") or ("" if _local_ep(ep) else (g.get("model") or "")),
            "connected": bool(sec.get("connected"))}


def save(endpoint=None, key=None):
    d = _load()
    sec = d.get(KIND) or {}
    if endpoint is not None:
        sec["endpoint"] = str(endpoint).strip()
    if key is not None:
        sec["key"] = str(key).strip()
    d[KIND] = sec
    return {"ok": _save(d), "state": state()}


def connect():
    sec = _load().get(KIND) or {}
    g = _global_default()
    endpoint = sec.get("endpoint") or g.get("endpoint") or ""
    key = sec.get("key") or g.get("key") or ""
    ok, model, err = _probe(endpoint, key)
    if not ok:
        return {"ok": False, "error": err or "连接失败"}
    d = _load()
    s = d.get(KIND) or {}
    s["connected"] = True
    # 只在没填模型时才用探测到的第一个：本机 Ollama 列表里第一个往往是别的模型，
    # 直接盖掉会把用户选的模型换走
    if model and not s.get("model"):
        s["model"] = model
    d[KIND] = s
    _save(d)
    return {"ok": True, "model": model, "endpoint": endpoint}


def disconnect():
    d = _load()
    s = d.get(KIND) or {}
    s["connected"] = False
    d[KIND] = s
    _save(d)
    return {"ok": True, "state": state()}


def switch():
    d = _load()
    d[KIND] = {"endpoint": "", "key": "", "model": "", "connected": False}
    ok = _save(d)
    return {"ok": ok, "cleared": True, "state": state()}


def _local_ep(endpoint):
    """本机端点（Ollama / LM Studio / llama.cpp）：不要借云端 key、不要借云端模型名"""
    u = (endpoint or "").lower()
    return any(x in u for x in ("127.0.0.1", "localhost", "0.0.0.0", ":11434", "::1"))


def cfg():
    """给 director_gen 用：返回 {base_url, api_key, model}

    配的是本机端点时：不把云端的 key 带过去（本地服务不认它，还会把 key 发到本地
    日志里），也不回落云端的模型名（否则 127.0.0.1 + deepseek-flash 这种组合会直接
    「模型不存在」）。
    """
    sec = _load().get(KIND) or {}
    g = _global_default()
    ep = sec.get("endpoint") or g.get("endpoint") or ""
    if _local_ep(ep):
        return {"base_url": ep, "api_key": sec.get("key") or "",
                "model": sec.get("model") or ""}
    return {"base_url": ep,
            "api_key": sec.get("key") or g.get("key") or "",
            "model": sec.get("model") or g.get("model") or ""}
