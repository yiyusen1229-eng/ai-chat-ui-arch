# -*- coding: utf-8 -*-
"""
AI 聊天界面架构 —— 可插拔的桌面聊天 UI 框架
内嵌本地 HTTP 服务 + WebView2 原生窗口
"""
# ============================================================================
# 目录 —— 这个文件 1700+ 行，按段读，别整篇读
#   ※ 行号会漂移；找位置请搜 §标题。
#
#   §0  顶部常量             路径 / 窗口尺寸 / 模型名 / 安全模式开关
#   §1  偏好设置             prefs.json 读写（GET/POST /api/prefs）
#   §2  模型配置             cloud_cfg / probe_endpoint / model_cfg_*
#   §3  标签解析             fewshot + <scene> <emotion> <action> 拆分
#   §4  翻译                 translate_ja（人设/世界书在 persona_ctx.py）
#   §5  token 与花费         _record_usage / /api/usage（算钱在 pricing.py）
#   §6  记忆书胶水           _memory_context / 后台打包（核心逻辑在 memory.py）
#   §7  HTTP 服务            class Handler
#        7.1 路由 do_GET / do_POST
#        7.2 静态文件 translate_path / send_head（Range）
#        7.3 窗口控制 _window_ctl
#        7.4 语音 _tts
#        7.5 模型配置接口 _modelcfg
#        7.6 文字渲染 _text_img
#        7.7 对话 _chat  ← 最大的一块
#   §8  启动                 ollama_models / _warm_font / serve / main
#
# 新功能请**新建独立文件**，不要再往这个文件里堆（见交接文档「一个功能一个文件」）。
# ============================================================================
import os
import sys
import shutil
import applog
import secret
import tts_client
import json
import time
import threading
try:
    import story_rag as STORY
except Exception:
    STORY = None
import special_cfg
import special_files
import characters
import char_res
import diag
import hotreload
import powerwatch
import logpack
import persona_ctx
import persona_stage
import runstate
import scene_cg
import api_export
import firstrun
import standing
try:
    import gdi_text as GLIB
except Exception:
    GLIB = None
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

# 用 `python main.py` 起的时候，本文件在 sys.modules 里叫 "__main__"，
# 别的模块里写的 `from main import ...` / `import main` 会**把这个文件再执行一遍**，
# 拿到的是另一份空壳：没有 _WINDOW、没有 _API，改它也不影响真身。
# 症状就是窗口接口一律回 {"ok": false, "error": "module 'main' has no attribute '_WINDOW'"}，
# 表现为「全屏 / 最大化点了没反应」（2026-11-05 用户报「全屏失效」就是这个）。
# 提前把 "main" 这个名字指到真身上：之后再 import main 拿到的都是同一份全局。
if __name__ == "__main__":
    sys.modules.setdefault("main", sys.modules["__main__"])

BUILD_TAG = time.strftime("%H%M%S")
APP_NAME = "WHITE ALBUM2 Extension~ どこまでも続く道~"
WINDOW_TITLE = APP_NAME
try:
    from version import VERSION as _VERSION
except Exception:
    _VERSION = ""
OLLAMA = "http://127.0.0.1:11434"
# 安全模式: 存在 模型开关.txt(或环境变量 WA2_NO_MODELS=1) 时不加载任何模型
def _no_models_flag():
    if os.environ.get("WA2_NO_MODELS") == "1":
        return True
    roots = [os.path.dirname(os.path.abspath(__file__))]
    if getattr(sys, "frozen", False):
        roots.insert(0, os.path.dirname(os.path.abspath(sys.executable)))
    for r in roots:
        for name in ("模型开关.txt", "no_models.flag"):
            if os.path.exists(os.path.join(r, name)):
                return True
    return False


NO_MODELS = _no_models_flag()

DEFAULT_MODEL = "kazusa14b-v7"
FALLBACK_MODEL = "kazusa14b-v6"


def res_dir():
    """资源目录: 打包后为 _MEIPASS, 开发时为脚本目录"""
    if getattr(sys, "_MEIPASS", None):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


BASE = res_dir()
UI_DIR = os.path.join(BASE, "ui2")
# 界面素材目录：开发时直接用 program/res/assets（run_dev.py 用环境变量指过来），
# 免得每次都把 200MB 素材复制进 ui2；打包版没有这个变量，就用 ui2/assets。
ASSETS_DIR = os.environ.get("WA2_ASSETS_DIR") or os.path.join(UI_DIR, "assets")
# 特别模式的立绘要直接放进 assets/sprites/<衣服>/，把这个目录告诉它
special_files.set_assets_dir(ASSETS_DIR)
# 场景背景图（assets/cg/index.json）也走同一个素材目录
scene_cg.set_assets_dir(ASSETS_DIR)
# 多角色：总角色资源文件夹 characters/ + 当前角色（见 characters.py）
# 自带 persona/ 与 worldbook.json 只当模板：角色文件夹空了就复制一份进去，
# 之后程序只读角色文件夹（角色完全自包含）；第一次启动还会把老的 char/<kind>/ 搬过来。
characters.set_seed_sources(persona_ctx.PERSONA_DIR, persona_ctx.WORLDBOOK_FILE)
_CHAR_ID = characters.ensure()
applog.log("当前角色: %s（%s）" % (_CHAR_ID, characters.active_dir()), "CHAR")
# 人设 / 世界书：各自一个文件夹（persona/、worldbook/），逻辑在 persona_ctx.py
EXE_DIR = (os.path.dirname(os.path.abspath(sys.executable))
           if getattr(sys, "frozen", False) else BASE)
CLOUD_CFG_FILE = os.path.join(EXE_DIR, "config.json")
FEWSHOT_FILE = os.path.join(EXE_DIR, "fewshot.json")
# 界面设置（音量 / 确认开关 / 右键功能 / 文字速度 / 对话框透明度…）
# 放 exe 同目录：清理缓存、private_mode 清 localStorage 都不影响它
PREFS_FILE = os.path.join(EXE_DIR, "prefs.json")


# 窗口模式的窗口大小（DIP/CSS 像素）。退出全屏后回到这个尺寸：
# 这台机器 250% 缩放下 1280x720 已经占满屏幕 83%，全屏和"窗口"肉眼一样大，
# 所以窗口模式固定用更小的一档（也是程序的 min_size）。
WINDOWED_SIZE = (1024, 576)


def _screen_size():
    """物理像素的屏幕尺寸（含任务栏区域）"""
    try:
        import ctypes
        u = ctypes.WinDLL("user32")
        return int(u.GetSystemMetrics(0)), int(u.GetSystemMetrics(1))
    except Exception:
        return 0, 0


def _dpi_scale():
    try:
        import ctypes
        return ctypes.WinDLL("user32").GetDpiForSystem() / 96.0
    except Exception:
        return 1.0


def prefs_load():
    """读界面设置；文件坏了就当没有（不抛异常，否则整个界面起不来）"""
    try:
        with open(PREFS_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def prefs_save(d):
    """整体覆写界面设置（先写临时文件再原子替换，避免写一半留下坏 JSON）"""
    if not isinstance(d, dict):
        return False
    try:
        tmp = PREFS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1, sort_keys=True)
        os.replace(tmp, PREFS_FILE)
        return True
    except Exception as e:
        try:
            applog.log("保存界面设置失败: %s" % e)
        except Exception:
            pass
        return False


