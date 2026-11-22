# -*- coding: utf-8 -*-
"""选择分支的自测：
  · api_choice.py —— 选项解析 / 清洗 / 数量裁剪
  · 存档带上「还没选的分支」后还能原样读回来（sessions.py 的 meta.choice）
不联网。

跑法：
    cd program
    build_env/venv/Scripts/python.exe tools/test_choice.py
"""
import os
import shutil
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
PROGRAM = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PROGRAM, "src"))

# 这个自测**不能联网**：数据根指到临时空目录，保证「没配云端模型」那条分支成立。
# （以前数据根是 program/，那里没有 config.json 所以碰巧不联网；
#   现在源码树跑也优先用 build_env 的数据，那里有真云端配置，会真的发请求。）
_DATA_TMP = tempfile.mkdtemp(prefix="wa2_choice_data_")
os.environ["WA2_DATA"] = _DATA_TMP

import api_choice as C  # noqa: E402
import sessions as S  # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    print(("  ok   " if cond else "  FAIL ") + name +
          (("  " + str(extra)[:200]) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def main():
    # 1) 清洗：编号 / 项目符号 / 引号
    check("去掉 1. 编号", C.clean_option("1. 放开手") == "放开手", C.clean_option("1. 放开手"))
    check("去掉 1、编号", C.clean_option("２、把脸背过去") == "把脸背过去", C.clean_option("２、把脸背过去"))
    check("去掉 - 项目符号", C.clean_option("- 转身走开") == "转身走开", C.clean_option("- 转身走开"))
    check("去掉 ①", C.clean_option("① 沉默") == "沉默", C.clean_option("① 沉默"))
    check("去掉引号", C.clean_option("「放开手」") == "放开手", C.clean_option("「放开手」"))
    check("空行返回空", C.clean_option("   ") == "", repr(C.clean_option("   ")))

    # 2) 解析：多行 + 去重 + 截断
    raw = "\n".join([
        "1. 放开手",
        "2. 把脸背过去",
        "3. 什么也不做",
        "4. 笑着说「我才不放手」",
    ])
    got = C.parse_options(raw, 2)
    check("按 count 截断", len(got) == 2, got)
    check("顺序不变", got == ["放开手", "把脸背过去"], got)
    got3 = C.parse_options(raw, 3)
    check("count=3 时给 3 个", len(got3) == 3, got3)

    got_dup = C.parse_options("1. 放开手\n2. 放开手\n3. 靠过去", 3)
    check("重复选项去重", got_dup == ["放开手", "靠过去"], got_dup)

    check("空输出给空列表", C.parse_options("", 2) == [])
    check("太短的行丢掉", C.parse_options("1. 好\n2. 放开手\n3. 靠过去", 3) == ["放开手", "靠过去"],
          C.parse_options("1. 好\n2. 放开手\n3. 靠过去", 3))
    check("太长的行丢掉",
          C.parse_options("1. " + "长" * 40 + "\n2. 放开手\n3. 靠过去", 3) == ["放开手", "靠过去"])
    check("带解释的行仍取前半（不炸）", isinstance(C.parse_options("1. 放开手（然后离开）", 2), list))

    # 3) count 边界
    check("count 小于下限 → 2", len(C.parse_options(raw, 0)) == 2, C.parse_options(raw, 0))
    check("count 大于上限 → 5", len(C.parse_options(raw, 99)) == 4, C.parse_options(raw, 99))
    check("count 给字符串也认", len(C.parse_options(raw, "3")) == 3)

    # 4) 最近对话压缩
    msgs = [{"role": "user", "content": "你还记得那天吗"},
            {"role": "assistant", "content": "「……记得」"},
            {"role": "user", "content": "那我们"},
            {"role": "assistant", "content": ""}]
    ctx = C._recent(msgs)
    check("上下文里有玩家和她", "玩家：" in ctx and "和纱：" in ctx, ctx)
    check("空内容不占行", ctx.count("\n") == 2, ctx)
    check("超过 8 条只留最后 8 条",
          C._recent([{"role": "user", "content": "第%d条" % i} for i in range(20)]).count("\n") == 7,
          C._recent([{"role": "user", "content": "第%d条" % i} for i in range(20)]))

    # 5) 没有云端配置时给明确错误（不抛异常）
    opts, err = C.generate([{"role": "user", "content": "在吗"}], 2)
    check("没有云端配置 → 返回错误而不是抛", isinstance(opts, list) and isinstance(err, str),
          (opts, err))
    check("错误信息可读", bool(err), err)

    # 6) 存档里的「还没选的分支」：进得去、出得来、老档不受影响
    tmp = tempfile.mkdtemp(prefix="wa2_choice_")
    old_dir = S.SESS_DIR
    S.SESS_DIR = tmp
    try:
        msgs = [{"role": "user", "content": "你还记得那天吗"},
                {"role": "assistant", "content": "「……记得」"}]
        r = S.session_save({"slot": "1", "messages": msgs,
                            "meta": {"bg": "classroom",
                                     "choice": ["放开手", "把脸背过去"]}})
        check("存档写成功", r.get("ok") is True, r)
        d = S.session_load("1")
        check("读档成功", d.get("ok") is True, d)
        check("meta.choice 原样读回",
              (d.get("meta") or {}).get("choice") == ["放开手", "把脸背过去"],
              (d.get("meta") or {}).get("choice"))
        check("其余 meta 没被挤掉", (d.get("meta") or {}).get("bg") == "classroom",
              (d.get("meta") or {}).get("bg"))
        S.session_save({"slot": "2", "messages": msgs, "meta": {"bg": "classroom"}})
        m2 = S.session_load("2").get("meta") or {}
        check("没有 choice 的存档不凭空长出一个", "choice" not in m2, m2)
        # 坏数据不该让读档炸（前端会过滤，但后端也不能挂）
        S.session_save({"slot": "3", "messages": msgs,
                        "meta": {"choice": {"oops": 1}}})
        check("meta.choice 类型乱写也能读出来（不抛）",
              S.session_load("3").get("ok") is True)
        # 空数组：前端当没有
        S.session_save({"slot": "4", "messages": msgs, "meta": {"choice": []}})
        check("空 choice 存得住（由前端判空）",
              (S.session_load("4").get("meta") or {}).get("choice") == [])
    finally:
        S.SESS_DIR = old_dir
        shutil.rmtree(tmp, ignore_errors=True)

    # 7) 同一对话点不重复生成：have 够了就不调模型，不够只补差额（2026-11-22 加）
    MSG = [{"role": "user", "content": "你还记得那天吗"},
           {"role": "assistant", "content": "「……记得」"}]
    check("count 夹到 2~5", (C.clamp_count(1), C.clamp_count(9), C.clamp_count("3")) == (2, 5, 3),
          (C.clamp_count(1), C.clamp_count(9), C.clamp_count("3")))

    # 数据根没有云端配置：真调模型会返回「没配云端模型」，所以 err=="" 就证明没发请求
    opts, err = C.generate(MSG, 2, have=["放开手", "把脸背过去"])
    check("已有 2 条、要 2 条 → 不调模型", opts == ["放开手", "把脸背过去"] and err == "", (opts, err))
    opts, err = C.generate(MSG, 2, have=["放开手", "把脸背过去", "沉默", "转身走开"])
    check("数量改小时只取前 count 条", opts == ["放开手", "把脸背过去"] and err == "", (opts, err))
    opts, err = C.generate(MSG, 3, have=["放开手", "把脸背过去"])
    check("不够时确实去调模型（这里没配置 → 报错并把已有带回来）",
          opts == ["放开手", "把脸背过去"] and bool(err), (opts, err))

    check("parse_options 会过滤已有的", C.parse_options("放开手\n靠过去\n沉默", 3,
                                                         avoid=["放开手"]) == ["靠过去", "沉默"])
    check("已有项的省略号/句号差别也算同一条",
          C.parse_options("放开手\n靠过去", 2, avoid=["放开手。"]) == ["靠过去"])

    # 增量提示词要点名别重复
    _p = C.prompt_for(3, ["放开手", "把脸背过去"])
    check("增量提示词只要 3 行", "只输出 3 行" in _p, _p[-80:])
    check("增量提示词点名已有选项", "放开手" in _p and "不要重复" in _p, _p[-120:])
    check("没有已有选项时不加这段", "不要重复" not in C.prompt_for(2), C.prompt_for(2))

    # 把 _ask 换成假的：验「只补差额」「回来拼成 count 条」「顺序是旧的在前」
    calls = []
    _orig_ask = C._ask

    def _fake_ask(ctx, need, avoid=None):
        calls.append({"need": need, "avoid": list(avoid or [])})
        if need == 3:
            return "\n".join(["1. 把伞递过去", "2. 沉默", "3. 转身走开"]), ""
        return "1. 新的甲\n2. 新的乙", ""

    try:
        C._ask = _fake_ask
        opts, err = C.generate(MSG, 5, have=["放开手", "把脸背过去"])
        check("数量 2→5：只要 3 个新选项", calls and calls[0]["need"] == 3, calls)
        check("数量 2→5：已有选项也告诉模型", calls and calls[0]["avoid"] == ["放开手", "把脸背过去"], calls)
        check("数量 2→5：拼成 5 条且旧的在前",
              opts == ["放开手", "把脸背过去", "把伞递过去", "沉默", "转身走开"], opts)
        check("数量 2→5：没有重复项", len(set(opts)) == 5, opts)

        calls[:] = []
        opts, err = C.generate(MSG, 2, have=["放开手", "把脸背过去"])
        check("数量 5→2：一个请求都不发", calls == [] and opts == ["放开手", "把脸背过去"],
              (calls, opts))

        calls[:] = []
        opts, err = C.generate(MSG, 4, have=["放开手", "把脸背过去", "把伞递过去", "沉默"])
        check("已有 4 条要 4 条：不发请求", calls == [] and len(opts) == 4, (calls, opts))

        # 模型漏给：补到几条算几条，别把旧的丢了
        C._ask = lambda ctx, need, avoid=None: ("1. 只有一条", "")
        opts, err = C.generate(MSG, 5, have=["放开手", "把脸背过去"])
        check("模型补不满也保留已有 + 新的一条",
              opts[:2] == ["放开手", "把脸背过去"] and len(opts) == 3 and bool(err), (opts, err))
    finally:
        C._ask = _orig_ask

    print()
    if FAILS:
        print("失败 %d 项：%s" % (len(FAILS), " / ".join(FAILS)))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
