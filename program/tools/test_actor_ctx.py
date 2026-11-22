# -*- coding: utf-8 -*-
"""上下文隔离的自测（不联网、不启动模型/语音、不花 token）。

跑法：
    cd program
    build_env/venv/Scripts/python.exe tools/test_actor_ctx.py

验的是「角色开口时到底能看到什么」这条链路：
  1. 数据目录：一个根，五项环境变量各自能覆盖，默认落在根下面
  2. scenelog：只记播出去的，最近 K 条 / 拼成文字不串行
  3. 播放队列 pop 才记账，clear 会把上一场清掉
  4. speaker.ctx：卡 / 私有记忆 / 关系 / 模型 / 语音都按 id 解析
  5. actor：提示词里真的带上了记忆、最近原话、红线；中文作答判失败
  6. 导演：beats 的红线（ban）能过 `_clean` 并传到角色模型
  7. 导演自己的提示词也带「刚才发生的事」
"""
import json
import os
import sys
import tempfile
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
PROGRAM = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PROGRAM, "src"))

# 先把数据目录指到临时位置，再 import 业务模块（有些模块在 import 时算路径）
_TMP = tempfile.mkdtemp(prefix="wa2_ctx_")
_DATA = os.path.join(_TMP, "data")
os.environ["WA2_USER_DIR"] = _DATA
os.environ["WA2_CACHE_DIR"] = os.path.join(_TMP, "cache")
os.environ["WA2_SESS_DIR"] = os.path.join(_TMP, "sessions")
os.environ["WA2_LOG_DIR"] = os.path.join(_TMP, "logs")
os.environ["WA2_NO_MODELS"] = "1"

