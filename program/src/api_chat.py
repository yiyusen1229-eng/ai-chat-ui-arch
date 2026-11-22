# -*- coding: utf-8 -*-
"""/api/chat 的实现，从 main.py 拆出；逻辑未改动。"""

import json, os, time
import char_res
import characters
import diag
import perf
import perfcfg
import persona_ctx
import persona_stage
import scene_cg
import hscene

# <hscene>是</hscene>：聊天模型对「当前是不是成人场面」的判定
import re

# 文字推断场景用：真的发生了"移动"才算换场景（嘴上说"要不要去"不算）
_MOVE_WORDS = ("走进", "进去", "进了", "到了", "到达", "离开", "走出", "出来",
               "上了", "下车", "回到", "推开", "推门", "回家", "进到")

# 地点线索词：玩家这句话里提到这些，就值得让模型判断一次"现在在哪儿"。
# 没提就不用判（省一次调用），台词照旧。
_PLACE_CUES = ("家", "公司", "学校", "教室", "办公室", "公寓", "别墅", "宿舍", "房间",
               "卫生间", "洗手间", "浴室", "厕所", "厨房", "卧室", "客厅", "阳台", "玄关",
               "走廊", "楼道", "楼梯", "天台", "门口", "大堂", "大厅", "后厨",
               "街", "路", "车站", "站台", "地铁", "电车", "机舱", "机场", "车",
               "咖啡厅", "咖啡馆", "餐厅", "食堂", "店", "商场", "超市", "酒吧", "ktv",
               "公园", "河边", "图书", "音乐室", "琴房", "体育馆", "操场", "舞台",
               "温泉", "酒店", "旅馆", "教室", "广播室", "更衣室", "会议室", "休息区")

# 让模型从素材表里挑地点（大致匹配）：标签对不上时用它兜底。
# 为什么不让后端硬算：地名千奇百怪（"和纱家别墅的卫生间"），
# 模型看一眼 67 个地名就能挑出"自己的公寓（洗手台）"这种最接近的。
_SCENE_JUDGE_SYS = (
    "你是场景判定器。根据对话判断「此刻两人身处哪个地点」。\n"
    "只能从下面给的列表里挑一个**最接近**的，逐字照抄它的名字（列表里就带房间细节，"
    "例如「自己的公寓（洗手台）」）。\n"
    "允许「差不多就行」：列表里没有完全对应的地点时，**必须**挑功能上最像的那个，"
    "不要因为名字不同就回答「不变」——\n"
    "  · 在卫生间/浴室洗漱 → 「自己的公寓（洗手台）」或「自己的公寓（浴室）」；\n"
    "  · 放学往家走、在路上 → 「回家的路」或「街道（黑夜）」；\n"
    "  · 在某个房间里做某事、列表没有这间房 → 找同样用途的房间（厨房→后厨，卧室→自己的公寓（房间））。\n"
    "只有两种情况回答「不变」：① 对话里根本没提地点也没换地方；② 当前背景已经是最贴切的那张。\n"
    "不要新造地名，不要解释。如果此刻没换地方、也没有任何地点线索，只回答两个字：不变。\n"
    "可用场景列表：%s")


def _judge_scene(prev_bg, player_text, action_text, reply_text, cfg, log, time_hint="", weather_hint=""):
    """没拿到 <scene> 时，让模型从素材表里挑一个最接近的地点。

    返回 (背景命中, 模型回答)；没判定出返回 ({}, "")
    """
    try:
        cat = scene_cg.catalog()
        # 给模型看**完整场景名**（182 条，约 2300 字），这样它能直接挑到
        # "自己的公寓（洗手台）"这种带房间细节的，而不是只给一个大地点再由后端猜房间
        names = [it.get("id") for it in (cat.get("items") or []) if it.get("id")]
        if not names:
            names = [p for p in (cat.get("places") or []) if p]
        if not names or not cfg:
            return {}, ""
        sys_msg = _SCENE_JUDGE_SYS % "、".join(names)
        user_msg = ("当前背景：%s\n玩家刚说：%s\n她这一轮的动作/旁白：%s\n她的台词：%s\n"
                    "这一轮的时间线索：%s；天气线索：%s\n"
                    "此刻在哪个地点？只回答地点名或「不变」。"
                    % (prev_bg or "（未知）", (player_text or "")[:200],
                       (action_text or "")[:120], (reply_text or "")[:120],
                       (time_hint or "（没提）"), (weather_hint or "（没提）")))
        payload = {"model": cfg.get("model"),
                   "messages": [{"role": "system", "content": sys_msg},
                                {"role": "user", "content": user_msg}],
                   "stream": False, "max_tokens": 40, "temperature": 0.0}
        # 会先"思考"的模型必须关思考：不关的话 40 个 token 全花在 reasoning 上，
        # content 是空的（日志："bad_response: content 为空（可能被思考吃掉了预算）"）
        _mb = ((cfg.get("model") or "") + " " + (cfg.get("base_url") or "")).lower()
        if "deepseek" in _mb or "pro" in _mb:
            payload["thinking"] = {"type": "disabled"}
            payload["reasoning_effort"] = "none"
        _jt = time.time()
        r = llm.generate(payload, cfg, timeout=30)
        log("场景：模型判断用时 %.2fs" % (time.time() - _jt))
        ans = (r.text or "").strip().strip("。．.「」\"'")
        if not ans or "不变" in ans:
            log("场景：模型判断=不变")
            return {}, ans
        hit = scene_cg.match(ans, prev_bg) or {}
        if not hit:
            # 模型给了地点但表里对不上：再拿它的回答当线索，配玩家原文试一次
            hit = scene_cg.match((ans + " " + (player_text or "")).strip(), prev_bg) or {}
        if hit:
            log("场景：模型判断 %r → %r" % (ans, hit.get("id") or ""))
        else:
            log("场景：模型判断 %r，但没对上素材" % ans)
        return hit, ans
    except Exception as e:
        log("场景模型判断失败: %s" % e)
        return {}, ""

