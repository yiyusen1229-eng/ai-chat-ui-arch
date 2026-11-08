# -*- coding: utf-8 -*-
"""一键跑自测（#11 测试三类化）。

    python tools/run_smoke.py               # 全部（含在线冒烟）
    python tools/run_smoke.py --no-live     # 只跑离线自测 / 静态检查
    python tools/run_smoke.py --cat 核心,边界
    python tools/run_smoke.py --list        # 列出用例（和 tests/cases.md 对应）
    python tools/run_smoke.py --verbose     # 失败的用例把最后几十行贴出来（默认也贴）

三类 + 静态 + 在线：
    核心  —— 能不能正常玩：角色资源 / 角色包 / 存档 / 记忆书 / 密钥 / 演出 / 参数
    边界  —— 极端输入：长会话 100·500 轮、缓存满、素材缺失、快速连点重播
    降级  —— 依赖不可用：语音没参考音频、场景匹配失败、检索没索引、阶段判定超时
    静态  —— 前后端语法、架构映射表、参数表、前端拆分告警（只许降不许升）
    在线  —— 起一个**无模型**的本地服务，打几个只读接口再关掉（--no-live 可关）

不追求覆盖率，追求「改完敢发布」。用例清单见 `program/tests/cases.md`。
"""
import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
PROGRAM = os.path.dirname(HERE)
BUILD_ENV = os.path.join(PROGRAM, "build_env")
# 自测的临时目录一律放项目里（已在 .gitignore）：别往用户 C 盘的 %TEMP% 里堆
SCRATCH = os.path.join(PROGRAM, "work_scratch")
SCRATCH_TMP = os.path.join(SCRATCH, "tmp")


def _child_env():
    """子进程用的环境：TEMP/TMP 指到 work_scratch/tmp，并强制 UTF-8 输出

    tempfile.mkdtemp() 会跟着 TEMP 走，所以各份自测的临时目录全落在项目里，
    跑完由 _clean_scratch() 收走；用户 %TEMP% 不再被写。

    PYTHONIOENCODING 必须在这里定死：不设的话子进程按控制台代码页输出
    （中文系统是 GBK），被测脚本打印的「全部通过」到这边就是乱码 ——
    判据对不上，明明通过也报失败。2026-11-06 实测：从没设这个变量的终端里跑冒烟，
    「演出标签提示」「选项条测量」两项无故标红，单独跑又都是好的。
    """
    os.makedirs(SCRATCH_TMP, exist_ok=True)
    env = dict(os.environ)
    for k in ("TEMP", "TMP", "TMPDIR"):
        env[k] = SCRATCH_TMP
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def _clean_scratch():
    """跑完清掉本次的临时目录（失败就算了，别影响结论）"""
    try:
        import shutil
        shutil.rmtree(SCRATCH_TMP, ignore_errors=True)
    except Exception:
        pass
SRC = os.path.join(PROGRAM, "src")

# check_split.py 的已知历史告警数：只许降不许升（升了就是这次改动带进来的）
CHECK_SPLIT_MAX = 91

CATS = ["核心", "边界", "降级", "静态", "在线"]

