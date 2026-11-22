# -*- coding: utf-8 -*-
"""场景 CG 自动匹配（一个功能一个文件）

背景图都在 assets/cg/ 下，**文件名就是标签**，括号里依次是
「时间 / 天气季节 / 地点细节」：

    咖啡厅（白天）.webp
    和纱家别墅（客厅）（黑夜）.webp
    商业街的斑马线前（黄昏）（圣诞节）.webp

tools/import_cg.py 负责把原图转成 webp 并生成 assets/cg/index.json；
本模块只读 index.json（不碰图片本身），把对话里的 <scene>地点</scene>
和这些标签比对，挑最贴切的一张，返回它的 id（= 文件名去掉扩展名）。

匹配分两步，避免「差不多就行」把图挑歪：
  1. 先按地点名选一个地点（精确 > 包含 > 字形相似），不够像就不换图；
  2. 再在同一地点的若干变体里，按时间 / 天气 / 细节标签挑一张；
     对话没说时间就沿用当前背景的时间，没说天气就不挑下雪、圣诞这种特例。
"""
import json
import os
import re
import threading

# ---------------- 词表 ----------------
# 时间：影响"同一地点换时段"，也是"沿用上一张图"的依据
TIME_WORDS = ("白天", "黄昏", "晚上", "黑夜", "深夜")
TIME_ALIAS = {
    "早晨": "白天", "清晨": "白天", "上午": "白天", "中午": "白天", "下午": "白天",
    "早上": "白天", "天亮": "白天", "午后": "白天",
    "傍晚": "黄昏", "日落": "黄昏", "夕阳西下": "黄昏",
    # 模型常写单字「夜」（实测：<scene>和纱家别墅（夜）</scene>），
    # 不在表里就会被当成地点细节丢掉 → 时间空 → 挑到「白天」那张，夜里下雪变白天。
    "夜": "晚上", "夜晚": "晚上", "夜里": "晚上", "入夜": "晚上", "天黑": "晚上", "晚间": "晚上",
    "午夜": "深夜", "凌晨": "深夜", "半夜": "深夜", "深夜": "深夜",
}
# 天气 / 季节：对话没提就不挑（圣诞、大雪都是特例）
WEATHER_WORDS = ("晴天", "阴天", "白雪皑皑", "薄雪", "冬天", "圣诞节")

# 只要不是地点本身的时间/天气词，都算"地点细节"（客厅、地下室、人行道、空无一人…）
# 这些不用枚举：index.json 里有什么就用什么，新增图不用改代码。

# 纯天空图：只有明确抬头看天时才挑，否则会盖掉正常场景
SKY_WORDS = ("天空", "云", "霞", "夕阳", "日出", "日落")

# 模型可能说的泛称 → 素材里的具体地点名（只做一次替换；替换不到再走相似度）
PLACE_ALIAS = {
    "咖啡馆": "咖啡厅", "咖啡店": "咖啡厅", "咖啡": "咖啡厅",
    "音乐室": "第二音乐室", "音乐教室": "音乐教室",
    "校门": "学校大门口", "学校": "学校大门口", "高中": "学校大门口",
    "大学": "大学大门口", "学院": "大学",
    "车站": "站台", "地铁站": "地铁站入口步梯", "地铁": "地铁站入口步梯",
    "机场": "机场内", "电车": "电车上", "公园": "公园里",
    "街道": "街上", "马路": "街上", "街边": "街上",
    "路口": "商业区的大路口", "商业街": "商业街的斑马线前",
    "斑马线": "商业街的斑马线前", "商业区": "商业区的大路口",
    "家里": "和纱家别墅", "家": "和纱家别墅", "别墅": "和纱家别墅",
    "餐厅": "高档餐厅", "酒店": "酒店大堂", "首饰": "首饰店",
    "ktv": "ktv包厢内", "KTV": "ktv包厢内", "包厢": "ktv包厢内",
    "音乐馆": "市音乐馆", "比赛会场": "钢琴比赛会场",
    "舞台": "学园祭舞台", "学园祭": "学园祭舞台",
    "图书馆": "大学图书馆", "食堂": "大学食堂", "教学楼": "大学教学楼外",
    "温泉": "温泉旅馆", "酒店大堂": "酒店大堂", "国道": "国道公路",
    # 旧版固定枚举，存档和旧对话里还会出现
    "classroom": "教室", "corridor": "教学楼走廊", "music": "第二音乐室",
    "street": "街上", "cafe": "咖啡厅", "home": "和纱家别墅",
    "night": "夜晚天空", "stage": "学园祭舞台",
    "车站前": "站台",
    "站前": "站台",
    "电车站": "站台",
    "校门前": "学校大门口",
    "校门口": "学校大门口",
    "学校门口": "学校大门口",
    "楼道": "大学楼道",
    "楼梯": "大学楼道",
    "天台": "学校大门口",
    "屋顶": "学校大门口",
    "楼顶": "学校大门口",
    "操场": "学校操场",
    "体育馆": "学校体育馆",
    "音乐厅": "市音乐馆",
    "商店": "商业街的斑马线前",
    "河边": "河边绿道",
    "回家": "回家的路",
    "飞机": "飞机机舱内",
    "教室外": "教学楼走廊",
    "教室门口": "教学楼走廊",
    "走廊上": "教学楼走廊",
    "便利店": "商业街的斑马线前",
    "超市": "商业街的斑马线前",
    "商店街": "商业街的斑马线前",
}