_re_hscene = re.compile(r"<hscene>\s*([^<]{1,8}?)\s*</hscene>", re.I)
import llm
import standing
import text_parse as _tp
from text_parse import split_bilingual
import tts_client

# 关键参数（#12：以前散在函数体里的数字，集中在这里；表见 other/docs/参数与指标.md）
MAX_TOKENS_CLOUD = 400       # 云端一次回复的上限
MAX_TOKENS_LOCAL = 260       # 本地小模型回复更短
CHAT_TIMEOUT_S = 300         # 等云端回复的超时
STORY_K = 2                  # 原作检索注入几块
STORY_MAX_CHARS = 900        # 原作检索最多注入多少字


def performance_hints(cc, outfit_hint=""):
    """人设末尾的演出标签提示（场景 / 服装 / 情绪 / 动作；云端另加语言 + 双语）。

    **云端和本地都要加**：以前整段挂在 `if cc:` 下面，本地模型（Ollama）
    压根不知道要输出 <scene>/<outfit>/<emotion>，于是场景 CG、立绘、BGM
    全都不会变（2026-11-06 用户反馈「基本没自动换过」）。
    本地不加的只有「只用日语」和「双语输出」：小模型做不好双语，
    硬要求反而写坏，中文由 translate.py 兜底。
    单独提出来是为了能测（见 tools/test_chat_hints.py）。
    """
    s = (scene_cg.prompt_hint() +
         "\n【服装】只有明确换了一套衣服时才输出 <outfit>衣服名</outfit>："
         + (outfit_hint or "（按当前角色立绘）") +
         "平时绝对不要输出。"
         "\n【情绪】在回复最前面输出一个情绪标记，格式 <emotion>情绪</emotion>，"
         "只能从这些里选：calm(平静) soft(温柔) 毒舌 撒娇 哭腔 调侃吐槽 问句 沉重 "
         "紧张 激动 委屈巴巴 自怨自艾 故作轻松 气喘吁吁 H。"
         "怎么挑：紧张=心跳快/结巴/怕被听见；激动=声音压不住（兴奋或气到发抖）；"
         "委屈巴巴=被误会、忍着不哭；自怨自艾=自责、否定自己；故作轻松=明明在意却装没事；"
         "气喘吁吁=刚跑过或刚结束、气息不稳；"
         "H=正在发生成人场面时用 —— 只要此刻是这种场面（场景、动作、对方的举动都算），"
         "哪怕台词听着跟平常没两样、很短、甚至只有拟声和喘息，也一律标 H，不要标 calm 或 soft；"
         "这种场面里的句子通常不长，标 calm 会让她用平静语气说话，很不搭。"
         "按这句台词的实际语气挑，只有心情真的平复时才用 calm；"
         "「毒舌」「调侃吐槽」是她平常的嘴硬和开玩笑，别当生气；"
         "只有真的在争执、说到痛处、要分开时才用「沉重」。"
         "标记只能是开头那一个，正文里不要再出现。"
         "\n【场面】如果是成人场面，在回复最前面加 <hscene>是</hscene>；否则不加。这个标记只用来挑语音参考，不会念出来。"
         "\n【格式】只输出你要说的话。如果有动作或表情，必须写成 "
         "<action>（…）</action> 放在台词前面，不允许出现没有标签的描写句。"
         # 人称：玩家 =「你」。模型默认会把对方写成第三人称「他」（用户 2026-11-22 反馈
         # 「（抓起椅子上的书包，扭头看了看他）」），读起来像旁人在看戏，不像在对玩家说话。
         "\n【人称】<action> 和旁白里指对方（玩家）一律用「你」，不要写「他」「对方」「那个人」；"
         "写你自己可以省略主语。例：<action>（把湿毛巾按在你额头上）</action>"
         # 格式示例两条路都要：小模型只靠模仿，光讲规则它一个标签都不吐
         # （2026-11-06 实测：本地模型 6 次「人已经进到某处」0 次输出 <scene>）
         "\n【示例】人到了新地方、还带动作和情绪时，回复长这样："
         "<hscene>否</hscene><scene>咖啡厅</scene><emotion>calm</emotion>"
         '<action>（推开门，暖气扑在脸上）</action>「…あったかい」')
    if cc:
        s += ("\n\n【语言】无论对方用什么语言说话，你都只用日语回答。"
              "\n【双语】台词必须给两份，紧跟在一起：<ja>日语原句</ja><zh>对应的中文</zh>。"
              "<zh> 不是另一个人写的翻译，就是你自己那句话的意思，"
              "所以要和 <ja> 对得上（不许改口、不许省略、不许加戏），中文要口语、带情绪。"
              '<action> 里的描写也用中文写。例：'
              '<emotion>毒舌</emotion><action>（别过脸）</action>'
              '<ja>「べ、別に。あんたのためじゃないから」</ja>'
              '<zh>「才、才不是。不是为你才做的」</zh>')
    return s


