# -*- coding: utf-8 -*-
"""模型接入抽象层（#24）：一个入口，三种 provider，一套错误与降级。

    from llm import generate, LLMError
    r = generate(payload, cfg, timeout=90, fallback_cfg=local_cfg)
    r.text / r.usage / r.model / r.degraded / r.reason

为什么要有这一层：调用点原来在 `api_chat._chat` 里直接 urllib，错误处理是两段
`except: self._json({"error": ...})` —— 「没余额」和「网线掉了」对用户都是同一句话，
而这两种情况该做的事完全不同（充值 vs 检查 Ollama）。

## provider

`provider_of(base_url)` 从 URL 认：本机 11434 → `ollama`；`/v1/chat/completions` 兼容
→ `openai`；其它 → `custom`（仍然按 OpenAI 形状发，认不出不影响能用）。

## 错误分类（`LLMError.kind`）

| kind | 触发 | 可重试 | 默认降级 |
|---|---|---|---|
| `auth` | 401 / 403 | 否 | 回落本地模型 |
| `balance` | 402 / body 里有 insufficient / 余额 | 否 | 回落本地模型 |
| `rate` | 429 | 是（1 次） | 还不行就回落 |
| `server` | 5xx | 是（1 次） | 还不行就回落 |
| `offline` | 超时 / 连不上 | 是（1 次） | 回落本地模型 |
| `bad_response` | 200 但没 choices / JSON 坏 | 是（1 次） | 还不行就报错 |

`degraded=True` 时调用方要告诉用户「这次用的是本地模型」——静默降级会让人以为
云端模型突然变笨了。

## 预算

`budget_note(cost)` 累加当天花费到 `cache/usage_daily.json`，`budget_status()`
给出今天花了多少、阈值多少、有没有超。阈值来自 `params.BUDGET_DAILY`（默认 0 = 不提醒）。
"""
import json
import os
import time
import urllib.error
import urllib.request

import datadir

# 每天花费提醒阈值（元）。0 = 不提醒；超过 80% 提醒一次，超过 100% 再提醒一次
BUDGET_DAILY = 0.0

CHAT_PATH = "/v1/chat/completions"

# 本地模型的上下文窗口。Ollama 默认跟着模型参数（这台机器上是 8192），
# 而修好世界书后提示词约 8500 token —— 超了会被截断、输出乱码。
# 这里显式传 num_ctx（只有原生 /api/chat 的 options 认这个参数）。
LOCAL_NUM_CTX = 16384
# 本地一次最多生成多少 token（对应 OpenAI 的 max_tokens）
LOCAL_NUM_PREDICT = 512
OLLAMA_PORT = ":11434"


class LLMError(Exception):
    """统一错误：调用方只需要看 kind 和 user_message"""

    def __init__(self, kind, message, user_message="", retryable=False, status=0):
        Exception.__init__(self, message)
        self.kind = kind
        self.detail = message
        self.user_message = user_message or message
        self.retryable = retryable
        self.status = status

    def __str__(self):
        return "%s: %s" % (self.kind, self.detail)


def is_local(base_url):
    """这台机器上的模型服务（Ollama / LM Studio / llama.cpp …）：不花钱"""
    u = (base_url or "").lower()
    return any(x in u for x in ("127.0.0.1", "localhost", "0.0.0.0", ":11434", "::1"))


class Result(object):
    def __init__(self, text, usage=None, model="", provider="", ms=0,
                 degraded=False, reason="", attempts=1, local=False):
        self.text = text
        self.usage = usage or {}
        self.model = model
        self.provider = provider
        self.ms = int(ms)
        self.degraded = degraded
        self.reason = reason
        self.attempts = attempts
        self.local = bool(local)
        if self.local:
            # 本地推理不要记成钱：模型的 token 是真的，但账面上是 0 元
            self.usage["_local"] = True

    def as_dict(self):
        return {"text": self.text, "usage": self.usage, "model": self.model,
                "provider": self.provider, "ms": self.ms,
                "degraded": self.degraded, "reason": self.reason,
                "attempts": self.attempts}


# ------------------------------------------------------------------ provider
def provider_of(base_url):
    u = (base_url or "").lower()
    if OLLAMA_PORT in u or "ollama" in u:
        return "ollama"
    if "/v1" in u or "openai" in u or "api." in u:
        return "openai"
    return "custom"