# 查询里常见的前缀（"在街上" "去咖啡厅"），比对地点名时去掉
_LEAD_PARTICLES = ("在", "去", "到", "从", "回", "往", "于")

_PART_RE = re.compile(r"[（(]([^）)]*)[）)]")

# ---------------- 目录状态 ----------------
_LOCK = threading.Lock()
_ASSETS_DIR = ""
_CACHE = {"mtime": -1.0, "data": None}


def set_assets_dir(path):
    """把素材目录告诉它（和 special_files 一样，由 main.py 启动时传入）"""
    global _ASSETS_DIR
    _ASSETS_DIR = path or ""
    _CACHE["mtime"] = -1.0
    _CACHE["data"] = None


def assets_dir():
    """当前素材目录（只读用；firstrun 的素材自检要它）"""
    return _ASSETS_DIR


def index_path():
    return os.path.join(_ASSETS_DIR, "cg", "index.json")


# ---------------- 文件名解析 ----------------
def parse_name(name):
    """「和纱家别墅（客厅）（黑夜）」→ {place, time, weather, state[]}"""
    name = (name or "").strip()
    if not name:
        return {"place": "", "time": "", "weather": "", "state": []}
    place = _PART_RE.sub("", name).strip()
    time = weather = ""
    state = []
    for part in (p.strip() for p in _PART_RE.findall(name)):
        if not part:
            continue
        t = TIME_ALIAS.get(part) or (part if part in TIME_WORDS else "")
        if t and not time:
            time = t
            continue
        if part in WEATHER_WORDS and not weather:
            weather = part
            continue
        state.append(part)
    return {"place": place, "time": time, "weather": weather, "state": state}


def _bigrams(text):
    if len(text) < 2:
        return {text} if text else set()
    return {text[i:i + 2] for i in range(len(text) - 1)}


def _dice(a, b):
    """两个字串的 bigram Dice 相似度，0~1"""
    A, B = _bigrams(a), _bigrams(b)
    if not A or not B:
        return 0.0
    return 2.0 * len(A & B) / (len(A) + len(B))


# ---------------- index.json ----------------
def catalog():
    """读 assets/cg/index.json（文件改了会自动重新读，不用重启程序）"""
    p = index_path()
    try:
        mtime = os.path.getmtime(p)
    except OSError:
        return {"items": [], "places": [], "sky": [], "mtime": 0}
    if _CACHE["data"] is not None and _CACHE["mtime"] == mtime:
        return _CACHE["data"]
    with _LOCK:
        if _CACHE["data"] is not None and _CACHE["mtime"] == mtime:
            return _CACHE["data"]
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return {"items": [], "places": [], "sky": [], "mtime": 0}
        items = data.get("items") or []
        # 老的 index 没有 place 字段时兜底解析一次
        for it in items:
            if not it.get("place"):
                it.update(parse_name(it.get("id") or it.get("file") or ""))
            it.setdefault("kind", "sky" if any(w in (it.get("place") or "") for w in SKY_WORDS) else "place")
        # 词表也从目录里长出来：新增「雨天」这种标签不用改代码
        times = list(TIME_WORDS)
        weathers = list(WEATHER_WORDS)
        states = []
        for it in items:
            if it.get("time") and it["time"] not in times:
                times.append(it["time"])
            if it.get("weather") and it["weather"] not in weathers:
                weathers.append(it["weather"])
            for st in it.get("state") or []:
                if st not in states:
                    states.append(st)
        data = {
            "items": items,
            "places": sorted({it["place"] for it in items if it.get("kind") != "sky" and it.get("place")}),
            "sky": sorted({it["place"] for it in items if it.get("kind") == "sky" and it.get("place")}),
            "times": times,
            "weathers": weathers,
            "states": states,
            "mtime": mtime,
        }
        _CACHE.update({"data": data, "mtime": mtime})
        return data


