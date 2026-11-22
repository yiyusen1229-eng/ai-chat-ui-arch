# -*- coding: utf-8 -*-
"""价格：从官网中文价格页抓当前单价（人民币），抓不到就用内置兜底表。

为什么用中文页：中文页标的是**元**，英文页是美元 —— 界面要显示人民币，直接用中文页。
抓不到也不能影响主流程：一律退回 BUILTIN，绝不让聊天因为查价失败而报错。

计费口径（DeepSeek 官方）：
    输入分「缓存命中 / 未命中」两档，输出一档；
    空闲时段 = 标价，高峰时段 = 标价 ×2
    高峰：UTC 周一~周五 01:00-04:00、06:00-10:00
    （中国法定节假日这里没判断，会被当成高峰价 —— 影响很小）
"""
import datetime
import json
import os
import re
import time
import urllib.request

import cache_io
import datadir

CACHE_DIR = datadir.env_or("WA2_CACHE_DIR", "cache")
CACHE_FILE = os.path.join(CACHE_DIR, "prices.json")
PRICE_URL = "https://api-docs.deepseek.com/zh-cn/quick_start/pricing"
TTL = 6 * 3600          # 6 小时内不重复抓

# 2026-11 中文价格页上的数字（元 / 1M token）
BUILTIN = {
    "deepseek-flash": {
        "hit":  {"off": 0.02, "peak": 0.04},
        "miss": {"off": 1.0,  "peak": 2.0},
        "out":  {"off": 4.0,  "peak": 8.0},
    },
    "deepseek-v4-pro": {
        "hit":  {"off": 0.15, "peak": 0.30},
        "miss": {"off": 4.5,  "peak": 9.0},
        "out":  {"off": 13.5, "peak": 27.0},
    },
    # OpenCode Go 里的 DeepSeek V4.1 Flash（官网高峰 $0.30/$1.20、空闲 $0.15/$0.60 每百万美元，
    # 按 7.2 折人民币；Go 是包月额度，这里给"按量等价"的参考价）
    "deepseek-v4.1-flash": {
        "hit":  {"off": 0.022, "peak": 0.043},
        "miss": {"off": 1.08,  "peak": 2.16},
        "out":  {"off": 4.32,  "peak": 8.64},
    },    # OpenCode Go 套餐里的 GLM-5.3-Flash（官网 $0.15 / $0.50 每百万，按 7.2 折人民币；
    # Go 是包月额度，这里给的是"按量等价"的参考价，方便界面上看个量级）
    "glm-5.3-flash": {
        "hit":  {"off": 0.22, "peak": 0.22},
        "miss": {"off": 1.08, "peak": 1.08},
        "out":  {"off": 3.60, "peak": 3.60},
    },
}
BUILTIN_CUR = "CNY"

_MEM = {"t": 0, "table": None, "source": "", "cur": BUILTIN_CUR}


def _cells(html):
    """按出现顺序取出所有单元格的纯文本"""
    out = []
    for m in re.finditer(r"<td[^>]*>(.*?)</td>", html, re.S):
        txt = re.sub(r"<[^>]+>", "", m.group(1))
        txt = txt.replace("&amp;", "&").replace("\u200b", "")
        out.append(re.sub(r"\s+", " ", txt).strip())
    return out


def _money(cell):
    """'0.02元' / '$0.003' → (数字, 'CNY'|'USD')"""
    m = re.search(r"([\d.]+)\s*元", cell)
    if m:
        return float(m.group(1)), "CNY"
    m = re.search(r"\$\s*([\d.]+)", cell)
    if m:
        return float(m.group(1)), "USD"
    return None, None


def _kind(label):
    u = label.upper()
    if "输入" in label or "INPUT" in u:
        if ("命中" in label and "未命中" not in label) or "CACHE HIT" in u:
            return "hit"
        return "miss"
    if "输出" in label or "OUTPUT" in u:
        return "out"
    return None


def _parse(html):
    """解析成 (价格表, 币种)；解析不出来返回 (None, None)"""
    cells = _cells(html)
    models = []
    for i, c in enumerate(cells):
        if c.upper() in ("模型", "MODEL"):
            for c2 in cells[i + 1: i + 4]:
                n = re.sub(r"\(\d+\)", "", c2).strip()
                if n and " " not in n and n.lower().startswith("deepseek"):
                    models.append(n)
            break
    if not models:
        return None, None

    table = {m: {"hit": {}, "miss": {}, "out": {}} for m in models}
    cur = None
    kind = None
    i, n = 0, len(cells)
    while i < n:
        c = cells[i]
        k = _kind(c)
        if k and ("百万" in c or "1M" in c.upper() or "TOKENS" in c.upper()):
            kind = k
            i += 1
            continue
        phase = ("off" if ("空闲" in c or c.upper() == "OFF-PEAK")
                 else ("peak" if ("高峰" in c or c.upper() == "PEAK") else None))
        if kind and phase:
            j, filled = i + 1, 0
            while j < n and filled < len(models):
                num, cc = _money(cells[j])
                if num is not None:
                    table[models[filled]][kind][phase] = num
                    cur = cur or cc
                    filled += 1
                elif ("空闲" in cells[j] or "高峰" in cells[j]
                      or _kind(cells[j])):
                    break
                j += 1
            i = j
            continue
        i += 1
    for m in models:
        for k2 in ("hit", "miss", "out"):
            if len(table[m][k2]) != 2:
                return None, None
    return table, (cur or "CNY")