def endpoint(base_url):
    """拼出 chat 接口地址（已经带 /v1 的不重复加）"""
    b = (base_url or "").rstrip("/")
    if b.endswith("/v1/chat/completions"):
        return b
    if b.endswith("/v1"):
        return b + "/chat/completions"
    return b + CHAT_PATH


_SESSION_ID = None


def session_id():
    """一次运行一个稳定会话 ID：OpenCode Go 网关卡会话，缺了它直接 400"""
    global _SESSION_ID
    if not _SESSION_ID:
        try:
            import uuid
            _SESSION_ID = "ses_" + uuid.uuid4().hex
        except Exception:
            _SESSION_ID = "ses_wa2"
    return _SESSION_ID


def headers(cfg):
    h = {"Content-Type": "application/json"}
    if (cfg or {}).get("api_key"):
        h["Authorization"] = "Bearer " + cfg["api_key"]
    b = ((cfg or {}).get("base_url") or "").lower()
    if "opencode.ai" in b:
        # OpenCode Go 的两条硬要求：稳定会话 ID（否则 400 MissingSessionID），
        # 以及别用 python-urllib 的默认 UA（他们按 UA 认客户端）
        h["x-opencode-session"] = session_id()
        h["User-Agent"] = "WA2-Kazusa-Bot/1.0"
    return h


# ------------------------------------------------------------------ 错误分类
_BALANCE_HINTS = ("insufficient", "balance", "quota", "余额", "欠费", "credit")


def classify_status(code, body=""):
    b = (body or "").lower()
    if code in (401, 403):
        return LLMError("auth", "HTTP %s" % code, "密钥不对或没有权限（设置里换一个 key）",
                        False, code)
    if code == 402 or any(k in b for k in _BALANCE_HINTS):
        return LLMError("balance", "HTTP %s %s" % (code, b[:120]),
                        "账户余额不足（充值，或先切到本地模型）", False, code)
    if code == 429:
        return LLMError("rate", "HTTP 429", "请求太频繁，被限流了", True, code)
    if 500 <= code < 600:
        return LLMError("server", "HTTP %s" % code, "模型服务出错（HTTP %s）" % code, True, code)
    return LLMError("http", "HTTP %s %s" % (code, b[:120]),
                    "模型服务返回 HTTP %s" % code, False, code)


def classify_exc(e):
    if isinstance(e, LLMError):
        return e
    if isinstance(e, urllib.error.HTTPError):
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            body = ""
        return classify_status(e.code, body)
    if isinstance(e, (TimeoutError,)):
        return LLMError("offline", "timeout", "模型没在超时时间内回话（网络慢或服务卡了）", True)
    if isinstance(e, urllib.error.URLError):
        return LLMError("offline", str(e.reason), "连不上模型服务（检查网络 / Ollama 有没有启动）",
                        True)
    return LLMError("unknown", str(e), "调用模型失败：%s" % e, False)


def _extract(d, require_text=True):
    """从响应里取正文：兼容 OpenAI 形状和 Ollama 原生形状"""
    if not isinstance(d, dict):
        raise LLMError("bad_response", "响应不是 JSON 对象", "模型返回的东西看不懂（不是 JSON）", True)
    ch = d.get("choices")
    if isinstance(ch, list) and ch:
        m = ch[0].get("message") or {}
        txt = m.get("content")
        if txt is None:
            txt = (m.get("text") or "")
        if isinstance(txt, list):      # 少数兼容实现把 content 做成片段数组
            txt = "".join((p.get("text") or "") for p in txt if isinstance(p, dict))
        return _finish(txt, d, require_text)
    msg = d.get("message")
    if isinstance(msg, dict) and "content" in msg:
        usage = d.get("usage") or _native_usage(d)
        return _finish(msg.get("content"), {"usage": usage}, require_text)
    raise LLMError("bad_response", "没有 choices/message", "模型返回里没有正文", True)


def _finish(txt, d, require_text):
    txt = (txt or "").strip()
    if require_text and not txt:
        # 空正文当成功是最坑的一种「假成功」：实测推理型模型把预算全花在思考上时
        # 会返回 content 为空（reasoning_content 有内容），调用方以为拿到了回复，
        # 于是写出空摘要 / 界面上一个字都不出。
        raise LLMError("bad_response", "content 为空（可能被思考吃掉了预算）",
                       "模型没返回正文（可能把预算花在思考上了，换个模型或调大 max_tokens）", True)
    return txt, d.get("usage") or {}