def cloud_cfg():
    """云端配置：**环境变量里的 key 优先**于 config.json。

    这样设了 WA2_API_KEY / DEEPSEEK_API_KEY 就完全不用往文件里写 key；
    文件里那份仍然保留，方便「临时去掉环境变量」时回落，不会被覆盖掉。
    """
    try:
        d = json.load(open(CLOUD_CFG_FILE, encoding="utf-8"))
    except Exception:
        d = {}
    if not isinstance(d, dict):
        d = {}
    d = secret.apply_env(d)
    if d.get("api_key") and d.get("model"):
        return d
    return {}

def _http_ok(url, headers=None, timeout=4):
    """访问一下，能拿到 HTTP 响应就算通"""
    try:
        req = urllib.request.Request(url, headers=headers or {})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return 200 <= r.status < 400 or r.status in (401, 403)
    except urllib.error.HTTPError as e:
        # 有响应（比如 404/401）说明服务是活的
        return e.code in (400, 401, 403, 404, 405, 422)
    except Exception:
        return False


def probe_endpoint(sec, endpoint, key, model):
    """按板块类型探测端点是否可用，返回 (ok, latency_ms, models)"""
    ep = (endpoint or "").rstrip("/")
    if not ep:
        return False, 0, []
    t0 = time.time()
    ok, models = False, []

    if sec == "chat":
        hdr = {"Authorization": "Bearer " + key} if key else {}
        for path in ("/models", "/v1/models"):
            try:
                with urllib.request.urlopen(
                        urllib.request.Request(ep + path, headers=hdr),
                        timeout=4) as r:
                    d = json.loads(r.read().decode("utf-8"))
                    models = [m.get("id") for m in d.get("data", []) if m.get("id")]
                    ok = True
                    break
            except Exception:
                continue

    elif sec == "voice":
        hdr = {}
        if key:
            hdr["Authorization"] = "Bearer " + key
            hdr["xi-api-key"] = key
        for path in ("/docs", "/", "/openapi.json", "/v1/models", "/health"):
            if _http_ok(ep + path, hdr):
                ok = True
                break

    else:  # mt
        for path in ("/api/tags", "/v1/models", "/api/version"):
            if _http_ok(ep + path):
                ok = True
                break
        if ok:
            try:
                with urllib.request.urlopen(ep + "/api/tags", timeout=4) as r:
                    d = json.loads(r.read().decode("utf-8"))
                    models = [m.get("name") for m in d.get("models", []) if m.get("name")]
            except Exception:
                pass

    return ok, int((time.time() - t0) * 1000), models


def ensure_cloud_cfg():
    """首次启动若没有 config.json，生成一份空白模板（不含任何密钥）"""
    try:
        if os.path.exists(CLOUD_CFG_FILE):
            return
        blank = {
            "provider": "",
            "base_url": "",
            "api_key": "",
            "model": "",
            "models": {
                "chat":  {"endpoint": "", "key": "", "model": "", "connected": False},
                "voice": {"endpoint": "", "key": "", "model": "", "connected": False},
                "mt":    {"endpoint": "", "key": "", "model": "", "connected": False},
            },
        }
        with open(CLOUD_CFG_FILE, "w", encoding="utf-8") as f:
            json.dump(blank, f, ensure_ascii=False, indent=1)
        log("generated blank config: %s" % CLOUD_CFG_FILE)
    except Exception as e:
        try:
            log("ensure_cloud_cfg failed: %s" % e)
        except Exception:
            pass
def apply_model_cfg():
    """把保存过的配置应用到运行时（聊天扁平字段 / 语音地址）"""
    try:
        cfg = model_cfg_all()
    except Exception:
        return
    voice = cfg.get("voice") or {}
    ep = (voice.get("endpoint") or "").strip()
    if ep:
        try:
            tts_client.URL = ep.rstrip("/")
        except Exception:
            pass








MODEL_SECTIONS = ("chat", "voice", "mt")
TTS_ENDPOINT_HINT = "http://127.0.0.1:9880"


def model_cfg_all():
    """三个板块的配置；没有存过就按当前实际在用的值给默认"""
    try:
        d = json.load(open(CLOUD_CFG_FILE, encoding="utf-8"))
    except Exception:
        d = {}
    m = d.get("models") or {}

    def pick(sec, ep, key, model):
        got = m.get(sec) or {}
        return {
            "endpoint": got.get("endpoint") or ep,
            "key": got.get("key") or key,
            "model": got.get("model") or model,
            "connected": bool(got.get("connected")),
        }

    chat_ep = d.get("base_url") or OLLAMA
    chat_key = d.get("api_key") or ""
    chat_model = d.get("model") or DEFAULT_MODEL
    out = {
        "chat": pick("chat", chat_ep, chat_key, chat_model),
        "voice": pick("voice", TTS_ENDPOINT_HINT, "", "GPT-SoVITS"),
        "mt": pick("mt", OLLAMA, "", TRANSLATE_MODEL),
    }
    # 翻译板块：说清楚它现在到底在不在用（用户反馈过「方案改了还显示已连接」）
    try:
        import translate as _tr
        _st = _tr.status()
        out["mt"]["connected"] = bool(out["mt"].get("connected")) and _st["mt_in_use"]
        out["mt"]["in_use"] = bool(_st["mt_in_use"])
        out["mt"]["active_engine"] = _st["label"]
        out["mt"]["note"] = ("当前翻译走「%s」%s" % (
            _st["label"], "" if _st["mt_in_use"] else "；这块配置只是备用"))
    except Exception:
        pass
    # key 来自环境变量时，界面只拿到掩码：真 key 不出后端，也不会被保存回去
    env = secret.env_key()
    if env:
        out["chat"]["key"] = secret.mask(env)
        out["chat"]["key_from_env"] = True
    return out