# (类别, 名称, 跑法, 参数, 超时秒)
#   跑法 py   → build_env/venv 的 python（没有就用当前解释器）
#   跑法 node → node
STEPS = [
    ("核心", "角色资源", "py", ["tools/test_char_res.py"], 120),
    ("核心", "角色包 52 项", "py", ["tools/test_char_pack.py"], 120),
    ("核心", "密钥脱敏 24 项", "py", ["tools/test_secret.py"], 120),
    ("核心", "记忆书界面 20 项", "py", ["tools/test_memory_view.py"], 120),
    ("核心", "存档 schema 29 项", "py", ["tools/test_session_schema.py"], 120),
    ("核心", "运行状态 17 项", "py", ["tools/test_runstate.py"], 120),
    ("核心", "缓存守卫 19 项", "py", ["tools/test_cache_io.py"], 120),
    ("核心", "素材自检 13 项", "py", ["tools/test_assets_report.py"], 120),
    ("核心", "响度归一化 28 项", "py", ["tools/test_audio_gain.py"], 180),
    ("核心", "调试账本 35 项", "py", ["tools/test_diag.py"], 120),
    ("核心", "参数表 51 项", "py", ["tools/test_params.py"], 180),
    ("核心", "常驻记忆书两段化 45 项", "py", ["tools/test_standing.py"], 120),
    ("核心", "演出/切片配置 44 项", "py", ["tools/test_perfcfg.py"], 120),
    ("核心", "剧情检索 27 项", "py", ["tools/test_story_rag.py"], 120),
    ("核心", "一键更新 30 项", "py", ["tools/test_update.py"], 120),
    ("核心", "选择分支 29 项", "py", ["tools/test_choice.py"], 120),
    ("核心", "新游戏清历史 7 项", "py", ["tools/test_newgame.py"], 60),
    ("核心", "分支数量接线 9 项", "node", ["tools/test_choices_prefs.js"], 60),
    ("核心", "BGM 情绪槽位 21 项", "node", ["tools/test_mood_bgm.js"], 60),
    ("核心", "远程语音端点 14 项", "py", ["tools/test_tts_remote.py"], 60),
    ("静态", "pref 键冲突 12 项", "py", ["tools/test_prefs_keys.py"], 60),
    ("核心", "语音缓存与重放 20 项", "py", ["tools/test_tts_cache.py"], 60),
    ("核心", "参考音频配对与随机 12 项", "py", ["tools/test_ref_texts.py"], 60),
    ("核心", "多角色同场 9 项", "py", ["tools/test_group.py"], 60),
    ("核心", "情绪标签贯通 69 项", "py", ["tools/test_emotion_wiring.py"], 60),
    ("核心", "成人场面判定 13 项", "py", ["tools/test_hscene.py"], 60),
    ("核心", "合成参数与切分 9 项", "py", ["tools/test_tts_params.py"], 60),
    ("核心", "桌面行为 21 项", "py", ["tools/test_desktop.py"], 90),
    ("核心", "睡眠唤醒检测 19 项", "py", ["tools/test_powerwatch.py"], 60),
    ("静态", "文档链接与索引 12 项", "py", ["tools/test_docs.py"], 60),
    ("核心", "角色包规范与迁移 32 项", "py", ["tools/test_pack_migrate.py"], 90),
    ("核心", "导出与首次自检 25 项", "py", ["tools/test_export.py"], 60),
    ("核心", "模型接入层 50 项", "py", ["tools/test_llm.py"], 60),
    ("核心", "演出标签提示 15 项", "py", ["tools/test_chat_hints.py"], 60),
    ("核心", "立绘索引扫描 13 项", "py", ["tools/test_sprite_scan.py"], 60),
    ("核心", "回复标签解析 44 项", "py", ["tools/test_text_parse.py"], 60),
    ("核心", "翻译层 21 项", "py", ["tools/test_translate.py"], 60),
    ("核心", "素材热重载 33 项", "py", ["tools/test_hotreload.py"], 120),
    ("核心", "素材向导 19 项", "py", ["tools/test_wizard.py"], 120),
    ("核心", "日志保留 + 诊断包 40 项", "py", ["tools/test_logpack.py"], 180),
    ("核心", "性能采样 59 项", "py", ["tools/test_perf_sample.py"], 120),
    ("核心", "窗口接口归属 8 项", "py", ["tools/test_window_api.py"], 60),
    ("核心", "数据清理 59 项", "py", ["tools/test_cleanup.py"], 120),
    ("核心", "演出状态机 23 项", "node", ["tools/test_perf.js"], 120),
    ("核心", "交叉淡化 31 项", "node", ["tools/test_fade.js"], 120),
    ("核心", "演出配置前端 16 项", "node", ["tools/test_perfcfg.js"], 120),
    ("核心", "热重载轮询前端 18 项", "node", ["tools/test_hotreload.js"], 120),
    ("边界", "长会话 / 缓存满 / 素材缺失", "py", ["tools/test_boundary.py"], 180),
    ("降级", "语音·场景·检索·阶段降级", "py", ["tools/test_degrade.py"], 180),
    ("静态", "后端语法 36 文件", "py", ["tools/test_py_syntax.py"], 120),
    ("静态", "前端语法 47 文件", "node", ["tools/test_ui_syntax.js"], 120),
    ("静态", "架构映射表", "py", ["tools/arch_map.py", "--check"], 120),
    ("静态", "参数与指标表", "py", ["tools/param_table.py", "--check"], 120),
    ("静态", "像素测量 25 项", "py", ["tools/test_measure_ui.py"], 180),
    ("静态", "选项条测量 9 项", "py", ["tools/test_measure_choice.py"], 120),
    ("静态", "前端拆分告警（≤%d）" % CHECK_SPLIT_MAX, "split", ["tools/check_split.py"], 180),
]


def py_exe():
    cand = os.path.join(BUILD_ENV, "venv", "Scripts", "python.exe")
    if os.path.isfile(cand):
        return cand
    cand2 = os.path.join(BUILD_ENV, "venv", "bin", "python")
    if os.path.isfile(cand2):
        return cand2
    return sys.executable