# ------------------------------------------------------------------ 调用
def native_payload(payload):
    """OpenAI 形状 → Ollama 原生形状（并把 num_ctx 带上）"""
    p = {"model": payload.get("model"),
         "messages": payload.get("messages") or [],
         "stream": False,
         "options": {"num_ctx": int(LOCAL_NUM_CTX)}}
    mt = payload.get("max_tokens")
    p["options"]["num_predict"] = int(mt) if mt else int(LOCAL_NUM_PREDICT)
    if "temperature" in payload:
        p["options"]["temperature"] = payload["temperature"]
    # 本地小模型很容易刷同一句：加一点重复惩罚，并把"看过多少"放大
    # （default repeat_penalty=1.1 对 14B 不够，实测会整段复读）
    p["options"]["repeat_penalty"] = 1.18
    p["options"]["repeat_last_n"] = 256
    # 要求 JSON 的时候让 Ollama 自己约束输出（语法级保证是合法 JSON）：
    # 本地小模型经常先写一段散文再给 JSON，光靠提示词镇不住。
    # 给了 schema 就按 schema 约束（字段名、枚举都对），比 "json" 更稳。
    if payload.get("schema"):
        p["format"] = payload["schema"]
    elif payload.get("json"):
        p["format"] = "json"
    # 本地小模型不该把预算花在思考上
    p["think"] = False
    return p


def _native_usage(d):
    """原生响应的用量 → OpenAI 口径（pricing.py 认这个）

    顺手把 Ollama 报的真实耗时也带上（单位 ns，这里换 ms）：
    `_prefill_ms` / `_decode_ms` / `_load_ms`。排查「本地模型好慢」只看这三个数
    就知道该治 prefill（提示词太长 / 缓存没命中）还是 decode（模型太大）。
    """
    pin = int(d.get("prompt_eval_count") or 0)
    pout = int(d.get("eval_count") or 0)
    if not (pin or pout):
        return {}
    u = {"prompt_tokens": pin, "completion_tokens": pout, "total_tokens": pin + pout}
    for key, src in (("_prefill_ms", "prompt_eval_duration"),
                     ("_decode_ms", "eval_duration"),
                     ("_load_ms", "load_duration")):
        if d.get(src):
            u[key] = round(float(d[src]) / 1e6, 1)
    return u


def _note_local(r):
    """本地推理打一行：prefill 与 decode 各占多久（云端不打，免得日志变吵）"""
    u = r.usage
    if r.provider != "ollama" or not u.get("prompt_tokens"):
        return
    try:
        import applog
        applog.log("本地推理 %s: 提示 %d tok / prefill %sms, 输出 %d tok / decode %sms, 合计 %dms%s"
                   % (r.model, u["prompt_tokens"], u.get("_prefill_ms", "?"),
                      u.get("completion_tokens", 0), u.get("_decode_ms", "?"), r.ms,
                      (", 加载 %sms" % u["_load_ms"]) if u.get("_load_ms") else ""), "LLM")
    except Exception:
        pass