# ---------------- 匹配 ----------------
# 天气线索：对话里怎么说 → 素材里的天气标签
WEATHER_HINT = {
    "下雪": "白雪皑皑", "大雪": "白雪皑皑", "飘雪": "白雪皑皑", "雪天": "白雪皑皑",
    "积雪": "白雪皑皑", "白雪": "白雪皑皑", "雪停了": "白雪皑皑", "雪地里": "白雪皑皑",
    "小雪": "薄雪", "薄雪": "薄雪", "零星小雪": "薄雪", "一层薄雪": "薄雪",
    "晴天": "晴天", "放晴": "晴天", "天晴": "晴天", "阳光": "晴天", "晴空": "晴天",
    "阴天": "阴天", "阴云": "阴天", "阴沉": "阴天", "乌云": "阴天", "多云": "阴天",
    "冬": "冬天", "寒冬": "冬天", "严寒": "冬天",
    "圣诞": "圣诞节",
}


def weather_hint(text):
    """从一句话里读天气/季节（取最靠后的提及）。没有就返回空串。

    为什么要它：模型经常只写地点（<scene>学校大门口</scene>），
    匹配会沿用上一张背景的天气 —— 于是「雪停了，天晴」可能还挂着雪景，
    或者反过来晴天接一张「积雪」的图。
    """
    s = text or ""
    if not s:
        return ""
    best, best_pos = "", -1
    for w, val in WEATHER_HINT.items():
        i = s.rfind(w)
        if i > best_pos:
            best, best_pos = val, i
    return best


def detail_hint(text, cat=None):
    """从一句话里读「地点细节」标签（空无一人/人来人往/开灯/熄灯…）。

    词表直接来自 index.json 的 state：素材里有什么就认什么，新加图不用改代码。
    返回有序列表（按在句子里出现的位置）。
    """
    s = text or ""
    if not s:
        return []
    try:
        c = cat or catalog()
    except Exception:
        return []
    states = c.get("states") or []
    hits = []
    for st in states:
        # 太长/太泛的标签不认（「房间内」这种到处都有）
        if len(st) < 2 or st in ("房间", "房间内", "门口", "人行道", "入口处"):
            continue
        i = s.rfind(st)
        if i >= 0:
            hits.append((i, st))
    hits.sort()
    return [st for _i, st in hits]


def time_hint(text):
    """从一句话里读出「时段」：早上/白天/午后 → 白天；傍晚/夕阳 → 黄昏；
    晚上/天黑了 → 晚上；深夜/凌晨 → 深夜。取**最靠后**的那次提及
    （「昨天晚上…今天白天」要按后说的算）。
    """
    s = text or ""
    if not s:
        return ""
    best, best_pos = "", -1
    for w, tp in TIME_ALIAS.items():
        i = s.rfind(w)
        if i > best_pos:
            best, best_pos = tp, i
    for w in TIME_WORDS:
        i = s.rfind(w)
        if i > best_pos:
            best, best_pos = w, i
    return best


def _parse_query(text, cat):
    """把模型给的场景串拆成地点 + 标签；标签词表来自 index.json"""
    q = parse_name(text)
    blob = (text or "").strip()
    times, weathers, states = cat["times"], cat["weathers"], cat["states"]

    if not q["time"]:
        for t in times:
            if t in blob:
                q["time"] = t
                break
        if not q["time"]:
            for alias, t in TIME_ALIAS.items():
                if alias in blob:
                    q["time"] = t
                    break
    if not q["weather"]:
        for w in weathers:
            if w in blob:
                q["weather"] = w
                break
    # 地点串里也会塞细节（「和纱家别墅的客厅」）：目录里出现过的细节词直接算标签
    for st in states:
        if st in blob and st not in q["state"]:
            q["state"].append(st)
    # 去掉开头"在/去/到…"
    place = q["place"]
    for p in _LEAD_PARTICLES:
        if place.startswith(p) and len(place) > 1:
            place = place[1:]
            break
    q["place"] = place.strip()
    return q


def _place_score(q, place):
    """地点名相似度：精确 1.0；包含关系 0.72~0.9；其余按 bigram 相似度打折"""
    if not q or not place:
        return 0.0
    if q == place:
        return 1.0
    if q in place or place in q:
        short, long_ = sorted((len(q), len(place)))
        return 0.72 + 0.18 * (short / float(long_))
    return 0.62 * _dice(q, place)