def model_cfg_save(sec, endpoint, key, model, connected):
    try:
        d = json.load(open(CLOUD_CFG_FILE, encoding="utf-8"))
    except Exception:
        d = {}
    m = d.get("models") or {}
    key = key or ""
    # 界面回填的是掩码（key 来自环境变量）时，别把 `sk-****abcd` 写进 config.json
    if secret.is_masked(key):
        old = (m.get(sec) or {}).get("key") or ""
        if sec == "chat" and not old:          # 老配置只有扁平字段，也要留住
            old = d.get("api_key") or ""
        key = old
    m[sec] = {"endpoint": endpoint, "key": key,
              "model": model, "connected": bool(connected)}
    d["models"] = m
    # 聊天板块同时写扁平字段，_chat() 立刻用新值
    if sec == "chat":
        d["base_url"] = endpoint
        d["api_key"] = key
        d["model"] = model
    try:
        with open(CLOUD_CFG_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        return True
    except Exception as e:
        log("model cfg save failed: %s" % e)
        return False


def fewshot_msgs():
    try:
        rows = json.load(open(FEWSHOT_FILE, encoding="utf-8"))
    except Exception:
        return []
    out = []
    for r in rows:
        u, a = (r.get("u") or "").strip(), (r.get("a") or "").strip()
        if u and a:
            out.append({"role": "user", "content": u})
            out.append({"role": "assistant", "content": a})
    return out



# ---------------- 人设 / 世界书（见 persona_ctx.py） ----------------
import threading as _th
from winutil import _win_hwnd, _user32, _quiet_act
from sessions import session_save, session_load, session_list, session_thumb
import sessions as _sessions
import memory as _memory
import pricing as _pricing
from api_window import WindowApi
from api_char import CharApi
from api_modelcfg import ModelCfgApi
from api_tts import TtsApi
from api_text import TextApi
from api_chat import ChatApi
from api_charpack import CharPackApi
from api_log import LogApi
from api_perfcfg import PerfCfgApi
from api_hotreload import HotReloadApi
from api_wizard import WizardApi
from api_cleanup import CleanupApi
from api_choice import ChoiceApi
from api_memory import MemoryApi
from api_update import UpdateApi
from text_parse import (
    ACTION_RE, split_scene, split_emotion, split_action, split_thinking,
    ja_clean as _ja_clean, ja_ok as _ja_ok,
)

# 特别模式「查看文件」：没导入时要指到程序真正读的默认位置，而不是空的 char/ 目录
_defaults = persona_ctx.default_paths()
special_files.set_default_paths(
    prompt=_defaults["prompt"],
    worldbook=_defaults["worldbook"],
    memory=_sessions.SESS_DIR,
    voice=tts_client.refs_dir(),
)


def _set_character_sessions():
    """合并版：存档按角色分开，切换角色不会读到另一个人的记录。

    显式设了 WA2_SESS_DIR（自测 / 便携版）时尊重它，不覆盖。
    """
    if os.environ.get("WA2_SESS_DIR"):
        return
    try:
        d = os.path.join(characters.active_dir(), "sessions")
        os.makedirs(d, exist_ok=True)
        old = _sessions.SESS_DIR
        if old and os.path.isdir(old) and os.path.abspath(old) != os.path.abspath(d):
            # 第一次升级：把老的公共存档迁给当前角色
            try:
                if not os.listdir(d):
                    for fn in os.listdir(old):
                        sp = os.path.join(old, fn)
                        if os.path.isfile(sp):
                            shutil.copy2(sp, os.path.join(d, fn))
            except Exception:
                pass
        _sessions.SESS_DIR = d
        try:
            special_files.DEFAULT_PATHS["memory"] = d
        except Exception:
            pass
    except Exception as e:
        log("角色存档目录设置失败: %s" % e)


_set_character_sessions()


def qs(q, names, default=""):
    """从 parse_qs 结果里取第一个存在的参数名（正式名 + 兼容旧名）"""
    for n in names:
        v = q.get(n)
        if v and v[0] != "":
            return v[0]
    return default
_RENDER_LOCK = _th.Lock()
_TEXT_CACHE = {}


def _clear_text_cache():
    """启动时清空字模磁盘缓存：下次用到再重新生成，避免缓存无限增长"""
    try:
        import shutil
        d = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                          "..", "res", "assets", "ui", "text_cache"))
        if os.path.isdir(d):
            shutil.rmtree(d, ignore_errors=True)
    except Exception:
        pass


def _text_disk_path(key):
    """文字渲染的磁盘缓存路径（同参数只渲染一次，之后直接读文件）"""
    import hashlib
    h = hashlib.md5(repr(key).encode("utf-8")).hexdigest()[:16]
    d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "res", "assets", "ui", "text_cache")
    d = os.path.normpath(d)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, h + ".png")
_TEXT_CACHE_MAX = 2000
# 内存里到多少条就开始丢最早的（#12：原来这个 400 写死在 api_text 里，和上面那 2000 对不上）
_TEXT_CACHE_EVICT = 400


def log(msg):
    """轻量日志: 打包后没有控制台, 写 logs/wa2_app.log 方便排查（写之前先脱敏）

    不写系统 %TEMP%：用户不希望 C 盘里出现程序的临时文件；
    真的写不进 logs/ 时 applog._log_dir() 自己会退到 %TEMP%/wa2_logs。
    """
    try:
        import datetime
        d = applog._log_dir()
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, "wa2_app.log")
        with open(p, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (datetime.datetime.now().strftime("%H:%M:%S"),
                                   secret.redact(msg)))
    except Exception:
        pass


persona_ctx.set_logger(log)      # 人设/世界书的日志也走这里
persona_stage.set_logger(log)    # 关系阶段的日志


def _write_asset_report():
    """启动时扫一遍素材，写 logs/asset_report.json（缺图/0 字节文件一目了然）"""
    try:
        import assets_report
        try:
            char_dir = characters.active_dir()
        except Exception:
            char_dir = ""
        r = assets_report.build(ASSETS_DIR, char_dir)
        assets_report.write(os.path.join(applog._log_dir(), "asset_report.json"), r)
        log(assets_report.summary(r))
        return r
    except Exception as e:
        log("素材自检失败：%s" % e)
        return None


def _hot_reload_setup():
    """素材热重载（#20）：素材 / 角色文件夹一变，就让字模缓存和立绘索引失效、重写素材报告。"""

    def _on_text(kinds):
        _clear_text_cache()            # 字体或素材变了，旧字模不能再用

    def _on_char(kinds):
        try:
            import char_res
            char_res._CACHE.clear()    # 立绘索引 / 人设按 mtime 缓存，清掉就会重读
        except Exception:
            pass

    def _on_assets(kinds):
        _write_asset_report()

    hotreload.watch(ASSETS_DIR, "")
    hotreload.set_hooks({"text_cache": _on_text, "char_res": _on_char,
                         "asset_report": _on_assets})
    hotreload.start()
    powerwatch.start()          # #18：睡眠唤醒检测（前端据此重建音频）


def _log_maintenance():
    """启动时清一次旧日志（#15：按天滚动，默认留 14 天；崩溃现场留最新 10 份）"""
    try:
        r = logpack.prune()
        if r.get("removed"):
            log("日志清理：%d 个旧文件，%.1f KB" % (len(r["removed"]), r["freed"] / 1024.0))
    except Exception as e:
        log("日志清理失败（不影响运行）：%s" % e)



_JA_CACHE = {}
TRANSLATE_MODEL = 'qwen2.5:14b-instruct-q4_K_M'

_JA_SHOTS = [
    ('别管我。', 'ほっといて。'),
    ('你真是个大笨蛋。', 'お前って本当にバカね。'),
    ('我今天不想说话。', '今日は話したくない。'),
]