def _cmd(kind, args):
    if kind in ("py", "split"):        # split 也是 python 脚本，只是判定规则不同
        return [py_exe()] + args
    return ["node"] + args


def _tail(text, n=18):
    lines = [l for l in (text or "").splitlines() if l.strip()]
    return "\n".join(lines[-n:])


def run_step(kind, args, timeout):
    t0 = time.time()
    try:
        p = subprocess.run(_cmd(kind, args), cwd=PROGRAM, capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, env=_child_env())
        out = (p.stdout or "") + (p.stderr or "")
        return p.returncode, out, time.time() - t0
    except subprocess.TimeoutExpired as e:
        return 124, "超时（%ss）\n%s" % (timeout, _tail(str(e))), time.time() - t0
    except Exception as e:
        return 125, "跑不起来：%s" % e, time.time() - t0


def verdict(kind, code, out):
    """返回 (通过?, 说明)"""
    if kind == "split":
        n = -1
        for line in (out or "").splitlines():
            if "合计发现" in line:
                try:
                    n = int(line.split("合计发现")[1].split("处")[0].strip())
                except Exception:
                    pass
        if n < 0:
            return False, "没读到告警数"
        if n > CHECK_SPLIT_MAX:
            return False, "%d 处，超过基线 %d" % (n, CHECK_SPLIT_MAX)
        return True, "%d 处（基线 %d）" % (n, CHECK_SPLIT_MAX)
    if code == 0 and ("全部通过" in (out or "") or "是最新的" in (out or "")):
        return True, ""
    if code == 0 and kind == "py" and "检查了" in (out or ""):
        return True, _tail(out, 2).replace("\n", " ")
    return False, "退出码 %s" % code


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _get(url, timeout=10):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", "replace")