def _strip_words(text, words):
    """去掉地名里混进来的时间/天气词（「圣诞节的大路口」→「大路口」）"""
    for w in sorted((w for w in words if w), key=len, reverse=True):
        text = text.replace(w, "")
    return text.strip("的，,。 ()（）")


def _place_variants(q):
    """地点名的几种写法：原样、去掉时间、去掉天气、两个都去掉"""
    base = q["place"]
    out = [base]
    for words in ([q["time"]], [q["weather"]], [q["time"], q["weather"]]):
        v = _strip_words(base, words)
        if v and v not in out:
            out.append(v)
    return out


def _variant_score(it, q, prev_time):
    """同一地点下，按时间/天气/细节给变体打分（0 附近，越高越贴）"""
    s = 0.0
    why = []

    if q["time"]:
        if it.get("time") == q["time"]:
            s += 0.45
            why.append("time=%s" % q["time"])
        elif it.get("time"):
            s -= 0.55
            why.append("time≠%s" % q["time"])
    if q["weather"]:
        if it.get("weather") == q["weather"]:
            s += 0.35
            why.append("weather=%s" % q["weather"])
        else:
            s -= 0.40

    for st in q["state"]:
        if any(st == e or st in e or e in st for e in (it.get("state") or [])):
            s += 0.30
            why.append("detail=%s" % st)
        else:
            s -= 0.10

    # 图上多出来的细节/天气：没提就扣分，避免一上来就挑「圣诞节」「空无一人」这种特例
    for e in it.get("state") or []:
        if not any(e == st or st in e or e in st for st in q["state"]):
            s -= 0.12 * min(1.0, len(e) / 8.0)
    if not q["weather"] and it.get("weather"):
        s -= 0.18

    # 时间连续：没提时间就沿用当前背景的时段
    if not q["time"] and it.get("time"):
        if prev_time and it["time"] == prev_time:
            s += 0.30
            why.append("keep=%s" % prev_time)
        elif not prev_time and it["time"] == "白天":
            s += 0.06
    return s, "+".join(why)


def match(scene, prev="", time_hint="", weather_hint="", detail_hints=None):
    """把对话里的场景串匹配到一张 CG。

    scene: 模型给的 <scene> 内容（可带括号标签，如「大学图书馆（黄昏）」）
    prev:  当前背景的 id（用于沿用时段）
    time_hint: 这一轮对话里明确提到的时段（白天/黄昏/晚上…）。
        模型经常只写地点不写时间，没有它就会沿用上一张背景的时段 ——
        实测「白天去学校」被接成「学校大门口（晚上）」。
    weather_hint: 这一轮提到的天气/季节（下雪/晴天/阴天…），同理。
    detail_hints: 这一轮提到的地点细节（开灯/熄灯/空无一人/人来人往…）。
    返回 {} 或 {id, file, place, time, weather, state, score, why}
    """
    cat = catalog()
    if not cat["items"]:
        return {}
    text = (scene or "").strip()
    if not text:
        return {}
    q = _parse_query(text, cat)
    if time_hint and not q["time"]:
        q["time"] = time_hint
    if weather_hint and not q["weather"]:
        q["weather"] = weather_hint
    for _st in (detail_hints or []):
        if _st and _st not in q["state"]:
            q["state"].append(_st)
    if not q["place"]:
        return {}
    # 复合场景名（「学校教学楼的天台」）直接匹配不上时，退一步看里面有没有认识的别名词
    _alias_hit = ""
    for _k, _v in sorted(PLACE_ALIAS.items(), key=lambda kv: -len(kv[0])):
        if _k and _k in text:
            _alias_hit = _v
            break

    # 当前背景的时段（"咖啡厅（晚上）" → 晚上），用于"没说时间就沿用"
    prev_time = parse_name(prev)["time"]

    # 天空图：只有明确提到天空/云/霞才参选
    sky_ok = any(w in text for w in SKY_WORDS)

    # 1. 选地点（别名也参与，例如「学校」→「学校大门口」）
    #    「圣诞节的大路口」这种把标签写进地点的，去掉时间/天气再比一遍
    aliases = []
    for name in _place_variants(q):
        if name not in aliases:
            aliases.append(name)
        alias = PLACE_ALIAS.get(name) or PLACE_ALIAS.get(name.lower())
        if alias and alias not in aliases:
            aliases.append(alias)

    best_place, best_place_score = "", 0.0
    # 素材里**本来就有**这个地点时直接用，别让别名抢走：
    # 新整理进来的「街道（白天）/（黑夜）」跟老的「街上」并存，
    # 按相似度打分别名会先撞上「街上」，把新图全抢过去了（实测）。
    if q.get("place") in cat["places"]:
        best_place, best_place_score = q["place"], 1.0
    for pl in ([] if best_place else cat["places"] + (cat["sky"] if sky_ok else [])):
        sc = max(_place_score(a, pl) for a in aliases)
        if sc > best_place_score:
            best_place, best_place_score = pl, sc
    if (not best_place or best_place_score < 0.55) and _alias_hit:
        # 退一步：用场景串里出现的别名词（「天台」→ 学校大门口）
        for pl in cat["places"]:
            if pl == _alias_hit:
                best_place, best_place_score = pl, 0.6
                break

    # 2. 在该地点的变体里挑
    pool = [it for it in cat["items"] if it.get("place") == best_place]
    if not pool:
        return {}
    scored = []
    for it in pool:
        sc, why = _variant_score(it, q, prev_time)
        scored.append((sc, it.get("id") or "", it, why))
    # 同分时按 id 排，保证每次结果一样（顺序稳定）
    scored.sort(key=lambda x: (-x[0], x[1]))
    best = scored[0]

    return {
        "id": best[2].get("id") or "",
        "file": best[2].get("file") or "",
        "place": best_place,
        "time": best[2].get("time") or "",
        "weather": best[2].get("weather") or "",
        "state": best[2].get("state") or [],
        "score": round(best[0], 3),
        "place_score": round(best_place_score, 3),
        "why": best[3],
    }