def translate_ja(text):
    """中译日(用户侧指令 + 和纱模型), 用于日文配音; 失败返回空串"""
    t = (text or '').strip()
    if not t:
        return ''
    if t in _JA_CACHE:
        return _JA_CACHE[t]
    # 先走统一的翻译层（translate.py）：云端优先、本地兜底，还能套术语表
    try:
        import translate as _tr
        ja = _tr.to_ja(t)
        if ja and _ja_ok(ja):
            if len(_JA_CACHE) < 500:
                _JA_CACHE[t] = ja
            return ja
    except Exception as e:
        log("翻译层失败（回落旧路径）: %s" % e)
    _mt = (model_cfg_all().get("mt") or {})
    mt_ep = (_mt.get("endpoint") or OLLAMA).rstrip("/")
    mt_model = _mt.get("model") or TRANSLATE_MODEL
    # 优先: 专用翻译模型 Hy-MT2（准确、快）
    try:
        tts_client.start_translate()
        ja = tts_client.translate_via_service(t, "ja")
        if ja and _ja_ok(ja):
            if len(_JA_CACHE) < 500:
                _JA_CACHE[t] = ja
            return ja
    except Exception as e:
        log("专用翻译失败: %s" % e)
    # 兜底: 本地模型
    try:
        payload = {
            'model': mt_model,          # 和纱模型, 日文最自然
            'messages': [
                {'role': 'user',
                 'content': '把这句话翻译成日语，只输出日语，不要中文：' + t},
            ],
            'stream': False,
            'max_tokens': 90,
            'reasoning_effort': 'none',
        }
        rq = urllib.request.Request(
            mt_ep + '/v1/chat/completions',
            data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
            headers={'Content-Type': 'application/json'},
        )
        with urllib.request.urlopen(rq, timeout=45) as r:
            d = json.loads(r.read().decode('utf-8'))
        raw = d['choices'][0]['message'].get('content') or ''
        raw, _ = split_thinking(raw)
        ja = _ja_clean(raw)
        if _ja_ok(ja):
            if len(_JA_CACHE) < 500:
                _JA_CACHE[t] = ja
            return ja
        log('翻译结果不含日文: %r' % raw[:60])
    except Exception as e:
        log('翻译失败: %s' % e)
    return ''


# ---------------- token / 花费统计 ----------------
_USAGE = {"tokens": 0, "cost": 0.0, "calls": 0,
          "since": time.strftime("%Y-%m-%d %H:%M:%S")}
_USAGE_LOCK = _th.Lock()


def _record_usage(model, usage):
    """记一次模型调用，返回这次的花费信息（给界面显示用）。

    usage 直接用接口返回的 usage 字段（有 prompt_cache_hit_tokens 就更准）；
    拿不到 usage 时记 0 —— 统计不准也不能影响对话。
    """
    usage = usage or {}
    try:
        c = _pricing.cost(model, usage)
    except Exception:
        c = 0.0
    tokens = int(usage.get("total_tokens") or 0)
    with _USAGE_LOCK:
        _USAGE["tokens"] += tokens
        _USAGE["cost"] += c
        _USAGE["calls"] += 1
        cum_t, cum_c = _USAGE["tokens"], _USAGE["cost"]
    return {"tokens": tokens,
            "in": int(usage.get("prompt_tokens") or 0),
            "out": int(usage.get("completion_tokens") or 0),
            "cost": round(c, 6),
            "model": model,
            "cumTokens": cum_t,
            "cumCost": round(cum_c, 6),
            "currency": _pricing.currency()}


def _usage_snapshot():
    with _USAGE_LOCK:
        d = dict(_USAGE)
        d["cost"] = round(d["cost"], 6)
    try:
        _t, src = _pricing.table()
        d["priceSource"] = src
        d["peak"] = _pricing.is_peak()
        d["currency"] = _pricing.currency()
    except Exception:
        d["priceSource"] = "builtin"
        d["peak"] = False
        d["currency"] = "CNY"
    d["ok"] = True
    return d

# ---------------- 记忆书（见 memory.py） ----------------
_MEM_PACK_LOCK = _th.Lock()


