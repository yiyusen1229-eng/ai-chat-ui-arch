# -*- coding: utf-8 -*-
"""存档带场面状态的自测（真服务 + 临时存档目录，不碰真实存档、不花 token）。

跑法：
    cd program
    build_env/venv/Scripts/python.exe tools/test_session_scene.py

验的是「存档记下这一场怎么演的、读档还原」这条链路：
  1. 存档里多了 meta.scene：多角色开关 / 走向 / 长度 / 阵容
  2. 存档结构没变：schema_version 还是 1（只是 meta 多了键）
  3. 读档能把开关和阵容恢复回来（不是"角色对了、场面没了"）
  4. 老存档（没有 meta.scene）读进来不会动当前设置
  5. 存档 / 读档 / 列表接口都还正常
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
PROGRAM = os.path.dirname(HERE)
BUILD = os.path.join(PROGRAM, "build_env")
PORT = 8806
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


def main():
    tmp = tempfile.mkdtemp(prefix="wa2_sess_")
    sess_dir = os.path.join(tmp, "sessions")
    os.makedirs(sess_dir, exist_ok=True)
    env = dict(os.environ)
    env["WA2_ASSETS_DIR"] = os.path.join(PROGRAM, "res", "assets")
    env["WA2_NO_MODELS"] = "1"
    env["WA2_SESS_DIR"] = sess_dir          # 别碰真存档
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

        print("[1] 摆一个「多角色开着、两个人阵容」的场面")
        post("/api/cast/set", {"members": [
            {"id": "和纱", "role": "actor"},
            {"id": "雪菜", "role": "actor"},
            {"name": "你", "role": "player"}]})
        post("/api/direct/options", {"enabled": True, "length": "medium",
                                     "direction": "把三年前的事说开"})
        st = get("/api/direct/status")
        check("阵容三个人", len((st.get("director") or {}).get("cast", {}).get("members") or []) == 3,
              st.get("director"))

        print("\n[2] 存档带上场面")
        r = post("/api/session/save", {"slot": "1",
                                       "messages": [{"role": "user", "content": "嗯"},
                                                    {"role": "assistant", "content": "……そう。",
                                                     "zh": "……是吗。", "speaker": "雪菜"}],
                                        "meta": {"bg": "教室", "outfit": "校服"}})
        check("存进去了", r.get("ok") is True, r)
        path = os.path.join(sess_dir, "1.json")
        check("文件在", os.path.isfile(path), path)
        d = json.load(open(path, encoding="utf-8"))
        check("schema_version 没变（只是 meta 多了键）", d.get("schema_version") == 1,
              d.get("schema_version"))
        scene = (d.get("meta") or {}).get("scene") or {}
        check("场景块存在", bool(scene), d.get("meta"))
        check("多角色=开", scene.get("enabled") is True, scene)
        check("走向存下来了", scene.get("direction") == "把三年前的事说开", scene)
        check("长度存下来了", scene.get("length") == "medium", scene)
        cast = scene.get("cast") or []
        check("阵容三个人", len(cast) == 3, cast)
        check("角色有 id、玩家有名字",
              any(m.get("id") == "雪菜" for m in cast) and
              any(m.get("name") == "你" and m.get("role") == "player" for m in cast), cast)

        print("\n[3] 换一套完全不同的场面，再读档")
        post("/api/cast/set", {"members": []})
        post("/api/direct/options", {"enabled": False, "length": "small", "direction": ""})
        st2 = get("/api/direct/status")
        check("当前已清空", not (st2.get("director") or {}).get("enabled"), st2.get("director"))

        d2 = get("/api/session/load?slot=1")
        check("读得出来", d2.get("ok") is True, d2.get("error"))
        check("返回里报告恢复了什么", (d2.get("restored_scene") or {}).get("cast") == 3,
              d2.get("restored_scene"))
        st3 = get("/api/direct/status")["director"]
        check("多角色开关恢复了", st3.get("enabled") is True, st3)
        check("走向恢复了", st3.get("direction") == "把三年前的事说开", st3)
        check("长度恢复了", st3.get("length") == "medium", st3)
        members = (st3.get("cast") or {}).get("members") or []
        check("阵容恢复了", len(members) == 3, members)
        check("阵容里的 id 对", any(m.get("id") == "雪菜" for m in members), members)

        print("\n[4] 老存档（没有 meta.scene）不动当前设置")
        post("/api/direct/options", {"enabled": False, "direction": ""})
        post("/api/cast/set", {"members": [{"id": "和纱", "role": "actor"}]})
        old = {"schema_version": 1, "slot": "old", "saved_at": "2026-11-01 00:00:00",
               "messages": [{"role": "user", "content": "旧"}, 
                            {"role": "assistant", "content": "……"}],
               "meta": {"bg": "教室"}}
        with open(os.path.join(sess_dir, "old.json"), "w", encoding="utf-8") as f:
            json.dump(old, f, ensure_ascii=False)
        d3 = get("/api/session/load?slot=old")
        check("老档读得出来", d3.get("ok") is True, d3.get("error"))
        check("老档不报告恢复场面", "restored_scene" not in d3, d3.get("restored_scene"))
        st4 = get("/api/direct/status")["director"]
        check("当前设置没被动", st4.get("enabled") is False, st4)
        check("阵容也没被动", len((st4.get("cast") or {}).get("members") or []) == 1,
              (st4.get("cast") or {}).get("members"))

        print("\n[5] 列表接口还正常")
        lst = get("/api/session/list")
        check("列表有两条", len(lst.get("slots") or []) == 2, lst.get("slots"))
        check("列表 schema_version 还是 1", lst.get("schema_version") == 1, lst)
        slots = dict((x.get("slot"), x) for x in (lst.get("slots") or []))
        check("槽位 1 有预览", bool((slots.get("1") or {}).get("preview")), slots.get("1"))
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