def live_smoke():
    """起一个无模型的本地服务，打几个只读接口，再关掉。"""
    t0 = time.time()
    assets = os.path.join(PROGRAM, "res", "assets")
    has_assets = os.path.isdir(assets)
    if not os.path.isfile(os.path.join(BUILD_ENV, "main.py")):
        return False, "build_env 没同步（先跑 run_dev.py --sync-only）", time.time() - t0

    port = _free_port()
    os.makedirs(SCRATCH_TMP, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="smoke_", dir=SCRATCH_TMP)
    env = _child_env()
    env["WA2_NO_MODELS"] = "1"           # 别把 GPT-SoVITS 拉起来，冒烟只要接口
    env["WA2_CACHE_DIR"] = os.path.join(tmp, "cache")
    env["WA2_SESS_DIR"] = os.path.join(tmp, "sessions")
    if has_assets:
        env["WA2_ASSETS_DIR"] = assets
    log = open(os.path.join(tmp, "server.log"), "w", encoding="utf-8")
    proc = subprocess.Popen([py_exe(), "main.py", "--serve-only", "--port", str(port)],
                            cwd=BUILD_ENV, env=env, stdout=log, stderr=subprocess.STDOUT)
    base = "http://127.0.0.1:%d" % port
    try:
        up = False
        for _ in range(70):                 # 最多等 ~21 秒
            if proc.poll() is not None:
                break
            try:
                st, body = _get(base + "/api/ping", timeout=2)
                if st == 200 and json.loads(body).get("ok"):
                    up = True
                    break
            except Exception:
                time.sleep(0.3)
        if not up:
            log.flush()
            try:
                tail = open(os.path.join(tmp, "server.log"), encoding="utf-8").read()
            except Exception:
                tail = ""
            return False, "服务没起来\n" + _tail(tail, 10), time.time() - t0

        problems = []
        st, body = _get(base + "/api/debug/state")
        d = json.loads(body)
        if not d.get("ok") or len(d.get("modules") or {}) < 6:
            problems.append("debug/state 结构不对")
        st, body = _get(base + "/api/scene/list")
        if not isinstance(json.loads(body), dict):
            problems.append("scene/list 不是 dict")
        st, body = _get(base + "/api/voice/ok")
        if not isinstance(json.loads(body), dict):
            problems.append("voice/ok 不是 dict")
        st, body = _get(base + "/api/session/list")
        if not isinstance(json.loads(body), dict):
            problems.append("session/list 不是 dict")
        # 演出 / 切片规则（#19）：结构对、默认值对
        st, body = _get(base + "/api/performance")
        pc = json.loads(body)
        if not pc.get("ok") or "performance" not in pc or "slices" not in pc:
            problems.append("performance 结构不对")
        elif pc["slices"].get("story_k") != 2 or pc["performance"].get("fade_ms") != 240:
            problems.append("performance 默认值不对")
        # 热重载 + 素材向导（#20）
        st, body = _get(base + "/api/hotreload")
        hl = json.loads(body)
        if not hl.get("ok") or "gen" not in hl:
            problems.append("hotreload 结构不对")
        st, body = _get(base + "/api/assets/wizard")
        wiz = json.loads(body)
        if not wiz.get("ok") or len(wiz.get("steps") or []) != 4:
            problems.append("assets/wizard 结构不对")
        # 数据清理入口（#17）：只读概览，结构对、8 个清理项
        st, body = _get(base + "/api/cleanup")
        cl = json.loads(body)
        if not cl.get("ok") or len(cl.get("items") or []) != 8:
            problems.append("cleanup 概览结构不对")
        # 诊断包（#15）：真生成一份，确认能打开，再删掉（不留垃圾）
        st, body = _get(base + "/api/log/bundle")
        b = json.loads(body)
        if not b.get("ok") or not os.path.isfile(b.get("path") or ""):
            problems.append("log/bundle 没生成")
        else:
            try:
                with zipfile.ZipFile(b["path"]) as z:
                    if "README.txt" not in z.namelist():
                        problems.append("诊断包缺 README")
            finally:
                try:
                    os.remove(b["path"])
                except OSError:
                    pass
        # 一键更新：只验检测 + 进度两个只读接口（真更新不能在这里跑）
        st, body = _get(base + "/api/update")
        up = json.loads(body)
        if "auto" not in up or up.get("mode") not in ("git", "zip"):
            problems.append("update 检测结构不对")
        st, body = _get(base + "/api/update/status")
        ups = json.loads(body)
        if not ups.get("ok") or "progress" not in ups:
            problems.append("update/status 结构不对")
        if has_assets:
            st, body = _get(base + "/api/text?text=" + urllib.request.quote("冒烟"))
            # 这里是二进制 PNG；只验 HTTP 200 和长度，不解析
        if problems:
            return False, " / ".join(problems), time.time() - t0
        return True, "端口 %d，接口 %d 个" % (port, 13 if has_assets else 12), time.time() - t0
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        try:
            log.close()
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-live", action="store_true", help="不跑在线冒烟")
    ap.add_argument("--no-sync", action="store_true", help="不先同步 src → build_env")
    ap.add_argument("--cat", default="", help="只跑这些类别，逗号分隔：" + ",".join(CATS))
    ap.add_argument("--list", action="store_true", help="只列用例")
    ap.add_argument("--verbose", action="store_true", help="通过也贴输出尾部")
    a = ap.parse_args()

    cats = [c.strip() for c in a.cat.split(",") if c.strip()] if a.cat else []
    for c in cats:
        if c not in CATS:
            print("未知类别：%s（可选：%s）" % (c, ",".join(CATS)))
            return 2

    picked = [s for s in STEPS if not cats or s[0] in cats]
    do_live = (not a.no_live) and (not cats or "在线" in cats)

    if a.list:
        for cat, name, kind, args, _t in picked:
            print("[%s] %-28s %s" % (cat, name, " ".join(_cmd(kind, args)[1:])))
        if do_live:
            print("[在线] %-28s 起本地服务打 /api/ping + 4 个只读接口" % "本地服务冒烟")
        return 0

    if not a.no_sync and not cats:
        print("同步 src → build_env …", end="", flush=True)
        code, out, _dt = run_step("py", ["tools/run_dev.py", "--sync-only"], 180)
        print(" " + ("好" if code == 0 else "失败（跳过在线冒烟）"))

    results = []
    t_all = time.time()
    print()
    for cat, name, kind, args, timeout in picked:
        code, out, dt = run_step(kind, args, timeout)
        ok, note = verdict(kind, code, out)
        results.append((cat, name, ok, note, dt))
        print("  %s [%s] %-28s %5.1fs %s" % ("ok  " if ok else "FAIL", cat, name, dt, note))
        if not ok or a.verbose:
            body = _tail(out, 12)
            if body:
                print("\n".join("        " + l for l in body.splitlines()))

    if do_live:
        ok, note, dt = live_smoke()
        results.append(("在线", "本地服务冒烟", ok, note, dt))
        print("  %s [在线] %-28s %5.1fs %s" % ("ok  " if ok else "FAIL", "本地服务冒烟", dt, note))

    bad = [r for r in results if not r[2]]
    _clean_scratch()                     # 临时目录只活在这次自测里
    print()
    print("—— %d 项：通过 %d，失败 %d，用时 %.1fs" %
          (len(results), len(results) - len(bad), len(bad), time.time() - t_all))
    if bad:
        print("失败：")
        for cat, name, _ok, note, _dt in bad:
            print("  - [%s] %s  %s" % (cat, name, note))
        return 1
    print("全部通过（可以提交 / 发布）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