def _stage_llm(system, user, max_tokens=6, timeout=20):
    """关系阶段分类：只回一个数字，尽量便宜（不加载人设）"""
    cc = cloud_cfg()
    if not cc:
        return ""
    base_url = (os.environ.get("WA2_MEM_URL")
                or cc.get("base_url") or OLLAMA).rstrip("/")
    key = cc.get("api_key") or ""
    model = cc.get("model") or DEFAULT_MODEL
    headers = {"Content-Type": "application/json", "Authorization": "Bearer " + key}
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "stream": False,
        "max_tokens": max_tokens,
        "temperature": 0,
    }
    if "pro" in model or "deepseek" in model.lower():
        payload["thinking"] = {"type": "disabled"}
        payload["reasoning_effort"] = "none"
    rq = urllib.request.Request(
        base_url + "/v1/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers)
    with urllib.request.urlopen(rq, timeout=timeout) as r:
        d = json.loads(r.read().decode("utf-8"))
    try:
        _record_usage(model, d.get("usage") or {})
    except Exception:
        pass
    return (d["choices"][0]["message"].get("content") or "").strip()


persona_stage.set_llm(_stage_llm)      # 关系阶段用云端只回一个数字


def _mem_llm(prompt, max_tokens=1200, timeout=90):
    """给记忆书用的模型调用：不加载人设，只让它把一段对话压成条目。

    WA2_MEM_URL 可以把这一步单独指到别的端点（自测用假模型时不烧真实 token）。
    """
    cc = cloud_cfg()
    if cc:
        base_url = (os.environ.get("WA2_MEM_URL")
                    or cc.get("base_url") or OLLAMA).rstrip("/")
        key = cc.get("api_key") or ""
        model = cc.get("model") or DEFAULT_MODEL
        headers = {"Content-Type": "application/json", "Authorization": "Bearer " + key}
    else:
        base_url = (os.environ.get("WA2_MEM_URL") or OLLAMA).rstrip("/")
        model = DEFAULT_MODEL
        headers = {"Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": _memory.SUM_SYSTEM},
                     {"role": "user", "content": prompt}],
        "stream": False,
        "max_tokens": max_tokens,
        "temperature": 0.2,
    }
    if not cc:
        payload["reasoning_effort"] = "none"
    if cc and ("pro" in model or "deepseek" in (base_url or "").lower() or "deepseek" in (model or "").lower()):
        payload["thinking"] = {"type": "disabled"}
        payload["reasoning_effort"] = "none"
    rq = urllib.request.Request(
        base_url + "/v1/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers)
    try:
        with urllib.request.urlopen(rq, timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8"))
        try:
            _record_usage(model, d.get("usage") or {})
        except Exception:
            pass
        return (d["choices"][0]["message"].get("content") or "").strip()
    except Exception as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:200]
        except Exception:
            pass
        log("记忆书：模型调用失败 %s: %s %s" % (type(e).__name__, e, detail))
        raise


# 常驻记忆书的关系摘要（#3）：用同一个便宜模型，只要 3~6 行短句
standing.set_llm(lambda prompt: _mem_llm(prompt, max_tokens=400, timeout=60))


def _memory_context(msgs, cloud):
    """按 token 预算决定这一轮发什么。

    没超阈值：原样全发（和以前一样）。
    超了阈值：只发「最近一段原文」，更早的压成记忆条目一起注入。

    **不会留空洞**：`covered`（已总结到哪）如果落后于「最近段起点」，
    窗口就往前拉到 covered，宁可多发几句，也不让中间那段两头不着。
    """
    try:
        trig, recent_b, entries_b = _memory.budget(cloud)
        total = _memory.est_messages(msgs)
        if total <= trig or len(msgs) < 4:
            return msgs, ""
        r = _memory.runtime()
        aligned = _memory.recent_start(msgs, recent_b, 0)
        if aligned <= 0:
            return msgs, ""
        covered = int(r.get("covered") or 0)
        # 历史变短了（"开始游戏"清空 / 换了存档）→ 记忆书作废，别把上一段对话的印象带过来
        if covered > len(msgs):
            _memory.reset_for_load([], 0, "")
            r = _memory.runtime()
            covered = 0
        # 第一次跨过阈值：必须先把旧的那段压成条目，否则就只能整段拒发
        if not r.get("entries"):
            ok, n = _memory.pack_span(msgs, 0, aligned, _mem_llm,
                                      max_chunks=int(_memory.cfg()["sync_chunks"]))
            covered = int(_memory.runtime().get("covered") or 0) if ok else 0
            log("记忆书：首次压缩%s（%d 条，覆盖到第 %d 条）"
                % ("成功" if ok else "失败", n, covered))
        start = min(aligned, covered) if covered else 0
        r = _memory.runtime()
        note = _memory.render_note(r.get("entries") or [], entries_b)
        turn = int(r.get("turns") or 0) + 1
        _memory.runtime_set(turns=turn, window_start=start)
        repack = int(_memory.cfg()["repack_turns"])
        if covered and aligned > covered and repack > 0 and turn % repack == 0:
            _repack_async(msgs, covered, aligned)
        log("记忆书：估算 %d / 阈值 %d，发最近 %d 条（共 %d 条，%d 条记忆）"
            % (total, trig, len(msgs) - start, len(msgs), len(r.get("entries") or [])))
        return (msgs[start:], note) if start > 0 else (msgs, note)
    except Exception as e:
        log("记忆书失败（本轮按全量发送）: %s" % e)
        return msgs, ""


def _repack_async(msgs, a, b):
    """后台重新打包被折叠的那一段；同时在跑就跳过（不排队）。"""
    def work():
        if not _MEM_PACK_LOCK.acquire(blocking=False):
            return
        try:
            ok, n = _memory.pack_span(msgs, a, b, _mem_llm,
                                      max_chunks=int(_memory.cfg()["async_chunks"]))
            if ok:
                log("记忆书：后台打包 %d~%d（%d 条）" % (a, b, n))
        except Exception as e:
            log("记忆书：后台打包失败 %s" % e)
        finally:
            _MEM_PACK_LOCK.release()
    _th.Thread(target=work, daemon=True).start()


# ---------------- HTTP 服务 ----------------


class Handler(UpdateApi, ChatApi, WindowApi, CharApi, CharPackApi, MemoryApi,
              ModelCfgApi, TtsApi, TextApi, LogApi, PerfCfgApi, HotReloadApi, WizardApi,
              CleanupApi, ChoiceApi,
              SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=UI_DIR, **kw)

    def end_headers(self):
        # 禁止 WebView2 缓存, 否则改了 css/js 看不到效果
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        super().end_headers()

    def log_message(self, *a):
        pass

    def translate_path(self, path):
        p = path.split("?")[0].split("#")[0]
        if p == "/" or p == "/game" or p == "/game/":
            p = "/index.html"
        elif p.startswith("/game/"):
            p = p[len("/game"):]
        elif p.startswith("/api/character/sprite/"):
            # 立绘图：当前角色的 portrait/ 优先，没有再用自带 assets/sprites/
            import urllib.parse as _up
            rel = _up.unquote(p[len("/api/character/sprite/"):])
            return (characters.sprite_file(os.path.join(ASSETS_DIR, "sprites"), rel)
                    or os.path.join(UI_DIR, "__api__"))
        elif p.startswith("/api/"):
            return os.path.join(UI_DIR, "__api__")
        import posixpath, urllib.parse
        p = urllib.parse.unquote(p)
        p = posixpath.normpath(p).lstrip("/")
        # /game/assets/** 走素材目录（开发时指向 program/res/assets）
        if p.split("/")[0] == "assets" and os.path.isdir(ASSETS_DIR):
            parts = p.split("/")[1:]
            return os.path.join(ASSETS_DIR, *parts)
        return os.path.join(UI_DIR, *p.split("/"))

    def _json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def send_response(self, code, message=None):
        self._last_status = code
        return super().send_response(code, message)

    def handle_one_request(self):
        self._req_start = time.time()
        try:
            return super().handle_one_request()
        finally:
            self._log_request(getattr(self, "_last_status", ""))

    def send_head(self):
        """支持 Range 的静态文件服务（视频能边下边播、可跳读）"""
        path = self.translate_path(self.path)
        if os.path.isdir(path):
            return super().send_head()
        try:
            f = open(path, "rb")
        except OSError:
            return super().send_head()
        try:
            fs = os.fstat(f.fileno())
            size = fs[6]
            ctype = self.guess_type(path)
            rng = self.headers.get("Range")
            start, end = 0, size - 1
            partial = False
            if rng and rng.startswith("bytes=") and size > 0:
                spec = rng[6:].split(",")[0].strip()
                a, _, b = spec.partition("-")
                try:
                    if a:
                        start = int(a)
                        if b:
                            end = min(int(b), size - 1)
                    elif b:                      # bytes=-N 取末尾 N 字节
                        start = max(0, size - int(b))
                except ValueError:
                    start, end = 0, size - 1
                if start > end or start >= size:
                    self.send_error(416)
                    f.close()
                    return None
                partial = True
            count = end - start + 1
            self.send_response(206 if partial else 200)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(count))
            if partial:
                self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
            self.send_header("Last-Modified", self.date_time_string(fs.st_mtime))
            self.end_headers()
            f.seek(start)
            self._range_left = count
            return f
        except Exception:
            f.close()
            raise

    def copyfile(self, source, outputfile):
        left = getattr(self, "_range_left", None)
        self._range_left = None
        if left is None:
            import shutil
            shutil.copyfileobj(source, outputfile, 1024 * 256)
            return
        while left > 0:
            chunk = source.read(min(1024 * 256, left))
            if not chunk:
                break
            outputfile.write(chunk)
            left -= len(chunk)

    def _log_request(self, status=None):
        try:
            dur = (time.time() - self._req_start) * 1000 if hasattr(self, "_req_start") else 0
            lvl = "SLOW" if dur > applog._SLOW_REQUEST_MS else "REQ"
            # 这两个接口的查询串本身就是对话 / 朗读文本，日志里不记（#15：日志也会被打进诊断包）
            path = self.path[:120]
            if "/api/text?" in path or "/api/tts?" in path:
                path = path.split("?", 1)[0] + "?<已隐藏>"
            # 热重载轮询（#20）每 4 秒一次，别把日志刷满
            quiet = path.startswith("/api/hotreload")
            if not quiet or lvl == "SLOW":
                applog.log("%s %s -> %s  %.0fms" % (getattr(self, "command", "?"),
                                                    path, status or "", dur), lvl)
        except Exception:
            pass

    def do_GET(self):
        self._req_start = time.time()
        if self.path.startswith("/api/export/chat"):
            return api_export.handle_get(self)   # 它自己解析 query（do_GET 里 q 是后面的局部变量，这里还不存在）
        if self.path.startswith("/api/firstrun"):
            return self._json(firstrun.state())
        if self.path.startswith("/api/window"):
            return self._window_ctl()
        if self.path.startswith("/api/log/dump"):
            applog.dump_stacks("手动触发")
            return self._json({"ok": True, "log": applog.path()})
        if self.path.startswith("/api/log/bundle"):
            return self._log_bundle()
        if self.path.startswith("/api/tts"):
            return self._tts()
        if self.path.startswith("/api/performance"):
            return self._performance_get()
        if self.path.startswith("/api/hotreload"):
            return self._hotreload_get()
        if self.path.startswith("/api/power"):
            return self._json(dict(ok=True, **powerwatch.state()))
        if self.path.startswith("/api/assets/wizard"):
            return self._wizard_get()
        if self.path.split("?")[0] == "/api/group/cast":
            try:
                import group as _grp
                return self._json({"ok": True, "items": _grp.list_cast(),
                                   "prefs": _grp.load_prefs()})
            except Exception as e:
                return self._json({"ok": False, "error": str(e)}, 500)
        if self.path.split("?")[0] == "/api/group/prefs":
            try:
                body = self._body()
                import group as _grp
                d = _grp.save_prefs(cast=body.get("cast"), story=body.get("story"))
                return self._json({"ok": True, "prefs": d})
            except Exception as e:
                return self._json({"ok": False, "error": str(e)}, 400)
        if self.path.split("?")[0] == "/api/cleanup":
            # #17 数据清理入口：概览（各项体积 + 角色列表），只读不删
            return self._cleanup_get()
        if self.path.startswith("/api/debug/state"):
            # 调试面板要的只读状态包（#13）：提示词分段 / 命中 / 缓存 / 分类器 / 阶段
            try:
                return self._json(diag.snapshot())
            except Exception as e:
                return self._json({"ok": False, "error": str(e)}, 500)
        if self.path == "/api/ping":
            return self._json({"ok": True, "app": APP_NAME})
        if self.path == "/api/run/state":
            return self._json(runstate.state())
        if self.path.startswith("/api/update/status"):
            # 一键更新：前端轮询进度（要排在 /api/update 前面）
            return self._update_status()
        if self.path.startswith("/api/update"):
            return self._update_check()
        if self.path.startswith("/api/open_url"):
            return self._open_url()
        if self.path == "/api/voice/ok":
            try:
                import tts_client as _tc
                _st = _tc.status()
                return self._json({"ok": (not NO_MODELS) and bool(_st.get("available")),
                                   "ready": bool(_st.get("ready")),
                                   "error": _st.get("error") or ""})
            except Exception as e:
                return self._json({"ok": False, "error": str(e)})
        if self.path == "/api/ollama":
            return self._json({"models": ollama_models()})
        if self.path == "/api/usage":
            return self._json(_usage_snapshot())
        if self.path == "/api/special/cfg":
            # 没有 memory.json 就按当前默认值生成一份，再读回来（特别模式的滑条用）
            try:
                special_cfg.ensure()
                return self._json({"ok": True, "cfg": special_cfg.current(),
                                   "ctx_max": special_cfg.CTX_MAX,
                                   "defaults": special_cfg.defaults()})
            except Exception as e:
                return self._json({"ok": False, "error": str(e)})
        if self.path == "/api/special/name":
            # 对话框左上角显示的名字（当前角色的 name.txt）
            try:
                return self._json({"ok": True, "name": special_cfg.get_name()})
            except Exception as e:
                return self._json({"ok": False, "error": str(e)})
        if self.path == "/api/special/status":
            # 角色资源接线：现在实际生效的是哪一份（没导入就是 null）
            try:
                return self._json({"ok": True, "files": char_res.status()})
            except Exception as e:
                return self._json({"ok": False, "error": str(e)})
        if self.path.startswith("/api/character"):
            # 多角色：列表 / 立绘索引（立绘图片本身走 translate_path 的静态分支）
            done = self._character_get()
            if done is not None:
                return done
        if self.path.startswith("/api/scene/list"):
            # 背景图目录（调试用；前端直接读 assets/cg/index.json，不依赖这个接口）
            return self._json(scene_cg.catalog_public())
        if self.path.startswith("/api/scene/match"):
            # 试匹配：/api/scene/match?q=大学图书馆（黄昏）&prev=咖啡厅（白天）
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            scene = (q.get("q") or [""])[0]
            prev = (q.get("prev") or [""])[0]
            try:
                return self._json({"ok": True, "query": scene, "prev": prev,
                                   "hit": scene_cg.match(scene, prev)})
            except Exception as e:
                return self._json({"ok": False, "error": str(e)})
        if self.path.startswith("/api/price/estimate"):
            # 特别模式的「每轮费用预估」：和 /api/usage 同一张价目表、同一套高峰判断
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            try:
                tokens = int((q.get("tokens") or ["0"])[0] or 0)
                out = int((q.get("out") or ["0"])[0] or 0)
            except ValueError:
                return self._json({"ok": False, "error": "tokens / out 必须是数字"}, 400)
            model = ((q.get("model") or [""])[0].strip()
                     or (cloud_cfg() or {}).get("model") or DEFAULT_MODEL)
            try:
                d = _pricing.estimate(model, tokens, completion_tokens=out)
                d["ok"] = True
                return self._json(d)
            except Exception as e:
                return self._json({"ok": False, "error": str(e)})
        if self.path.startswith("/api/price"):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            m = (q.get("model") or [""])[0]
            try:
                p, src = _pricing.prices_for(m)
                return self._json({"ok": True, "model": m, "price": p, "source": src,
                                   "peak": _pricing.is_peak(),
                                   "currency": _pricing.currency()})
            except Exception as e:
                return self._json({"ok": False, "error": str(e)})
        if self.path == "/api/prefs":
            return self._json({"ok": True, "prefs": prefs_load()})
        if self.path == "/api/memory":
            # 记忆书查看（常驻设定 + 自动摘要条目）
            return self._memory_get()
        if self.path == "/api/modelcfg":
            return self._json(model_cfg_all())
        if self.path.startswith("/api/session/load"):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            slot = (q.get("slot") or ["auto"])[0]
            d = session_load(slot)
            if not isinstance(d, dict) or not d.get("ok"):
                # 读不了（坏档 / 来自更新版本）：别动记忆书和关系阶段，把错误原样交给前端
                err = (d or {}).get("error") if isinstance(d, dict) else "load_failed"
                log("存档：读 %s 失败（%s）" % (slot, err))
                if isinstance(d, dict):
                    d.pop("memory", None)
                    return self._json(d)
                return self._json({"ok": False, "error": "load_failed"})
            if d.get("migrated_from") is not None:
                log("存档：%s 从 v%s 迁移到 v%s"
                    % (slot, d.get("migrated_from"), d.get("schema_version")))
            # 读档：存档里的记忆书一次性复制进运行时缓存（cache/memory_runtime.json），
            # 之后整局都只读缓存 —— 不再区分"存档记忆书 / 缓存记忆书"两套。
            try:
                info = _memory.seed_from_save((d or {}).get("memory"), slot)
                if info["enabled"]:
                    log("记忆书：读档 %s → 缓存 %d 条（覆盖到第 %d 条）"
                        % (slot, info["entries"], info["covered"]))
                else:
                    log("记忆书：读档 %s 没有记忆书，缓存已清空" % slot)
            except Exception as e:
                log("记忆书：读档恢复失败 %s" % e)
            try:
                persona_stage.reset()          # 关系阶段按读进来的历史重新判断
            except Exception as e:
                log("人设阶段：读档重置失败 %s" % e)
            try:
                # 成人场面：按存档里的历史重新判定（清空会让 H 存档首播变平静，
                # 沿用旧状态又会让日常存档带着 H 语气）
                import hscene as _hsc
                _msgs = (d or {}).get("messages") or (d or {}).get("history") or []
                _hsc.reset()
                _is = _hsc.is_intimate(_msgs)
                _hsc.set_last(_is)
                log("场面：读档 %s 判定为%s" % (slot, "成人场面" if _is else "普通场面"))
            except Exception as e:
                log("场面判定：读档重算失败 %s" % e)
            if isinstance(d, dict):
                d.pop("memory", None)          # 记忆书对前端不可见
            return self._json(d)
        if self.path.startswith("/api/session/list"):
            return self._json(session_list())
        if self.path.startswith("/api/session/thumb"):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            data = session_thumb((q.get("slot") or [""])[0])
            if not data:
                return self._json({"ok": False, "error": "no thumb"}, 404)
            try:
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "public, max-age=600")
                self.end_headers()
                self.wfile.write(data)
            except Exception:
                pass
            return
        if self.path.startswith("/api/text"):
            return self._text_img()
        return super().do_GET()

    def do_POST(self):
        self._req_start = time.time()
        if self.path.startswith("/api/update/apply"):
            # 一键更新：整份代码取最新，不走补丁链（漏几版都没关系）
            return self._update_apply()
        if self.path.startswith("/api/choice"):
            # 选择分支：让模型基于最近对话给 2~5 个玩家可选项
            return self._choice()
        if self.path.startswith("/api/hotreload"):
            return self._hotreload_post()
        if self.path.startswith("/api/cleanup/"):
            # #17 数据清理入口：清缓存 / 导出数据 / 删角色（删角色要二次确认）
            if self.path.startswith("/api/cleanup/cache"):
                return self._cleanup_cache()
            if self.path.startswith("/api/cleanup/export"):
                return self._cleanup_export()
            if self.path.startswith("/api/cleanup/character"):
                return self._cleanup_character()
        if self.path == "/api/prefs":
            try:
                body = self._body()
            except Exception as e:
                return self._json({"ok": False, "error": str(e)}, 400)
            if not isinstance(body, dict):
                return self._json({"ok": False, "error": "bad prefs"}, 400)
            return self._json({"ok": prefs_save(body)})
        if self.path == "/api/modelcfg":
            return self._modelcfg()
        if self.path == "/api/special/cfg":
            try:
                body = self._body()
            except Exception as e:
                return self._json({"ok": False, "error": "bad body: %s" % e}, 400)
            return self._json(special_cfg.save(body))
        if self.path == "/api/special/name":
            try:
                body = self._body()
            except Exception as e:
                return self._json({"ok": False, "error": "bad body: %s" % e}, 400)
            return self._json(special_cfg.set_name((body or {}).get("name")))
        if self.path == "/api/character/switch":
            try:
                body = self._body()
            except Exception as e:
                return self._json({"ok": False, "error": "bad body: %s" % e}, 400)
            res = characters.switch((body or {}).get("id") or "")
            return self._json(self._after_switch(res))
        if self.path == "/api/character/pick":
            # 弹系统的文件夹选择窗口，从总角色资源文件夹里挑一个角色
            try:
                res = characters.pick_folder()
            except Exception as e:
                res = {"ok": False, "error": str(e)}
            return self._json(self._after_switch(res))
        if self.path == "/api/character/new":
            try:
                body = self._body()
            except Exception:
                body = {}
            res = characters.create((body or {}).get("name") or "")
            return self._json(self._after_switch(res))
        if self.path == "/api/charpack/export":
            # 角色包导出：先弹「选目录」，再打包（都在这一个请求里做完）
            return self._charpack_export()
        if self.path == "/api/charpack/import":
            # 角色包导入：弹「选文件」，解成 characters/ 下的新角色（不自动切换）
            return self._charpack_import()
        if self.path == "/api/memory/delete":
            # 删掉运行时记忆书里的一条（不动存档里的快照）
            return self._memory_delete()
        if self.path.startswith("/api/special/upload"):
            # 特别模式的角色资源：原始字节流，文件名走 query（前端 encodeURIComponent）
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            kind = (q.get("kind") or [""])[0]
            name = (q.get("name") or [""])[0]
            try:
                n = int(self.headers.get("Content-Length", 0) or 0)
                if n > special_files.MAX_BYTES:
                    return self._json({"ok": False, "error": "文件太大（上限 25MB）"}, 413)
                data = self.rfile.read(n) if n > 0 else b""
            except Exception as e:
                return self._json({"ok": False, "error": str(e)}, 400)
            return self._json(special_files.save_upload(kind, name, data))
        if self.path.startswith("/api/special/open"):
            # 特别模式「查看文件」：在资源管理器里打开这一类资源所在的目录
            try:
                body = self._body()
            except Exception as e:
                return self._json({"ok": False, "error": "bad body: %s" % e}, 400)
            return self._json(special_files.open_folder(((body or {}).get("kind") or "").strip()))
        if self.path.startswith("/api/session/save"):
            try:
                body = self._body()
            except Exception as e:
                return self._json({"ok": False, "error": str(e)}, 400)
            if not isinstance(body, dict):
                return self._json({"ok": False, "error": "bad body"}, 400)
            # 记忆书：把运行时缓存整份快照进存档。不在这里现跑总结 ——
            # 总结只在对话超过阈值后按 repack_turns 做（见 _memory_context），
            # 存档只是一次拷贝，所以每轮自动存档也不会额外调用模型。
            r = _memory.runtime()
            entries = list(r.get("entries") or [])
            if entries:
                body["memory"] = {
                    "enabled": True,
                    "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "covered_upto": int(r.get("covered") or 0),
                    "entries": entries,
                }
            return self._json(session_save(body))
        if self.path == "/api/chat":
            return self._chat()
        self.send_error(404)

    def _body(self):
        n = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(n).decode("utf-8"))


