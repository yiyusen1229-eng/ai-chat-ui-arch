# -*- coding: utf-8 -*-
"""场景原话的自测（真服务 + 临时存档目录，不花 token）。

跑法：
    cd program
    build_env/venv/Scripts/python.exe tools/test_scene_flow.py

现在的上下文是这么走的（和单角色 /api/chat 一个路子）：
前端每次请求导演都带上 window.__history，后端用 scenelog.rows_from 把它变成
「刚才发生的事」。所以读档、中途打开多角色都不用另外"铺回"。

这个测试钉住：
  1. /api/scene/state 能看当前原话（排查用）
  2. 读档时按存档 messages 铺一次兜底（没带 messages 的调用也有上下文）
  3. 玩家每轮都会清的 /api/play/clear **不清**场景原话；?reset=1 才清
  4. 老的 /api/scene/say、/api/scene/seed 已经删掉（不留下两套上下文来源）
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
PROGRAM = os.path.dirname(HERE)
BUILD = os.path.join(PROGRAM, "build_env")
PORT = 8808
BASE = "http://127.0.0.1:%d" % PORT
FAILS = []


def check(name, cond, extra=""):
    print(("  ok   " if cond else "  FAIL ") + name +
          (("  " + str(extra)[:300]) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def get(path, timeout=10):
    with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def post(path, obj, timeout=20):
    data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    rq = urllib.request.Request(BASE + path, data=data,
                                headers={"Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(rq, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def status_of(path, obj):
    """POST 一个"应该 404"的地址，返回状态码（不抛）"""
    data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    rq = urllib.request.Request(BASE + path, data=data,
                                headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(rq, timeout=10) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0


def main():
    tmp = tempfile.mkdtemp(prefix="wa2_scene_")
    sess_dir = os.path.join(tmp, "sessions")
    os.makedirs(sess_dir, exist_ok=True)
    env = dict(os.environ)
    env["WA2_ASSETS_DIR"] = os.path.join(PROGRAM, "res", "assets")
    env["WA2_NO_MODELS"] = "1"
    env["WA2_SESS_DIR"] = sess_dir
    log = open(os.path.join(tmp, "server.log"), "w", encoding="utf-8")
    proc = subprocess.Popen([os.path.join(BUILD, "venv", "Scripts", "python.exe"),
                             "main.py", "--serve-only", "--port", str(PORT)],
                            cwd=BUILD, env=env, stdout=log, stderr=subprocess.STDOUT)
    try:
        up = False
        for _ in range(80):
            time.sleep(0.5)
            try:
                if get("/api/ping", 2).get("ok"):
                    up = True
                    break
            except Exception:
                pass
        if not up:
            print("服务没起来")
            return 1

        print("[1] 一开始场景原话是空的")
        st = get("/api/scene/state")
        check("只读接口能用", st.get("ok") is True, st)
        check("空场 0 条", st.get("lines") == 0, st)

        print("\n[2] 读档按存档 messages 铺一次兜底")
        post("/api/session/save", {"slot": "1",
                                   "messages": [
                                       {"role": "user", "content": "我问你那天为什么先走。"},
                                       {"role": "assistant", "content": "……そう。", "zh": "……是吗。",
                                        "speaker": "和纱"},
                                       {"role": "assistant", "content": "雪停了。", "narr": True},
                                       {"role": "assistant", "content": "……別に。", "zh": "……没什么。",
                                        "speaker": "和纱"}],
                                   "meta": {"bg": "教室"}})
        d = get("/api/session/load?slot=1")
        check("读档成功", d.get("ok") is True, d.get("error"))
        st = get("/api/scene/state")
        check("铺回 4 条", st.get("lines") == 4, st)
        check("最后一条是那句台词", "没什么" in (st.get("last") or ""), st)
        check("第一条是玩家说的", "先走" in (st.get("first") or ""), st)

        print("\n[3] 清队列不清场景原话，真换场才清")
        clr = post("/api/play/clear", {})
        check("默认不是换场", clr.get("scene_reset") is False, clr)
        check("原话还在", get("/api/scene/state").get("lines") == 4)
        clr2 = post("/api/play/clear?reset=1", {})
        check("报告是换场", clr2.get("scene_reset") is True, clr2)
        check("清空了", get("/api/scene/state").get("lines") == 0)

        print("\n[4] 老的单独记账接口已经删掉（上下文只有一个来源）")
        check("/api/scene/say 不存在", status_of("/api/scene/say", {"text": "x"}) == 404)
        check("/api/scene/seed 不存在", status_of("/api/scene/seed", {"messages": []}) == 404)
    except Exception as e:
        print("异常:", type(e).__name__, e)
        FAILS.append("exception")
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=8)
        except Exception:
            proc.kill()
        log.close()

    print("\n" + ("全部通过" if not FAILS else "失败 %d 项：%s" % (len(FAILS), "、".join(FAILS))))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