def prompt_hint():
    """写进 system prompt 的【场景】说明（地点表来自素材目录，不用手写）。

    2026-11-06 改过一轮措辞：原来的判据是「明确发生了地点变化」——
    太含糊，本地小模型（kazusa14b-v7）实测 6 次「人已经进到某处」的输入**一次都没输出**
    <scene>（`tools/measure_scene_tags.py` 就是量这个的）。现在把判据写成三件可执行的事，
    并给一条**看得见的例子**（小模型靠模仿，光讲规则没用）。
    """
    cat = catalog()
    pl = cat["places"]
    trigger = ("\n【场景】出现下面三种情况之一时，在回复最前面输出 <scene>地点</scene>：\n"
               "  ① 这一轮里他们**换了地方**（到了、走进、离开、上车、回家路上…）；\n"
               "  ② 这一轮**明确说了人在哪儿**（哪怕只是换个房间：\n"
               "     「我在卫生间洗漱」「坐在教室里」都算）；\n"
               "  ③ 时间明显往后推了（天黑了、第二天早上）；\n"
               "  **另外：这一轮如果说了时段（早上/白天/午后/傍晚/晚上/深夜），\n"
               "  一定要把时间写进 <scene>**，例如 <scene>学校大门口（白天）</scene> ——\n"
               "  天气/特殊日子也一起写进去：<scene>街上（晚上）（白雪皑皑）</scene>；\n"
               "  只写地点的话，程序会沿用上一张的天气（雪停了还挂着雪景）。\n"
               "  只写地点的话，程序会沿用上一张背景的时段，「白天去学校」就会接成晚上的校门口。\n"
               "选地点时**不用和下面表的字面完全一致，挑最接近的就行**：\n"
               "  在卫生间洗漱、表里没这间房 → 挑带洗手台/浴室的房间；\n"
               "  放学往家走 → 挑回家的路 / 街道；以此类推。\n"
               "只是嘴上说「要不要去某地」、人还没动，就不要输出；\n"
               "还在原地、也没提地点，也不要输出。\n"
               "例：<scene>咖啡厅</scene>\n")
    if not pl:
        # 素材还没导入：退回最早那套固定枚举，至少不把功能弄坏
        return (trigger + "可选值：classroom(教室) corridor(走廊) music(音乐室) street(街道) "
                "cafe(咖啡馆) home(家里) night(夜晚) stage(舞台)。"
                "只是嘴上说「要不要去哪」、人还没到，就不要输出这个标记。")
    lines = [
        trigger,
        "地点从下面这些里选一个，可以带上时间写成「地点（黄昏）」：\n" + "、".join(pl) + "。",
    ]
    if cat["sky"]:
        lines.append("只是抬头看天时，可以用：" + "、".join(cat["sky"]) + "。")
    lines.append("只是嘴上说「要不要去哪」、人还没到，就不要输出这个标记；"
                 "一次只写一个地点，不要解释。没有以上三种情况就不要输出。")
    return "".join(lines)


def catalog_public():
    """给前端/调试用的目录（不带词表内部状态）"""
    cat = catalog()
    return {"items": cat["items"], "places": cat["places"], "sky": cat["sky"]}