def _load_cache():
    # 坏缓存（0 字节 / 半截）当没有：cache_io 会删掉它，下面回落到内置价目
    d = cache_io.read_json(CACHE_FILE)
    if isinstance(d, dict) and d.get("table"):
        return d
    return None


def _save_cache(table, cur):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        tmp = CACHE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"t": time.time(), "table": table, "cur": cur},
                      f, ensure_ascii=False, indent=1)
        os.replace(tmp, CACHE_FILE)
    except Exception:
        pass


def table(force=False):
    """返回 (价格表, 来源)。来源：official / cache / builtin"""
    now = time.time()
    if not force and _MEM["table"] and now - _MEM["t"] < TTL:
        return _MEM["table"], _MEM["source"]
    c = _load_cache()
    if not force and c and now - c.get("t", 0) < TTL:
        _MEM.update({"t": c["t"], "table": c["table"], "source": "cache",
                     "cur": c.get("cur") or BUILTIN_CUR})
        return c["table"], "cache"
    try:
        req = urllib.request.Request(PRICE_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            html = r.read().decode("utf-8", "replace")
        t, cur = _parse(html)
        if t:
            _save_cache(t, cur)
            _MEM.update({"t": now, "table": t, "source": "official", "cur": cur})
            return t, "official"
    except Exception:
        pass
    if c and c.get("table"):
        _MEM.update({"t": c.get("t", 0), "table": c["table"], "source": "cache",
                     "cur": c.get("cur") or BUILTIN_CUR})
        return c["table"], "cache"
    _MEM.update({"t": now, "table": BUILTIN, "source": "builtin", "cur": BUILTIN_CUR})
    return BUILTIN, "builtin"


def currency():
    table()
    return _MEM.get("cur") or BUILTIN_CUR


def prices_for(model):
    t, src = table()
    name = (model or "").strip()
    if name in t:
        return t[name], src
    first = next(iter(t.values())) if t else BUILTIN["deepseek-flash"]
    return first, src


def is_peak(now=None):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    if now.weekday() >= 5:          # 周末全时段便宜
        return False
    h = now.hour + now.minute / 60.0
    return (1 <= h < 4) or (6 <= h < 10)


def cost(model, usage):
    """按官方口径算一次调用的花费（单位见 currency()）"""
    if not usage:
        return 0.0
    p, _src = prices_for(model)
    key = "peak" if is_peak() else "off"
    hit = int(usage.get("prompt_cache_hit_tokens") or 0)
    prompt = int(usage.get("prompt_tokens") or 0)
    miss = usage.get("prompt_cache_miss_tokens")
    miss = int(miss) if miss is not None else max(0, prompt - hit)
    out = int(usage.get("completion_tokens") or 0)
    return (hit / 1e6 * p["hit"][key] +
            miss / 1e6 * p["miss"][key] +
            out / 1e6 * p["out"][key])


def estimate(model, prompt_tokens, completion_tokens=0, cache_hit_tokens=0):
    """给「还没发生的一轮」估价：和 cost() 用同一张价目表、同一套高峰判断。

    特别模式那个「每轮费用预估」走这里，保证和右下角的花费是同口径
    （换模型、进高峰时段两边一起变）。默认按「缓存未命中」算，偏保守。
    """
    p, src = prices_for(model)
    key = "peak" if is_peak() else "off"
    prompt_tokens = max(0, int(prompt_tokens or 0))
    completion_tokens = max(0, int(completion_tokens or 0))
    hit = min(prompt_tokens, max(0, int(cache_hit_tokens or 0)))
    miss = prompt_tokens - hit
    total = (hit / 1e6 * p["hit"][key] +
             miss / 1e6 * p["miss"][key] +
             completion_tokens / 1e6 * p["out"][key])
    return {
        "model": model,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "phase": key,
        "peak": key == "peak",
        "currency": currency(),
        "price": {"hit": p["hit"][key], "miss": p["miss"][key], "out": p["out"][key]},
        "cost": round(total, 6),
        "source": src,
    }
