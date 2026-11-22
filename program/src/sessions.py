# -*- coding: utf-8 -*-
"""存档读写：sessions/<slot>.json

从 main.py 拆出来的独立模块，对外只有四个函数：
    session_save(data)   存
    session_load(slot)   读
    session_list()       列表（不含缩略图本体）
    session_thumb(slot)  缩略图 JPEG 字节

存档带 schema_version：
- 老档没有版本号 → 按 0 处理，读完先备份再迁移写回
- 比程序新的存档 → 拒绝读取（save_too_new），提示升级程序，绝不硬读
- 坏档（不是 JSON / 结构不对）→ 返回 corrupt，不抛异常
"""
import base64
import json
import os
import shutil
import time

import datadir

SESS_DIR = datadir.env_or("WA2_SESS_DIR", "sessions")

# 存档结构版本：加字段 / 改结构就 +1，并在 _MIGRATIONS 里补一步 旧版 -> 新版
SCHEMA_VERSION = 1


def _mig_0_to_1(d):
    """v0（没有版本号的老档）→ v1：只是补上版本号，结构没变"""
    d.setdefault("messages", [])
    d.setdefault("meta", {})
    return d


_MIGRATIONS = {0: _mig_0_to_1}


def _sess_path(slot):
    """槽位号 → 文件路径（只保留安全字符，避免路径穿越）"""
    safe = "".join(c for c in str(slot or "auto") if c.isalnum() or c in "-_")[:32]
    return os.path.join(SESS_DIR, (safe or "auto") + ".json")


def _write_atomic(path, payload):
    """先写 .tmp 再原子替换：写一半断电不会毁掉旧档"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _read_payload(path):
    """读一个存档并做结构校验，返回 (payload, err)"""
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except Exception as e:
        return None, {"error": "corrupt", "detail": str(e)}
    if not isinstance(d, dict):
        return None, {"error": "corrupt", "detail": "not an object"}
    msgs = d.get("messages")
    if msgs is not None and not isinstance(msgs, list):
        return None, {"error": "corrupt", "detail": "messages is not a list"}
    if msgs is not None and any(not isinstance(m, dict) for m in msgs):
        return None, {"error": "corrupt", "detail": "message is not an object"}
    return d, None


def _ver_of(d):
    """存档版本；没有/写坏都按 0（老档）处理"""
    try:
        return int(d.get("schema_version"))
    except (TypeError, ValueError):
        return 0


def _migrate(d, v):
    """把 v 版迁到 SCHEMA_VERSION；返回 (payload, 原版本, err)"""
    start = v
    while v < SCHEMA_VERSION:
        fn = _MIGRATIONS.get(v)
        if fn is None:
            return None, start, {"error": "migrate_missing", "from": v}
        try:
            d = fn(d) or d
        except Exception as e:
            return None, start, {"error": "migrate_failed", "from": v, "detail": str(e)}
        v += 1
    d["schema_version"] = SCHEMA_VERSION
    return d, start, None


def _backup(path, frm):
    """迁移前留一份原档：<槽位>.json.v<旧版本>.bak（已存在就不覆盖）"""
    bak = "%s.v%d.bak" % (path, frm)
    if not os.path.exists(bak):
        shutil.copy2(path, bak)
    return bak


def session_save(data):
    try:
        slot = data.get("slot") or "auto"
        payload = {
            "schema_version": SCHEMA_VERSION,
            "slot": slot,
            "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            # 日记必须完整：这里**不裁剪**，回想界面翻的就是这份
            "messages": data.get("messages") or [],
            "meta": data.get("meta") or {},
        }
        # 记忆书（可选）：有就原样存，没有就不写 —— 旧档照样能读
        mem = data.get("memory")
        if isinstance(mem, dict):
            payload["memory"] = mem
        os.makedirs(SESS_DIR, exist_ok=True)
        path = _sess_path(slot)
        _write_atomic(path, payload)
        _SESS_CACHE.pop(os.path.basename(path), None)
        return {"ok": True, "slot": slot, "count": len(payload["messages"]),
                "saved_at": payload["saved_at"], "schema_version": SCHEMA_VERSION}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def session_load(slot="auto"):
    path = _sess_path(slot)
    if not os.path.exists(path):
        return {"ok": False, "error": "no such slot"}

    d, err = _read_payload(path)
    if err:
        return dict(ok=False, slot=slot, **err)

    v = _ver_of(d)
    if v > SCHEMA_VERSION:
        # 新版本程序写的存档：不猜、不硬读，让用户先升级
        return {"ok": False, "slot": slot, "error": "save_too_new",
                "schema_version": v, "supported": SCHEMA_VERSION,
                "hint": "存档来自更新版本的程序，请先升级程序再读"}

    migrated_from = None
    if v < SCHEMA_VERSION:
        d2, frm, merr = _migrate(d, v)
        if merr:
            return dict(ok=False, slot=slot, **merr)
        try:
            _backup(path, frm)
            _write_atomic(path, d2)
        except Exception as e:
            return {"ok": False, "slot": slot, "error": "migrate_write",
                    "detail": str(e)}
        d = d2
        migrated_from = frm
        _SESS_CACHE.pop(os.path.basename(path), None)

    out = dict(ok=True, **d)
    if migrated_from is not None:
        out["migrated_from"] = migrated_from
    return out


_SESS_CACHE = {}      # 文件名 -> (mtime, 摘要)：避免每次开界面都把上百个存档全解析一遍


def _session_brief(path, fname):
    """读一个存档，只取槽位要显示的那点信息（不含缩略图本体）"""
    try:
        mt = os.path.getmtime(path)
    except OSError:
        return None
    hit = _SESS_CACHE.get(fname)
    if hit and hit[0] == mt:
        return hit[1]
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        if not isinstance(d, dict):
            return None
        msgs = d.get("messages") or []
        if not isinstance(msgs, list):
            return None
        preview = ""
        for m in reversed(msgs):
            if isinstance(m, dict) and m.get("role") == "assistant":
                t = (m.get("zh") or m.get("content") or "").strip()
                if t:
                    preview = t
                    break
        meta = d.get("meta") or {}
        ver = _ver_of(d)
        rec = {"slot": d.get("slot"), "saved_at": d.get("saved_at"),
               "count": len(msgs), "preview": preview[:60],
               "bg": meta.get("bg", ""),
               # 缩略图不塞进列表：只报有没有，本体走 /api/session/thumb
               "thumb": bool(meta.get("thumb")),
               "schema_version": ver,
               # 比程序新：列表里标出来，别让用户以为能读
               "incompatible": ver > SCHEMA_VERSION}
        _SESS_CACHE[fname] = (mt, rec)
        return rec
    except Exception:
        return None


def session_list():
    try:
        os.makedirs(SESS_DIR, exist_ok=True)
        out = []
        for f in sorted(os.listdir(SESS_DIR)):
            if not f.endswith(".json"):
                continue
            rec = _session_brief(os.path.join(SESS_DIR, f), f)
            if rec:
                out.append(rec)
        return {"ok": True, "slots": out, "schema_version": SCHEMA_VERSION}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def session_thumb(slot):
    """槽位缩略图（JPEG 字节）；没有就返回 None"""
    try:
        path = _sess_path(slot)
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        t = (d.get("meta") or {}).get("thumb") or ""
        if not t.startswith("data:image"):
            return None
        return base64.b64decode(t.split(",", 1)[1])
    except Exception:
        return None