class ChatApi:
    """见 main.Handler。"""
    def _chat(self):
        from main import (ASSETS_DIR, DEFAULT_MODEL, FALLBACK_MODEL, NO_MODELS, OLLAMA, STORY, _memory_context, _record_usage, cloud_cfg, fewshot_msgs, log, ollama_models, split_action, split_emotion, split_scene, split_thinking, urllib)
        try:
            req = self._body()
        except Exception as e:
            return self._json({"error": "bad request: %s" % e}, 400)

        if NO_MODELS and not cloud_cfg():
            return self._json({"reply": "（安全模式：模型未加载）", "thinking": ""})

        emo = "calm"
        scene_now = ""
        outfit_now = ""
        cc = cloud_cfg()
        base_url = (req.get("base_url") or (cc.get("base_url") if cc else "") or OLLAMA).strip()
        if not (base_url.startswith("http://") or base_url.startswith("https://")):
            base_url = OLLAMA
        model = req.get("model") or (cc.get("model") if cc else "") or DEFAULT_MODEL
        if not cc and model not in ollama_models():
            model = FALLBACK_MODEL if FALLBACK_MODEL in ollama_models() else model
        msgs = req.get("messages") or []
        raw_msgs = list(msgs)          # 关系阶段要看完整历史，不能用裁剪后的
        # 当前背景图（前端带的）：模型没说时间时，新场景沿用这个时段
        prev_bg = (req.get("bg") or "").strip()
        # 自动续说：这一轮没有新的玩家消息，是她在接着自己刚才的话往下说
        _auto_continue = bool(req.get("continue"))
        _line_type = "her"          # 续写分句类型（her/you/narr/other），异常路径也要有值
        _speaker = ""               # 非和纱的说话人名字
        # 成人场面：先听模型自己（<hscene> 标记），没给才退回关键词兜底
        _intimate = False
        _h_src = ""
        # 当前衣服 / 手动时期：决定这一轮用哪套人设和世界书
        outfit_now = (req.get("outfit") or "").strip()
        period_req = (req.get("period") or "").strip().lower()
        period = period_req if period_req in ("ic", "cc", "coda") else persona_ctx.period_of(outfit_now)
        log("时期: %s (outfit=%s, 手动=%s)" % (persona_ctx.PERIOD_LABELS.get(period, period),
                                              outfit_now[:20], period_req or "auto"))

        # 上下文预算：超过阈值就切成「记忆书 + 最近一段原文」
        # 预算按**这一轮实际用的端点**选，不按「有没有云端 key」选。
        # 以前用 bool(cc)：配了云端 key 的人即使切到本地模型玩，也按云端 32000 的预算
        # 全量发历史（实测 8500 token）—— 而这个 14B 本地模型 4k 往上就开始说胡话。
        _local_now = llm.provider_of(base_url) == "ollama"
        msgs, mem_note = _memory_context(msgs, bool(cc) and not _local_now)

        # ---- 调试面板（#13）：这一轮 system 花在哪一段、送了多少条 ----
        # 只记字数和估算 token，不记正文（面板不该变成聊天记录查看器）
        _seg = {"msgs_total": len(raw_msgs), "msgs_sent": len(msgs),
                "mem_note": len(mem_note or ""),
                "mem_trimmed": len(msgs) < len(raw_msgs)}

        # 演出 / 切片规则（#19）：每角色的 persona_slices.json，缺了就是默认值
        try:
            _sl = perfcfg.slices()
        except Exception as e:
            log("切片配置读取失败（用默认值）: %s" % e)
            _sl = dict(perfcfg.DEFAULTS_SLICES)

        wb_ctx, wb_names = persona_ctx.worldbook_context(
            msgs, period=period,
            max_entries=_sl["worldbook_entries"], max_chars=_sl["worldbook_chars"])
        system = persona_ctx.persona_text(raw_msgs, period=period)
        if _auto_continue:
            # 续写模式：和"一句台词"的硬规则冲突，必须在最前面覆盖掉，
            # 否则模型会拒绝写旁白（实测返回空）。
            system = ("【续写模式·最高优先级】这一轮不是对话，而是在续写小说。"
                      "忽略后面所有「只输出一句台词」「不要旁白」「必须用「」」之类的限制。\n"
                      "- 旁白/动作/环境/心理：直接写，不加引号；\n"
                      "- 台词：写成 名字：「台词」 的形式（和纱：「…」、春希：「…」）；\n"
                      "- 每轮只写 1~2 句（≤60 字），用换行分开；不要一次写完。\n\n") + system
        _seg["persona"] = len(system)

        # system 的拼装顺序（措辞必须分开，否则模型会把设定和"这次聊过的事"混一起）：
        #   人设 → 常驻记忆书（角色固定设定）→ 世界书（设定）→ 对话记忆书（自动摘要）
        #   后面还有：剧本检索（原作片段）
        try:
            # 常驻记忆书（#3）：两段化之后是「固定设定 + 关系摘要」；没 standing.json 就回落老文本
            mem_extra = standing.text()
            # 切片配置（#19）可以再压一道上限
            if mem_extra and _sl.get("memory_max_chars"):
                mem_extra = mem_extra[:_sl["memory_max_chars"]]
            _seg["memory_extra"] = len(mem_extra or "")
            if mem_extra:
                system = system + (
                    "\n\n【你记得的旧事（角色设定的一部分，事先记好的，不是这轮对话里发生的事；"
                    "用你自己的口气提起，不要照抄原文）】\n" + mem_extra)
            wb_extra = char_res.worldbook_text()
            _seg["wb_extra"] = len(wb_extra or "")
            if wb_extra:
                system = system + (
                    "\n\n【设定补充（用户整理好的资料，只作参考，不要整段复述）】\n"
                    + wb_extra)
        except Exception as e:
            log("角色资源读取失败: %s" % e)

        if wb_ctx:
            system = system + "\n\n" + wb_ctx
        _seg["worldbook"] = len(wb_ctx or "")
        _seg["wb_names"] = (wb_names or [])[:8]
        if mem_note:
            system = system + "\n\n" + mem_note
        _seg["mem_note"] = len(mem_note or "")

        # 剧本检索: 找出和这句话相关的原作片段, 作为"想起来的事"而不是台词。
        # 和世界书分开写：世界书是「设定」，这里是「原作里真的发生过什么」。
        try:
            if STORY is not None and req.get("memory") and STORY.enabled():
                last_user = ""
                _prev_users = []
                for m in reversed(msgs):
                    if m.get("role") == "user":
                        _u = m.get("content") or ""
                        if not last_user:
                            last_user = _u
                        elif len(_prev_users) < 2:
                            _prev_users.append(_u)
                        if len(_prev_users) >= 2:
                            break
                # 情境/时期加权：现在是 coda 就别拿 IC 的教室段子；
                # 这句像亲密/调情/拌嘴，就优先取同情境的片段（语气才对）。
                _sit = STORY.situation_of(last_user + " " + " ".join(_prev_users), loose=True)
                _per = (period or "").lower() or None
                mem = STORY.memory_context(last_user, k=_sl["story_k"],
                                           max_chars=_sl["story_max_chars"],
                                           period=_per, tags=_sit)
                _seg["story"] = len(mem or "")
                if mem:
                    system = system + (
                        "\n\n【原作里发生过的片段（资料，不是这轮对话的内容，也不是你的台词）】\n"
                        + mem
                        + "\n（这些是以前真实发生过的事。只在聊天自然提及时，用你自己的口气带一句；"
                          "不要照抄原文，不要整段复述，不要把它说成刚刚发生的事。）")
                    log("story_rag 命中 %d 字（问：%s）" % (len(mem), last_user[:20]))
        except Exception as e:
            log("story_rag 失败: %s" % e)

        try:
            _sp = characters.sprite_index(os.path.join(ASSETS_DIR, "sprites"))
            _outfit_names = list((_sp.get("outfits") or {}).keys())
            outfit_hint = "、".join(_outfit_names) if _outfit_names else "（按当前角色立绘）"
        except Exception:
            outfit_hint = "（按当前角色立绘）"
        _before_tail = len(system)
        # 演出标签（场景 / 服装 / 情绪 / 动作）：云端和本地都要，见 performance_hints
        if _auto_continue:
            _last_line = ""
            try:
                for _m in reversed(raw_msgs):
                    if isinstance(_m, dict) and _m.get("role") == "assistant":
                        _last_line = (_m.get("content") or "").strip()
                        break
            except Exception:
                pass
            system = system + ("\n【续写故事】这一轮没有对方的新发言。你要像小说一样把场景往下写："
                              "可以写旁白（不加引号的叙述，指玩家时用「你」）、她的台词（用「」包住）、"
                              "也可以写「你」的台词（用『』包住，就像对方真的说了这句）。"
                              "每一轮只写一小段（1~2 句，总共不超过 60 字），"
                              "不要一次把整段情节写完，也不要复述已经写过的内容。")
            if _last_line:
                system = system + ("\n上一句是：" + _last_line[:60] +
                                   "\n不要重复上面这句，要往下推进（换个动作、换句话）。")
                # 交替推进：上一句是她的台词，这一轮就写旁白或对方的话，别又是她一句
                _lp = _last_line.lstrip()
                if _lp[:1] == "「":
                    system = system + ("\n【这一轮只写旁白】：不要加任何引号，不要写她说话，"
                                       "只写一句环境/动作/心理的叙述（20~40 字）。")
                elif _lp[:1] == "『":
                    system = system + ("\n【这一轮写她的反应】：只写一句她的台词（用「」），"
                                       "不要写旁白。")
                else:
                    system = system + ("\n【这一轮写她的台词】：只写一句她说的话（用「」）。")
        system = system + performance_hints(cc, outfit_hint)
        # 场面的最终判定在模型回复里（<hscene>），这里不预判；判定为「是」时解析后强制 H。
        _seg["tail"] = len(system) - _before_tail
        # 「为什么标签不出现」这类问题，光看代码猜不出来 —— WA2_DUMP_PROMPT=1 时把这一轮
        # 真正发出去的 system 落到 work_scratch/last_prompt.txt，方便和最小复现对比
        if os.environ.get("WA2_DUMP_PROMPT"):
            try:
                import datadir
                _dp = os.path.join(datadir.sub("work_scratch"), "last_prompt.txt")
                os.makedirs(os.path.dirname(_dp), exist_ok=True)
                with open(_dp, "w", encoding="utf-8") as _f:
                    _f.write(system)
            except Exception:
                pass
        # few-shot 示例（本地也喂：小模型靠模仿，光讲规则不吐标签）
        # WA2_NO_FEWSHOT=1 只是给「标签不出现」这类问题做 A/B 用的
        _few = [] if os.environ.get("WA2_NO_FEWSHOT") else fewshot_msgs()
        _req_msgs = msgs
        if _auto_continue:
            # 这条只用于本次请求、不入历史：模型对「最后一条消息」的顺从度远高于 system 里的长说明
            _lp2 = (msgs[-1].get("content") or "").strip() if msgs else ""
            _ask = ("（旁白：只写环境/动作/心理，不要写台词的引号，20~40 字）"
                    if _lp2[:1] in ("「", "『") else
                    "（接着写一句台词；如果有其他人说话，写成 名字：「台词」）")
            _req_msgs = list(msgs) + [{"role": "user", "content": _ask}]
        else:
            # 标签在长 system 里容易被模型忽略（实测 deepseek-v4.1-flash 有时整轮一个标签都不给，
            # 于是场景/立绘/情绪全不切换）。把格式提醒贴到**最后一条用户消息**后面：
            # 模型对"最近的一条"顺从度最高。这条只用于本次请求，不入历史。
            _fmt = ("\n\n（格式提醒：回复最前面先给标签 —— <hscene>否</hscene>"
                    "<emotion>情绪</emotion>；场景真的换了才加 <scene>地点</scene>；"
                    "有动作就用 <action>（…）</action>；台词写成 <ja>日语</ja><zh>中文</zh>。"
                    "标签必须给，不要省略。）")
            if _req_msgs:
                _req_msgs = list(_req_msgs)
                _last = dict(_req_msgs[-1])
                _last["content"] = (_last.get("content") or "") + _fmt
                _req_msgs[-1] = _last
        payload = {
            "model": model,
            "messages": ([{"role": "system", "content": system}] + _few + _req_msgs),
            "stream": False,
            "max_tokens": MAX_TOKENS_CLOUD if cc else MAX_TOKENS_LOCAL,
        }
        if not cc:
            payload["reasoning_effort"] = "none"
        if cc and ("pro" in model or "deepseek" in (base_url or "").lower() or "deepseek" in (model or "").lower()):
            payload["thinking"] = {"type": "disabled"}
            payload["reasoning_effort"] = "none"
        t0 = time.time()
        try:
            # #24：统一走 llm 层 —— provider 识别、错误分类、重试一次、必要时回落本地模型
            _cfg = {"api_key": (cc or {}).get("api_key") or "",
                    "base_url": base_url, "model": model}
            _fb = None
            if cc:                      # 云端出错时才回落本地；本来就是本地就没有退路
                try:
                    if ollama_models():
                        _fb = {"base_url": OLLAMA, "model": (FALLBACK_MODEL or model),
                               "api_key": ""}
                except Exception:
                    _fb = None
            _res = llm.generate(payload, _cfg, timeout=CHAT_TIMEOUT_S, fallback_cfg=_fb)
            raw = (_res.text or "").strip()
            # 「标签为什么不出现」这类问题看代码猜不出来：WA2_DUMP_RAW=1 时把模型原样输出
            # 落到 work_scratch/last_raw.txt（和 WA2_DUMP_PROMPT 配套用）
            if os.environ.get("WA2_DUMP_RAW"):
                try:
                    import datadir
                    _rp = os.path.join(datadir.sub("work_scratch"), "last_raw.txt")
                    os.makedirs(os.path.dirname(_rp), exist_ok=True)
                    with open(_rp, "w", encoding="utf-8") as _f:
                        _f.write(raw)
                except Exception:
                    pass
            if _res.degraded:
                log("云端不可用（%s），这一轮用的是本地模型 %s" % (_res.reason, _res.model))
            _uinfo = _record_usage(_res.model or model, _res.usage)
            try:
                _bw = llm.budget_warning(llm.budget_note((_uinfo or {}).get("cost") or 0))
            except Exception:
                _bw = ""
            raw, emo = split_emotion(raw)
            # 情绪标签是模型该给的；它经常偷懒不写（实测 5 句里 3 句没有），
            # 以前没标签直接当 calm，参考音频就永远停在同一组。这里让偷懒看得见：
            #   source=tag      模型自己写了
            #   source=inferred 按台词形态猜的（可能猜错，日志里能对账）
            #   source=default  真没线索，用 calm
            try:
                _src = _tp.LAST_EMO_SOURCE
                if _src != "tag":
                    log("情绪标签%s：%s（模型没写 <emotion>）"
                        % ("推断" if _src == "inferred" else "缺失", emo))
            except Exception:
                pass
            # —— 场面判定：优先用模型自己给的 <hscene> ——
            # 续说轮（没有新的玩家发言）不重新判定：消息里全是我方的短句，
            # 关键词判不出来，连判几次就会把迟滞推向「退出 H 场面」，
            # 于是整个自动续说都变成平静语气。这里直接沿用当前场面。
            _h = None
            if _auto_continue:
                try:
                    _h = bool(hscene.last_scene())
                except Exception:
                    _h = False
                _h_src = "续说沿用"
                log("成人场面：续说轮沿用当前场面=%s" % ("是" if _h else "否"))
            _m = _re_hscene.search(raw or "")
            if _m:
                _v = (_m.group(1) or "").strip().lower()
                _h = _v in ("是", "yes", "y", "true", "1", "h")
                raw = _re_hscene.sub("", raw, count=1)
                _h_src = "模型标记"
                log("成人场面：模型判定=%s" % ("是" if _h else "否"))
            elif not _auto_continue:
                try:
                    # 只看**最近两条**：H 台词离开后，前面几条 H 不能一直把场面钉住
                    # （否则在教室里聊升学也会被判成 H）
                    _h = hscene.is_intimate(raw_msgs[-2:])
                except Exception:
                    _h = False
                _h_src = "关键词兜底"
                log("成人场面：模型没给 <hscene>，关键词判定=%s" % ("是" if _h else "否"))
            _intimate = bool(_h)
            # 逐句兜底：**这一句本身**带明确的露骨词时立刻算 H 场面。
            # 只看"最近两条"的话，H 场景的第一句永远是平常语气（分数还没凑够），
            # 后面再回想才变成 H —— 这就是「第一次平常、回想才对」的直接原因。
            # 注意用 is_explicit 而不是 score()：score 的强特征词里有"腰/胸/だめ"
            # 这类日常词，拿它逐句判会把普通台词全变成 H 语音。
            if not _intimate:
                try:
                    if hscene.is_explicit(raw or ""):
                        _intimate = True
                        log("成人场面：这句本身带露骨词 → 判定为是")
                except Exception:
                    pass
            if not _auto_continue:
                try:
                    # 迟滞只保护「短句/拟声」：玩家发一句完整的、毫无 H 特征的句子时，
                    # 说明场面已经切走了（例：教室里聊升学），要立刻退出，
                    # 否则普通台词会被当成「H 场面里的短句」继续用 H 语音。
                    if not _intimate:
                        _u = ""
                        for _m2 in reversed(raw_msgs):
                            if isinstance(_m2, dict) and _m2.get("role") == "user":
                                _u = (_m2.get("content") or "").strip()
                                break
                        # 玩家说了一句完整、又不带露骨词的句子 → 场面已经切走了，立刻退出。
                        # 判据用 is_explicit（只认露骨词）：score() 里有"腰/胸/だめ"这类
                        # 日常词，用它判断会导致"腰有点疼"也退不出 H 场面，
                        # 普通台词继续用 H 语音（用户反馈「正常语音变 H 了」）。
                        try:
                            _u_explicit = hscene.is_explicit(_u) if _u else False
                        except Exception:
                            _u_explicit = False
                        if _u and len(_u) >= 6 and not _u_explicit:
                            hscene.force_exit()
                            log("成人场面：玩家这句是普通完整句 → 直接退出")
                    # 带迟滞：返回的是**生效**状态（H 场面里短句不会把语气拽回平静）
                    _intimate = hscene.set_last(_intimate)
                except Exception:
                    pass
            if _intimate != bool(_h):
                log("成人场面：本轮判定=否，但仍在 H 场面里（迟滞保护）")
            # 续说轮模型常常不写 <emotion>，会被推断成 calm（语气全变平静）。
            # 这时候沿用历史里上一条的情绪标签。
            if _auto_continue and _src != "tag":
                _le = (req.get("last_emo") or "").strip()
                if _le:
                    log("续说：沿用上一条情绪 %r" % _le)
                    emo = _le
            # 成人场面里模型仍可能标 calm/soft：这里按场面改掉（只影响选参考音频）
            if _intimate:
                # 成人场面里**任何**情绪标签都压成 H：
                # 实证过一次判定为「是」但标签是「毒舌」，只改 calm/soft 的名单漏掉了它，
                # 结果用毒舌参考音频去念 H 台词。这里不再列举，直接压。
                try:
                    _norm = (emo or "").strip()
                    if _norm != "H":
                        log("情绪纠正：成人场面把 %r 改成 H" % (_norm,))
                        emo = "H"
                except Exception:
                    pass
            raw, scene_now, outfit_now = split_scene(raw)
            acts, raw = split_action(raw)
            # 模型自己给的双语：<ja>日语</ja><zh>中文</zh>（比事后再翻译准，还省一次往返）
            raw, _ja, _zh_model = split_bilingual(raw)
            reply, think = split_thinking(raw)
            if _ja:                      # 有 <ja> 就以它为准（那是模型的原句）
                reply = _ja
            action = " ".join(a for a in acts if a).strip()
            # 自动续写：模型按约定用引号区分三类 —— 「」她说的、『』对方说的、无引号旁白。
            # 只有她的台词加「」并配语音；旁白和对方台词不配音。
            if _auto_continue:
                _ms = re.search(r"<say>(.*?)</say>", reply or "", re.S | re.I)
                _my = re.search(r"<you>(.*?)</you>", reply or "", re.S | re.I)
                _body = (reply or "").strip()
                if _ms:
                    reply, _line_type = _ms.group(1), "her"
                elif _my:
                    reply, _line_type = _my.group(1), "you"
                elif _body[:1] in ("「", "『"):
                    _line_type = "you" if _body[0] == "『" else "her"
                else:
                    # 「和纱：「…」」「春希：「…」」这种带说话人的写法
                    _mm = re.match(r"^([^：:「『\n]{1,6})[：:]\s*[「『](.*?)[」』]\s*$",
                                   _body, re.S)
                    if _mm:
                        _who = _mm.group(1).strip()
                        reply = _mm.group(2).strip()
                        _line_type = "her" if _who in ("和纱", "かずさ") else "other"
                        _speaker = _who if _line_type == "other" else ""
                    else:
                        _line_type = "narr"
                reply = re.sub(r"</?(say|you)>", "", reply or "", flags=re.I).strip()
                if _line_type == "other" and _speaker:
                    reply = _speaker + "：" + reply
            # 游戏里角色台词一律用「」包起来，由程序统一处理，不让模型自己加
            _r = (reply or "").strip()
            if _r:
                _r = _r.strip("「」『』")
                # 纯旁白（整句是括号里的动作/环境描写、里面没有引号台词）标成 narr、
                # 而且不包引号。不标的话前端会当 "her" 配音：把「（她在路灯下停了一瞬…）」
                # 这种长句翻成日语念出来，听感就是一长串怪声（日志里出现过 900KB 的音频）。
                if (_line_type == "her" and re.match(r"^[（(【]", _r)
                        and not re.search(r"[「『]", _r)):
                    _line_type = "narr"
                    reply = _r
                else:
                    reply = _r if _line_type != "her" else ("「" + _r + "」")

            # 中文：优先用模型自己写的那句；没写 / 不像中文才回落翻译层
            reply_zh = ""
            action_zh = ""
            try:
                _z = (_zh_model or "").strip().strip("「」『』")
                if _z and _tp.looks_zh(_z):
                    reply_zh = _z
                elif any("\u3040" <= c <= "\u30ff" for c in reply):
                    # 走统一翻译层：云端优先、本地兜底，人名过术语表
                    # （原来只调本地 Hy-MT2，はるき 会被译成「春树」）
                    import translate as _tr
                    reply_zh = _tr.to_zh(_r)
                if action:
                    # 动作描写模型一般直接给中文；是日语才翻
                    if _tp.looks_zh(action) or not any("\u3040" <= c <= "\u30ff" for c in action):
                        action_zh = action
                    else:
                        import translate as _tr2
                        action_zh = _tr2.to_zh(action)
            except Exception as e:
                log("日译中失败: %s" % e)

            # 场景：把 <scene> 的地点/时间对上素材里的背景图（匹配逻辑在 scene_cg.py）
            bg_hit = {}
            # 玩家这句话 + 这轮动作描写：判断有没有"地点线索"
            _u_now = ""
            try:
                for _m3 in reversed(raw_msgs):
                    if isinstance(_m3, dict) and _m3.get("role") == "user":
                        _u_now = (_m3.get("content") or "").strip()
                        break
            except Exception:
                _u_now = ""
            _cue_txt = (_u_now + " " + (action or "")).strip()
            _cue = bool(_cue_txt) and any(w in _cue_txt for w in _PLACE_CUES)
            # 这一轮明确提到的时段（早上/白天/傍晚/天黑了…）：模型只写地点时用它定时间，
            # 否则会沿用上一张背景的时段（「白天去学校」被接成晚上的校门口）
            try:
                _time_hint = scene_cg.time_hint(_cue_txt)
            except Exception:
                _time_hint = ""
            # 天气 / 地点细节同理：模型只写地点时，别让它沿用上一张的雪景/熄灯
            try:
                _weather_hint = scene_cg.weather_hint(_cue_txt)
            except Exception:
                _weather_hint = ""
            try:
                _detail_hints = scene_cg.detail_hint(_cue_txt)
            except Exception:
                _detail_hints = []
            if scene_now:
                try:
                    bg_hit = scene_cg.match(scene_now, prev_bg, time_hint=_time_hint,
                                           weather_hint=_weather_hint, detail_hints=_detail_hints)
                except Exception as e:
                    log("场景匹配失败: %s" % e)
                if not bg_hit:
                    log("场景没匹配上：%r（当前背景 %r）" % (scene_now, prev_bg))
                else:
                    log("场景：%r → %r" % (scene_now, bg_hit.get("id") or ""))
            else:
                # 模型没给 <scene>（实测会漏）：先看文字里有没有移动动词
                # （走进/到了/回家…），嘴上说"要不要去"不算。
                try:
                    if _cue_txt and any(v in _cue_txt for v in _MOVE_WORDS):
                        _hit = scene_cg.match(_cue_txt, prev_bg, time_hint=_time_hint,
                                             weather_hint=_weather_hint, detail_hints=_detail_hints)
                        if _hit:
                            _cur_place = scene_cg.parse_name(prev_bg)["place"]
                            if _hit.get("place") and _hit.get("place") != _cur_place:
                                bg_hit = _hit
                                log("场景：模型没给标记，按文字推断 %r → %r"
                                    % (_hit.get("place"), _hit.get("id") or ""))
                except Exception as e:
                    log("场景文字推断失败: %s" % e)

            # 有地点线索时，再让模型判一次「此刻在哪儿」——**大致匹配**，
            # 不要求标签和素材完全一致：
            #   · 标签常只写到大地点（和纱家别墅），模型能挑到更贴切的房间
            #     （"卫生间里洗漱" → 自己的公寓（洗手台））；
            #   · 标签漏掉时也能补上（"放学往家走" → 回家的路）。
            if _cue and not _auto_continue:
                try:
                    _jcfg = {"api_key": (cc or {}).get("api_key") or "",
                             "base_url": base_url, "model": model}
                    _jhit, _jans = _judge_scene(prev_bg, _u_now, action, reply, _jcfg, log,
                                                   time_hint=_time_hint, weather_hint=_weather_hint)
                    if _jhit:
                        _cur_place = scene_cg.parse_name(prev_bg)["place"]
                        if _jhit.get("place") and _jhit.get("place") != _cur_place:
                            if bg_hit and bg_hit.get("id") != _jhit.get("id"):
                                log("场景：模型改判 %r（原来是 %r）"
                                    % (_jhit.get("id") or "", bg_hit.get("id") or scene_now))
                            bg_hit = _jhit
                        elif not bg_hit:
                            log("场景：模型判断和当前同一地点，不切")
                except Exception as e:
                    log("场景模型判断异常: %s" % e)
            if not bg_hit:
                log("场景：本轮没换（当前 %r）" % (prev_bg or "",))
            diag.note("scene", query=scene_now[:24], id=bg_hit.get("id", ""),
                      place=bg_hit.get("place", ""), time=bg_hit.get("time", ""),
                      score=bg_hit.get("score"))

            _ms = int((time.time() - t0) * 1000)
            _stage = (persona_stage.debug_state() or {}).get("stage")
            # 调试面板：这一轮的提示词分段 / token 估算 / 耗时 / 用量（只记数字和标签）
            try:
                _tok = {"system": diag.token_est(system),
                        "msgs": sum(diag.token_est(m.get("content") or "") + 4 for m in msgs),
                        "fewshot": sum(diag.token_est(m.get("content") or "") + 4 for m in _few)}
                diag.round_record(
                    model=model, tier=("cloud" if cc else "local"), period=period,
                    stage=_stage,
                    seg=_seg, tokens=_tok,
                    usage={k: _uinfo.get(k) for k in ("tokens", "in", "out", "cost") if k in (_uinfo or {})},
                    scene={"id": bg_hit.get("id", ""), "score": bg_hit.get("score")},
                    emotion=emo, outfit=(outfit_now or "")[:16],
                    reply_chars=len(reply or ""), ms=_ms)
            except Exception as e:
                log("调试账本写入失败（不影响对话）: %s" % e)
            # 常驻记忆书（#3）：关系阶段变了才在后台刷一次关系摘要（每个阶段最多一次）
            try:
                standing.note(_stage, raw_msgs)
            except Exception:
                pass
            # 性能采样（#14）：每 N 轮记一行，后台线程写，不占这一轮
            try:
                perf.maybe()
            except Exception:
                pass

            self._json({"reply": reply, "reply_zh": reply_zh, "line_type": _line_type, "thinking": think,
                        "action": action, "action_zh": action_zh,
                        "emotion": emo, "hscene": bool(_intimate),
                        "scene": scene_now, "outfit": outfit_now,
                        "bg": bg_hit.get("id", ""),
                        "period": period,
                        "usage": _uinfo,
                        "model": _res.model or model,
                        "degraded": bool(_res.degraded),
                        "degrade_reason": _res.reason,
                        "budget_warn": _bw,
                        "ms": _ms,
                        "worldbook": wb_names})
        except llm.LLMError as e:
            diag.count("chat.error")
            diag.note("chat_error", kind=e.kind, status=e.status, detail=str(e.detail)[:80])
            log("模型调用失败[%s]：%s" % (e.kind, e.detail))
            self._json({"error": e.user_message, "kind": e.kind, "status": e.status})
        except Exception as e:
            diag.count("chat.error")
            self._json({"error": "调用模型失败：%s" % e})