_models_cache = {"t": 0, "v": []}


def ollama_models():
    now = time.time()
    if now - _models_cache["t"] < 10:
        return _models_cache["v"]
    try:
        with urllib.request.urlopen(OLLAMA + "/api/tags", timeout=3) as r:
            d = json.loads(r.read().decode("utf-8"))
        v = [m["name"] for m in d.get("models", [])]
    except Exception:
        v = []
    _models_cache.update({"t": now, "v": v})
    return v


def _warm_font():
    try:
        import sub_render
        n = sub_render.atlas_coverage()
        log("字模预热完成: %d 个字" % n)
    except Exception as e:
        log("字模预热失败: %s" % e)


threading.Thread(target=_warm_font, daemon=True).start()
# 剧本检索索引预热（12MB，建索引约 1 秒）：别让第一句相关的话干等
if STORY is not None:
    threading.Thread(target=STORY.preload, daemon=True).start()
if not NO_MODELS:
    threading.Thread(target=lambda: tts_client.start_translate(), daemon=True).start()
if not NO_MODELS:
    threading.Thread(target=lambda: tts_client.start(), daemon=True).start()
applog.start_heartbeat(30)
try:
    _cc0 = cloud_cfg()
    _eff = (_cc0.get("model") if _cc0 else DEFAULT_MODEL) or DEFAULT_MODEL
    _where = ("云端 " + (_cc0.get("base_url") or "")) if _cc0 else ("本地 " + OLLAMA)