import datadir      # noqa: E402
import scenelog     # noqa: E402
import playqueue    # noqa: E402
import speaker      # noqa: E402
import cast         # noqa: E402
import actor        # noqa: E402
import director_gen  # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    print(("  ok   " if cond else "  FAIL ") + name +
          (("  " + str(extra)[:300]) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def make_char(cid, name, card, memory="", relations=None, model=True, voice=True):
    d = os.path.join(_DATA, "characters", cid)
    write(os.path.join(d, "name.txt"), name)
    write(os.path.join(d, "card", "director.md"), card)
    for sub in ("prompt", "worldbook", "memory", "voice", "portrait"):
        os.makedirs(os.path.join(d, sub), exist_ok=True)
    if memory:
        write(os.path.join(d, "memory", "旧事.txt"), memory)
    if relations is not None:
        write(os.path.join(d, "relations.json"), json.dumps(relations, ensure_ascii=False))
    if model:
        write(os.path.join(d, "model.json"), json.dumps({
            "chat": {"connected": True, "endpoint": "http://127.0.0.1:9/v1",
                     "key": "k", "model": "fake-" + cid}
        }, ensure_ascii=False))
    if voice:
        write(os.path.join(d, "voice", "calm", "a.wav"), "RIFF")
    return d


CARD_KS = "嘴上不饶人，心里在意；不会主动示弱，也不会道歉。"


def main():
    print("[1] 数据目录")
    r = datadir.root()
    if os.path.basename(datadir.program_dir()) == "src":
        # 自测把 src 挂进 sys.path：程序目录是源码树，数据根不能是 src。
        # 源码树旁边有 build_env/ 时用它（和 run_dev 起的一致，才会看到真实角色），
        # 没有才退回上一级 program/。
        _prog = os.path.dirname(datadir.program_dir())
        _want = (os.path.join(_prog, "build_env") if os.path.isdir(os.path.join(_prog, "build_env"))
                 else _prog)
        check("源码树不算数据根（优先 build_env）", r == _want, r)
    else:
        check("默认根 = 程序目录", r == datadir.program_dir(), r)
    old = os.environ.get("WA2_DATA")
    os.environ["WA2_DATA"] = os.path.join(_TMP, "moved")
    check("WA2_DATA 换根", datadir.root() == os.path.abspath(os.path.join(_TMP, "moved")),
          datadir.root())
    check("子目录跟着根走",
          datadir.sub("cache") == os.path.join(datadir.root(), "cache"), datadir.sub("cache"))
    os.environ.pop("WA2_DATA", None)
    if old is not None:
        os.environ["WA2_DATA"] = old
    check("具体项环境变量优先",
          datadir.env_or("WA2_CACHE_DIR", "cache") ==
          os.path.abspath(os.environ["WA2_CACHE_DIR"]), datadir.env_or("WA2_CACHE_DIR", "cache"))
    info = datadir.info()
    check("info 报得出根和来源",
          info.get("root") and info["cache"]["source"] == "env:WA2_CACHE_DIR", info)

    # 配置 / 缓存都归到数据根：这里点的是「谁还在自己算路径」
    print("\n[1b] 配置与缓存都归数据根")
    import logpack
    import translate as _tr
    import api_cleanup as _clean
    check("字模缓存在缓存目录下",
          datadir.text_cache() == os.path.join(datadir.cache_dir(), "text_cache"),
          datadir.text_cache())
    check("logpack 读配置也是数据根", logpack._exe_dir() == datadir.root(), logpack._exe_dir())
    check("translate 读 config.json 也是数据根", _tr._src_dir() == datadir.root(), _tr._src_dir())
    check("数据清理面板认同一个根", _clean._data_dir() == datadir.root(), _clean._data_dir())

    print("\n[2] scenelog：只记播出去的")
    scenelog.clear()
    for i in range(10):
        scenelog.add("和纱" if i % 2 == 0 else "", "第%d句" % i, "say" if i % 2 == 0 else "narr")
    check("留最近 K 条", [x["text"] for x in scenelog.recent(3)] == ["第7句", "第8句", "第9句"],
          scenelog.recent(3))
    txt = scenelog.as_text(4)
    check("台词带名字", "和纱：「第8句」" in txt, txt)
    check("旁白不加名字", "第9句" in txt.splitlines()[-1] and "「" not in txt.splitlines()[-1], txt)
    check("空行不记", (scenelog.add("和纱", "   ") or scenelog.stats()["lines"]) == 10)
    scenelog.clear()
    long_text = "啊" * 900
    for _ in range(3):
        scenelog.add("和纱", long_text)
    check("拼文字有上限", len(scenelog.as_text(3, 200)) <= 200, len(scenelog.as_text(3, 200)))
    check("清空", scenelog.clear() == 3 and scenelog.stats()["lines"] == 0)

    # 读档 / 中途打开多角色：用已有历史把「刚才发生的事」铺回来
    scenelog.clear()
    n = scenelog.seed([
        {"role": "user", "content": "我问你那天为什么先走。"},
        {"role": "assistant", "content": "……そう。", "zh": "……是吗。", "speaker": "和纱"},
        {"role": "assistant", "content": "雪停了。", "narr": True},
        {"role": "assistant", "content": "……別に。", "zh": "……没什么。", "speaker": "和纱"},
    ], player="你")
    rows = scenelog.recent(10)
    check("铺回四条", n == 4 and len(rows) == 4, (n, rows))
    check("玩家那条记在玩家名下", rows[0]["speaker"] == "你", rows[0])
    check("角色台词用中文那侧", rows[1]["text"] == "……是吗。" and rows[1]["speaker"] == "和纱", rows[1])
    check("旁白不带名字", rows[2]["text"] == "雪停了。" and rows[2]["speaker"] == "", rows[2])
    check("铺完导演看得到", "先走" in scenelog.as_text(10), scenelog.as_text(10))

    # 读档后要能「接着上文」：窗口给足（不是只给最近 10 条），早先的剧情靠记忆书摘要补
    scenelog.clear()
    for i in range(30):
        scenelog.add("和纱", "第%d句" % i, "say")
    deep = director_gen._user_prompt(0, 7, 2)
    check("导演看得到 30 句之前的话", "第0句" in deep, deep[:300])
    import memory as _mem
    real_rt = _mem.runtime
    _mem.runtime = lambda: {"entries": [{"text": "三年前她在车站等了一夜。"}]}
    try:
        d2 = director_gen._user_prompt(0, 7, 2)
        a2 = actor._user_text(speaker.ctx("和纱"), "说一句", "", "", "", "最近一句")
    finally:
        _mem.runtime = real_rt
    check("导演拿到更早的摘要", "车站等了一夜" in d2, d2[:400])
    check("角色也拿得到", "车站等了一夜" in a2, a2[:400])
    check("没有摘要时不写这一块", "【更早的事（摘要）】" not in director_gen._user_prompt(0, 7, 2))

    # 上下文直接来自 history（和 /api/chat 一个路子）：不再单独记账、不再单独铺回
    msgs = [
        {"role": "user", "content": "我问你那天为什么先走。"},
        {"role": "assistant", "content": "……そう。", "zh": "……是吗。", "speaker": "和纱"},
        {"role": "assistant", "content": "雪停了。", "narr": True},
    ]
    rows2 = scenelog.rows_from(msgs, "你")
    check("历史能变成场景行", len(rows2) == 3 and rows2[1]["speaker"] == "和纱", rows2)
    up3 = director_gen._user_prompt(0, 7, 2, rows2)
    check("导演提示里带上历史", "先走" in up3 and "是吗" in up3, up3[:400])
    check("有旁白就不催画面", "这一拍要画" not in up3, up3[:400])
    up4 = director_gen._user_prompt(0, 7, 2, scenelog.rows_from(msgs[:1], "你"))
    check("最后一条是玩家就提示别代写", "玩家刚说过话" in up4, up4[:400])
    check("rows_from 认中文那侧", scenelog.render(rows2).count("……是吗。") == 1,
          scenelog.render(rows2))

    print("\n[3] 队列：pop 才记账")
    scenelog.clear()
    playqueue.push([{"speaker": "和纱", "cid": "和纱", "text": "先看看", "type": "say",
                     "ja": "……見てみる。", "emotion": "cold"},
                    {"speaker": "", "text": "雪停了。", "type": "narr"}])
    check("push 不记账", scenelog.stats()["lines"] == 0, scenelog.stats())
    head = playqueue.peek() or {}
    check("日语原文能过队列", head.get("ja") == "……見てみる。", head)
    check("情绪能过队列", head.get("emotion") == "cold", head)
    check("角色 id 能过队列", head.get("cid") == "和纱", head)
    playqueue.pop()
    check("pop 记一条", [x["text"] for x in scenelog.recent(3)] == ["先看看"], scenelog.recent(3))
    # 玩家每发一句话都会清一次队列（丢掉没播完的预生成）。以前这里顺手把场景原话也清了，
    # 于是导演和角色每一轮都从零开始 —— 剧情接不上。现在默认只清队列。
    playqueue.clear()
    check("清队列不动场景原话", scenelog.stats()["lines"] == 1, scenelog.stats())
    playqueue.clear(reset_scene=True)
    check("真换场才清原话", scenelog.stats()["lines"] == 0, scenelog.stats())

    print("\n[4] speaker.ctx：按 id 解析")
    make_char("和纱", "和纱", CARD_KS,
              memory="她记得三年前那个冬天，雪很大。",
              relations={"雪菜": "嘴上不承认，其实还气她", "曜子": "不想提她"})
    make_char("雪菜", "雪菜", "温柔，但什么都看在眼里。",
              memory="她跟和纱认识很久了。")
    ctx = speaker.ctx("和纱", present=["和纱", "雪菜", "你"])
    check("卡读到", ctx["card"] == CARD_KS, ctx["card"])
    check("名字读到", ctx["name"] == "和纱", ctx["name"])
    check("私有记忆读到", "三年前" in ctx["memory"], ctx["memory"])
    check("关系表读到", ctx["relations"].get("雪菜"), ctx["relations"])
    check("只注入在场的人", "雪菜" in ctx["relations_text"] and "曜子" not in ctx["relations_text"],
          ctx["relations_text"])
    check("模型按角色", ctx["model"]["model"] == "fake-和纱", ctx["model"])
    check("语音就绪", ctx["has_voice"] is True)
    ctx2 = speaker.ctx("雪菜")
    check("另一个角色不串味", "三年前" not in ctx2["memory"] and ctx2["card"] != CARD_KS,
          ctx2["memory"])
    check("没卡的角色也能取（只是没卡）", speaker.ctx("不存在")["has_card"] is False)

    print("\n[5] actor：提示词里真的带上了")
    cast.set_cast([{"name": "你", "role": "player"},
                   {"id": "和纱", "role": "actor"},
                   {"id": "雪菜", "role": "actor"}])

    # 玩家戏份：自动模式该给玩家写台词，手动模式不该替他写
    up_auto = director_gen._user_prompt(0, 7, 2,
                                        scenelog.rows_from([{"role": "assistant", "content": "「……」",
                                                             "speaker": "和纱"}], "你"), True)
    check("自动模式会给玩家一句", "【给玩家一句】" in up_auto, up_auto[:600])
    up_manual = director_gen._user_prompt(0, 7, 2, [], False)
    check("手动模式不替玩家写", "【玩家自己在说话】" in up_manual, up_manual[:600])
    up_auto2 = director_gen._user_prompt(0, 7, 2,
                                         scenelog.rows_from([
                                             {"role": "assistant", "content": "「……」", "speaker": "和纱"},
                                             {"role": "user", "content": "等你半天了。"}], "你"), True)
    check("玩家刚说完就不再催", "【给玩家一句】" not in up_auto2, up_auto2[:600])

    # 玩家 / 龙套没有语音模型：有台词但不送合成
    beats_clean, dropped = director_gen._clean([
        {"speaker": "你", "type": "say", "text": "等很久了吗？"},
        {"speaker": "和纱", "type": "say", "text": "……别问。"}], 60)
    voices = dict((b["speaker"], b.get("voice")) for b in beats_clean)
    check("玩家台词不送语音", voices.get("你") is False, voices)
    check("角色台词照样送语音", voices.get("和纱") is True, voices)

    scenelog.clear()
    scenelog.add("雪菜", "三年前那场演唱会，你还记得吧。", "say")
    scenelog.add("", "和纱没有说话。", "narr")

    captured = []
    real_open = urllib.request.urlopen
    import translate
    real_zh = translate.to_zh
    translate.to_zh = lambda s: "（译文）" + s

    class _Resp(object):
        def __init__(self, payload):
            self._b = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        def read(self):
            return self._b

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    ANSWER = {"v": "……別に。あんたが決めたことだろ。<emotion>cold</emotion>"}

    def fake_open(req, timeout=0, **kw):
        captured.append(json.loads(req.data.decode("utf-8")))
        return _Resp({"choices": [{"message": {"content": ANSWER["v"]}}],
                      "usage": {"prompt_tokens": 100, "completion_tokens": 20,
                                "total_tokens": 120}})

    urllib.request.urlopen = fake_open
    try:
        r = actor.speak("和纱", "她问你三年前那场演唱会", scene="放学后的音乐室",
                        ban="她绝不会主动道歉")
    finally:
        urllib.request.urlopen = real_open

    sent = captured[-1] if captured else {}
    sys_msg = (sent.get("messages") or [{}])[0].get("content", "")
    user_msg = (sent.get("messages") or [{}, {}])[1].get("content", "")
    check("请求带上了角色卡", CARD_KS in sys_msg, sys_msg[:80])
    check("请求带上了私有记忆", "【你记得的旧事】" in user_msg and "三年前" in user_msg,
          user_msg[:200])
    check("请求带上了最近原话", "【刚才发生的事】" in user_msg and "演唱会" in user_msg,
          user_msg[:300])
    check("请求带上了红线", "【这一拍的红线】" in user_msg and "不会主动道歉" in user_msg,
          user_msg[:300])
    check("关系只给在场的人", "【你对他们现在怎么看】" in user_msg
          and "曜子" not in user_msg, user_msg[:400])
    check("带上了场景与意图", "放学后的音乐室" in user_msg and "演唱会" in user_msg)
    check("仍用这个角色的模型", sent.get("model") == "fake-和纱", sent.get("model"))
    check("返回日语 + 中文 + 情绪",
          r and r.get("ja", "").startswith("……") and r.get("zh", "").startswith("（译文）")
          and r.get("emo") == "cold", r)

    # 中文作答 → 判失败（原先就有这条，别在改上下文时弄丢）
    ANSWER["v"] = "别自作多情了。"
    urllib.request.urlopen = fake_open
    try:
        r2 = actor.speak("和纱", "随便说一句")
    finally:
        urllib.request.urlopen = real_open
    check("中文作答判失败", r2 is None, r2)

    # 显式传 recent="" → 不出「刚才发生的事」
    ANSWER["v"] = "……そーいうこと。"
    urllib.request.urlopen = fake_open
    try:
        actor.speak("和纱", "随口接一句", recent="")
    finally:
        urllib.request.urlopen = real_open
    check("recent 可以显式关掉", "【刚才发生的事】" not in
          (captured[-1]["messages"][1]["content"]), captured[-1]["messages"][1]["content"][:200])

    # 没卡 → 不调用模型
    n_before = len(captured)
    urllib.request.urlopen = fake_open
    try:
        r3 = actor.speak("没有这个角色", "说一句")
    finally:
        urllib.request.urlopen = real_open
    check("没卡不调用模型", r3 is None and len(captured) == n_before)

    # 玩家给的走向要标成「方向」而不是「场景」，免得角色把它念出来
    urllib.request.urlopen = fake_open
    try:
        actor.speak("和纱", "接一句", direction="把话说开", recent="")
    finally:
        urllib.request.urlopen = real_open
    last_user = captured[-1]["messages"][1]["content"]
    check("走向单独标出来", "【这一场往哪儿演】" in last_user and "把话说开" in last_user,
          last_user[:300])
    check("走向注明不是台词", "不是台词" in last_user, last_user[:300])

    # 情绪标签：能解出来，而且只认约定的那几个词（否则立绘挑不到图）
    ANSWER["v"] = "……うるさい。\n<emotion>毒舌</emotion>"
    urllib.request.urlopen = fake_open
    try:
        r4 = actor.speak("和纱", "怼回去", recent="")
    finally:
        urllib.request.urlopen = real_open
    check("情绪标签解出来了", r4 and r4.get("emo") == "毒舌", r4)
    check("台词里不留标签", r4 and "<emotion>" not in r4.get("ja", ""), r4)

    ANSWER["v"] = "……そう。\n<emotion>不开心</emotion>"
    urllib.request.urlopen = fake_open
    try:
        r5 = actor.speak("和纱", "随口一句", recent="")
    finally:
        urllib.request.urlopen = real_open
    check("不认识的音节当没写", r5 and r5.get("emo") == "" and r5.get("ja"), r5)
    translate.to_zh = real_zh

    print("\n[5b] cast.status：界面状态走同一套解析")
    st = cast.status()
    by = dict((m.get("name") or "", m) for m in st.get("members") or [])
    check("玩家在列表里", by.get("你", {}).get("role") == "player", st.get("members"))
    check("角色带卡状态", by.get("和纱", {}).get("has_card") is True, by.get("和纱"))
    check("角色带语音状态", by.get("和纱", {}).get("voice") is True, by.get("和纱"))
    check("可加入列表非空", len(st.get("can_join") or []) >= 2, st.get("can_join"))

    print("\n[6] 导演：红线能过 _clean 并传给角色")
    beats, dropped = director_gen._clean([
        {"speaker": "和纱", "type": "say", "text": "「……我知道。」", "ban": "不能说出「抱歉」"},
        {"speaker": "雪菜", "type": "say", "text": "「那就好。」"},
        {"speaker": "你", "type": "say", "text": "「我先走了。」"},
        {"speaker": "路人X", "type": "say", "text": "被丢掉"},
    ], 400)
    check("不认识的说话人被丢", dropped == 1 and len(beats) == 3, (dropped, len(beats)))
    check("ban 保留", beats[0].get("ban") == "不能说出「抱歉」", beats[0])
    check("没写 ban 就是空串", beats[1].get("ban") == "", beats[1])
    # 立绘 / 音色按 id 找：演员有 id，玩家没有
    check("演员带上 cid", beats[0].get("cid") == "和纱" and beats[1].get("cid") == "雪菜",
          [b.get("cid") for b in beats])
    check("玩家没有 cid", beats[2].get("cid") == "", beats[2])

    got = {}

    def fake_speak(cid, intent, scene="", prev="", timeout=0, ban="", recent=None,
                   direction=""):
        got[cid] = {"intent": intent, "ban": ban, "direction": direction}
        return {"ja": "……そう。", "zh": "……是吗。", "emo": ""}

    real_speak = actor.speak
    actor.speak = fake_speak
    try:
        out = director_gen._render_beats(
            [{"speaker": "和纱", "type": "say", "text": "意图A", "ban": "别道歉", "voice": True},
             {"speaker": "雪菜", "type": "say", "text": "意图B", "voice": True}],
            scene="音乐室", direction="把话说开")
    finally:
        actor.speak = real_speak
    check("ban 传到了角色模型", got.get("和纱", {}).get("ban") == "别道歉", got)
    check("走向也传到了角色模型", got.get("和纱", {}).get("direction") == "把话说开", got)
    check("台词换成了角色写的日语",
          out and out[0].get("ja") == "……そう。" and out[0].get("text") == "……是吗。", out)

    # 玩家 / 旁白不该被翻成日语：翻了就会被某个角色的音色念出来
    import translate as _tr
    real_ja = _tr.to_ja
    called = []

    def fake_ja(s):
        called.append(s)
        return "「…」"

    _tr.to_ja = fake_ja
    actor.speak = fake_speak
    try:
        mix = director_gen._render_beats(
            [{"speaker": "你", "type": "say", "text": "我先走了。", "voice": True},
             {"speaker": "", "type": "narr", "text": "雪停了。", "voice": False}],
            scene="音乐室")
    finally:
        actor.speak = real_speak
        _tr.to_ja = real_ja
    check("玩家台词不翻日语", called == [] and not (mix[0].get("ja")), (called, mix[0]))
    check("旁白不翻日语", not (mix[1].get("ja")) and mix[1].get("voice") is False, mix[1])

    print("\n[7] 导演自己的提示词也带上下文")
    scenelog.clear()
    scenelog.add("和纱", "「……别管我。」", "say")
    real_state = director_gen._state
    director_gen._state = lambda: {"direction": "走向：把话说开", "beats": 7, "chars_per_beat": 350}
    try:
        up = director_gen._user_prompt(0, 7, 2)
    finally:
        director_gen._state = real_state
    check("导演看得到刚才发生的事", "【刚才发生的事】" in up and "别管我" in up, up[:400])
    check("导演契约里有 ban", '"ban"' in up and "ban" in director_gen.SYSTEM)
    check("导演规则里写了红线", "ban" in director_gen.SYSTEM)

    # 全是台词时催一条画面；刚有旁白就不催（措辞改过，认「这一拍要画」）
    scenelog.clear()
    for i in range(3):
        scenelog.add("和纱" if i % 2 == 0 else "雪菜", "台词%d" % i, "say")
    up_say = director_gen._user_prompt(0, 7, 2)
    check("全是台词时提示要画面", "这一拍要画" in up_say, up_say[-500:])
    scenelog.add("", "雪停了。", "narr")
    up_narr = director_gen._user_prompt(0, 7, 2)
    check("刚有旁白就不催", "这一拍要画" not in up_narr, up_narr[-500:])
    check("导演规则里写了要留画面", "画面" in director_gen.SYSTEM)

    # 玩家刚说过话：导演看得到，而且被告知别再替他说话
    print("\n[7b] 玩家说的话要进场景原话")
    scenelog.clear()
    scenelog.add("雪菜", "「……三年前的事。」", "say")
    scenelog.add("你", "「我问你那天为什么先走。」", "say")
    check("最后一条被认成玩家", director_gen._last_is_player() is True,
          scenelog.recent(2))
    up2 = director_gen._user_prompt(0, 7, 2)
    check("导演看得到玩家原话", "为什么先走" in up2, up2[:600])
    check("提示别再替玩家说话", "玩家刚说过话" in up2 and "不要再替他说话" in up2, up2[:700])
    scenelog.add("和纱", "「……别问了。」", "say")
    check("说完就撤掉那条提示", "玩家刚说过话" not in director_gen._user_prompt(0, 7, 2))

    print("\n[8] 记账：调过的模型都要报 usage")
    import translate as _tr2
    import usage as _u

    rec = []

    def rec_fn(model, u):
        rec.append((model, dict(u or {})))
        return {}

    check("一开始没有记账口", True)          # 占位：下面显式解绑再验，免得受前面 import main 影响
    _u.bind(None)
    check("解绑后没有记账口", _u.bound() is False)
    check("没记账口也不炸", _u.record("m", {"total_tokens": 5}) == {})
    _u.bind(rec_fn)
    check("绑定后认得出", _u.bound() is True)

    # 角色这一句
    rec.clear()
    ANSWER["v"] = "……うるさい。"
    urllib.request.urlopen = fake_open
    try:
        actor.speak("和纱", "怼回去", recent="")
    finally:
        urllib.request.urlopen = real_open
    check("角色开口记账", any(m == "fake-和纱" and u.get("total_tokens") == 120
                          for m, u in rec), rec)

    # 导演这一拍（llm.generate 换成假的，别真去联网）
    rec.clear()
    real_gen = director_gen.llm.generate
    real_speak2 = actor.speak
    real_ja2 = _tr2.to_ja
    real_cfg2 = director_gen._cfg

    class _FakeResult(object):
        text = '{"beats":[{"speaker":"和纱","type":"say","text":"嗯。"}]}'
        model = "fake-director"
        usage = {"prompt_tokens": 500, "completion_tokens": 50, "total_tokens": 550}

    director_gen.llm.generate = lambda payload, cfg, timeout=0: _FakeResult()
    director_gen._cfg = lambda: {"base_url": "http://127.0.0.1:9", "api_key": "k",
                                 "model": "fake-director"}
    actor.speak = lambda *a, **k: None
    _tr2.to_ja = lambda s: ""
    try:
        g = director_gen.generate(1)
    finally:
        director_gen.llm.generate = real_gen
        director_gen._cfg = real_cfg2
        actor.speak = real_speak2
        _tr2.to_ja = real_ja2
    check("导演记账", any(m == "fake-director" and u.get("total_tokens") == 550
                        for m, u in rec), (rec, g.get("ok")))

    # 翻译也要记账（一句台词一次调用，不能从累计里消失）
    rec.clear()
    real_cc = _tr2._cloud_cfg
    _tr2._cloud_cfg = lambda: {"key": "k", "base": "http://127.0.0.1:9", "model": "fake-mt"}
    urllib.request.urlopen = fake_open
    try:
        _tr2._cloud_raw("テスト", "zh")
    finally:
        urllib.request.urlopen = real_open
        _tr2._cloud_cfg = real_cc
    check("翻译记账", any(m == "fake-mt" for m, _u2 in rec), rec)

    _u.bind(None)
    check("能解绑", _u.bound() is False)

    print("\n" + ("全部通过" if not FAILS else "失败 %d 项：%s" % (len(FAILS), "、".join(FAILS))))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