def post(url, payload, cfg, timeout=90):
    """真正发请求（测试里替换这个函数就能把网络挡在外面）

    本地 Ollama 走原生 /api/chat：只有它认 num_ctx（OpenAI 兼容接口会把那个参数丢掉，
    于是模型按自己的 8192 截断提示词）。云端仍然走 /v1/chat/completions。
    """
    if provider_of((cfg or {}).get("base_url")) == "ollama":
        native_url = (cfg.get("base_url") or "").rstrip("/")
        if native_url.endswith("/v1"):
            native_url = native_url[:-3]
        native_url = native_url + "/api/chat"
        rq = urllib.request.Request(native_url,
                                    data=json.dumps(native_payload(payload),
                                                    ensure_ascii=False).encode("utf-8"),
                                    headers=headers({"api_key": ""}))
        with urllib.request.urlopen(rq, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    # 云端：只发它认识的字段。json / schema 是我们给本地 Ollama 用的内部键，
    # 原样发过去会被当未知字段拒掉（deepseek 会直接 400）。
    body = dict(payload)
    want_json = bool(body.pop("json", False) or body.pop("schema", None))
    if want_json:
        body["response_format"] = {"type": "json_object"}
    rq = urllib.request.Request(url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                headers=headers(cfg))
    with urllib.request.urlopen(rq, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def generate(payload, cfg, timeout=90, fallback_cfg=None, retry_pause=0.6, sleep=None):
    """发一次请求；可重试的错误重试一次，再不行按需回落本地模型。

    返回 Result；全部失败抛 LLMError（带用户能看懂的话）。
    """
    sleep = sleep or time.sleep
    cfg = dict(cfg or {})
    url = endpoint(cfg.get("base_url"))
    t0 = time.time()
    attempts = 0
    last = None
    for i in (1, 2):
        attempts = i
        try:
            d = post(url, payload, cfg, timeout)
            text, usage = _extract(d, require_text=True)
            res = Result(text, usage, cfg.get("model") or "", provider_of(cfg.get("base_url")),
                         (time.time() - t0) * 1000, False, "", attempts,
                         local=is_local(cfg.get("base_url")))
            _note_local(res)
            return res
        except Exception as e:
            err = classify_exc(e)
            last = err
            if not err.retryable or i == 2:
                break
            sleep(retry_pause)

    if last is None:
        last = LLMError("unknown", "没有尝试", "调用模型失败", False)

    # 降级：本地模型还活着就换过去（只对 auth/balance/offline 这类「换个人还能干」的错）
    if fallback_cfg and last.kind in ("auth", "balance", "offline", "rate", "server"):
        fcfg = dict(fallback_cfg)
        fpayload = dict(payload)
        fpayload["model"] = fcfg.get("model") or payload.get("model")
        fpayload.pop("thinking", None)
        fpayload.pop("reasoning_effort", None)
        try:
            d = post(endpoint(fcfg.get("base_url")), fpayload, fcfg, timeout)
            text, usage = _extract(d, require_text=True)
            res = Result(text, usage, fcfg.get("model") or "", provider_of(fcfg.get("base_url")),
                         (time.time() - t0) * 1000, True, last.kind, attempts + 1,
                         local=is_local(fcfg.get("base_url")))
            _note_local(res)
            return res
        except Exception as e:
            other = classify_exc(e)
            last.user_message = "%s；本地模型也没成（%s）" % (last.user_message, other.user_message)
    raise last


# ------------------------------------------------------------------ 预算
def _budget_path():
    base = datadir.env_or("WA2_CACHE_DIR", "cache")
    return os.path.normpath(os.path.join(base, "usage_daily.json"))


def _today():
    return time.strftime("%Y-%m-%d")


def budget_status():
    """{date, cost, limit, ratio, over}"""
    try:
        with open(_budget_path(), encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        d = {}
    day = _today()
    if d.get("date") != day:
        d = {"date": day, "cost": 0.0}
    limit = _limit()
    cost = float(d.get("cost") or 0)
    ratio = (cost / limit) if limit > 0 else 0.0
    return {"date": day, "cost": round(cost, 4), "limit": limit,
            "ratio": round(ratio, 3), "over": bool(limit > 0 and cost >= limit)}


def _limit():
    """当天预算上限（元）。环境变量 WA2_BUDGET_DAILY 可以临时覆盖（自测用）"""
    try:
        env = os.environ.get("WA2_BUDGET_DAILY")
        if env not in (None, ""):
            return float(env)
    except Exception:
        pass
    try:
        return float(BUDGET_DAILY or 0)
    except Exception:
        return 0.0


def budget_note(cost):
    """累加今天的花费；返回 budget_status()"""
    try:
        c = float(cost or 0)
    except Exception:
        c = 0.0
    if c <= 0:
        return budget_status()
    p = _budget_path()
    st = budget_status()
    st["cost"] = float(st["cost"]) + c
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"date": st["date"], "cost": round(st["cost"], 6)}, f)
        os.replace(tmp, p)
    except Exception:
        pass
    limit = st["limit"]
    return {"date": st["date"], "cost": round(st["cost"], 4), "limit": limit,
            "ratio": round((st["cost"] / limit) if limit > 0 else 0.0, 3),
            "over": bool(limit > 0 and st["cost"] >= limit)}


def budget_warning(st):
    """给前端一句话；没到 80% 返回空串"""
    if not st or not st.get("limit"):
        return ""
    r = st.get("ratio") or 0
    if st.get("over"):
        return "今天的模型花费已经超过预算（%.2f/%.2f 元），建议切本地模型" % (st["cost"], st["limit"])
    if r >= 0.8:
        return "今天已花 %.0f%% 预算（%.2f/%.2f 元）" % (r * 100, st["cost"], st["limit"])
    return ""