except Exception:
    _eff, _where = DEFAULT_MODEL, "本地 " + OLLAMA
# 以前这里印的是 DEFAULT_MODEL（本地模型名），用云端时看着像「在用本地模型」，误导
applog.log("程序启动: 安全模式=%s 语音依赖=%s 对话模型=%s（%s）"
           % (NO_MODELS, tts_client.available(), _eff, _where))


class _Server(ThreadingHTTPServer):
    """Windows 上 SO_REUSEADDR 允许**两个进程同时绑同一个端口**：
    第二个进程以为自己起来了，但连接都被前一个吃掉 —— 表现就是
    「--serve-only 启动后进程活着，端口却不响应」。这里必须关掉。"""
    allow_reuse_address = False


def serve(port_holder, prefer_port=None):
    ports = ([prefer_port] if prefer_port else []) + [8771, 8772, 0]
    srv = None
    for p in ports:
        try:
            srv = _Server(("127.0.0.1", p), Handler)
            break
        except OSError:
            continue
    if srv is None:
        srv = _Server(("127.0.0.1", 0), Handler)
    port_holder.append(srv.server_address[1])
    srv.serve_forever()


def serve_only(prefer_port=None):
    """只起 HTTP 服务，不创建窗口。

    用途：只改后端 / 用 curl 调接口 / 在没有界面素材的机器上调试。
    启动后访问 http://127.0.0.1:<端口>/api/ping 验证。
    """
    os.environ.setdefault("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS",
                          "--autoplay-policy=no-user-gesture-required")
    runstate.install_hooks()
    runstate.begin(APP_NAME, _VERSION)
    _log_maintenance()
    _write_asset_report()
    _hot_reload_setup()
    _memory.clear_runtime()          # 后端模式也当作一次"新启动"

    port_holder = []
    t = threading.Thread(target=serve, args=(port_holder, prefer_port), daemon=True)
    t.start()
    for _ in range(100):
        if port_holder:
            break
        time.sleep(0.05)

    port = port_holder[0] if port_holder else 0
    if not port:
        log("启动失败：没有拿到可用端口（8771/8772/随机端口都绑定失败）")
        return 0
    base = "http://127.0.0.1:%d" % port

    log("=" * 60)
    log("  后端模式（不开窗口）")
    log("  %s" % base)
    log("")
    log("  自检      GET  %s/api/ping" % base)
    log("  对话      POST %s/api/chat" % base)
    log("  文字渲染  GET  %s/api/text?text=测试" % base)
    log("  语音      GET  %s/api/tts?text=テスト" % base)
    log("")
    log("  Ctrl+C 退出")
    log("=" * 60)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log("\n已退出")
    runstate.end()
    return port


def main(prefer_port=None):
    runstate.install_hooks()
    runstate.begin(APP_NAME, _VERSION)
    _log_maintenance()
    _clear_text_cache()
    _write_asset_report()
    _hot_reload_setup()
    _memory.clear_runtime()          # 记忆缓存只活这一次运行
    try:
        import tts_client as _tc
        # 以前这里是 clear_tts_cache()：每次启动清空 → 开一次程序后去回想重放，
        # 每句都要重新合成 3~7 秒（用户报「重放又要等加载」）。
        # 缓存 key 里已含「文本+语种+情绪+参考音频版本」，换音色/改台词天然失效，
        # 所以只需要修剪，不需要清空。
        _tc.prune_tts_cache()
    except Exception:
        pass
    os.environ.setdefault("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS",
                      "--autoplay-policy=no-user-gesture-required")
    import webview

    port_holder = []
    # 窗口模式也认 --port（测试 / 多开时能指定端口，不然只能靠 8771 撞运气）
    t = threading.Thread(target=serve, args=(port_holder, prefer_port), daemon=True)
    t.start()
    for _ in range(100):
        if port_holder:
            break
        time.sleep(0.05)
    bind_port_zero = True
    port = port_holder[0] if port_holder else 0
    try:
        log("窗口模式: http://127.0.0.1:%d" % port, "WIN")
    except Exception:
        pass
    url = "http://127.0.0.1:%d/game/index.html?t=%d" % (port, int(time.time()))

    class _Api:
        """窗口控制（无边框窗口需要自己实现关闭/最小化）"""

        def __init__(self):
            self._window = None

        def win(self, action):
            try:
                if action == "close":
                    self._window.destroy()
                elif action == "min":
                    self._window.minimize()
                return {"ok": True}
            except Exception as e:
                return {"ok": False, "error": str(e)}

        def drag_begin(self):
            """真正在用的拖动：后端开一个很轻的线程，盯着鼠标光标挪窗口。

            为什么不走前端算坐标：
              * JS 的 screenX 是 CSS 像素，GetWindowRect 是物理像素，高 DPI 下必然错位；
              * 每帧一次 JS→Python 桥调用，快拖时调用会排队，窗口追不上光标。
            这里光标位置和窗口位置都是物理像素，同一个坐标系，直接相减，不会有跳和偏。
            """
            try:
                import ctypes
                import threading
                import time as _t
                from ctypes import wintypes
                hw = _win_hwnd()
                if not hw:
                    return {"ok": False, "error": "no hwnd"}
                u = ctypes.WinDLL("user32", use_last_error=True)
                pt = wintypes.POINT()
                u.GetCursorPos(ctypes.byref(pt))
                r = wintypes.RECT()
                u.GetWindowRect(hw, ctypes.byref(r))
                sx, sy, wx, wy = pt.x, pt.y, r.left, r.top
                VK_LBUTTON = 0x01
                SWP = 0x0001 | 0x0004 | 0x0010   # NOSIZE | NOZORDER | NOACTIVATE
                if getattr(self, "_dragging", False):
                    return {"ok": True}          # 已经在拖了，别开第二个循环
                self._dragging = True
                state = {"run": True}

                def loop():
                    try:
                        while state["run"]:
                            if not (u.GetAsyncKeyState(VK_LBUTTON) & 0x8000):
                                break                      # 左键松开就结束
                            p = wintypes.POINT()
                            u.GetCursorPos(ctypes.byref(p))
                            u.SetWindowPos(hw, None, wx + (p.x - sx), wy + (p.y - sy),
                                           0, 0, SWP)
                            _t.sleep(0.008)                # ~120Hz
                    except Exception:
                        pass
                    finally:
                        state["run"] = False
                        self._dragging = False

                threading.Thread(target=loop, daemon=True).start()
                return {"ok": True}
            except Exception as e:
                return {"ok": False, "error": str(e)}

    _api = _Api()
    globals()["_API"] = _api
    window = webview.create_window(
        WINDOW_TITLE, url,
        width=1280, height=720,
        min_size=(1024, 576),
        background_color="#02070f",
        text_select=False,
        frameless=True,
        # 关闭"按住任意位置拖窗口"：否则拖历史界面滑块时整个窗口会跟着动
        easy_drag=False,
        js_api=_api,
    )
    _api._window = window
    global _WINDOW
    _WINDOW = window
    ensure_cloud_cfg()
    apply_model_cfg()
    _write_asset_report()
    # private_mode=True: 每次启动都用全新的 WebView2 数据目录, 彻底避免旧缓存
    webview.start(private_mode=True)
    runstate.end()                   # 正常退出：删掉运行标记


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="AI 聊天界面架构 · 和纱")
    ap.add_argument("--serve-only", action="store_true",
                    help="只起 HTTP 服务，不创建窗口（调后端用）")
    ap.add_argument("--port", type=int, default=None,
                    help="指定端口（默认 8771，占用则自动换）")
    args = ap.parse_args()

    if args.serve_only:
        serve_only(args.port)
    else:
        main(args.port)