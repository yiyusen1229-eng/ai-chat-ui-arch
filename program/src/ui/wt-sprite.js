/* wt-sprite.js —— 立绘 / 服装 / 表情（从 game.js 搬出）
 *
 * 对外（WA2.sprite）：resolveOutfit / outfitOf / spritePath / setSpriteByScene /
 *                      reload / summary / index（衣服索引）
 * 兼容老调用：window.setSpriteByScene / resolveOutfit / outfitOf / spritePath /
 *             __reloadSprites / __spriteSummary 都指到这里
 *
 * 衣服名和文件名都是中文：characters/<角色>/portrait/<衣服名>/<表情名>.png
 */
(function () {
  var WA2 = window.WA2 = window.WA2 || {};
  var $ = (WA2.util && WA2.util.$) || function (id) { return document.getElementById(id); };

  var OUTFIT_BY_SCENE = {
    classroom: "高中·春秋JK制服",
    corridor: "高中·春秋JK制服",
    music: "舞台演出服",       // 练琴 / 演奏
    stage: "舞台演出服",
    street: "coda·常服（眼镜）",
    cafe: "coda·第二套常服",
    home: "婚后·居家服",
    night: "睡衣",
    dress: "礼服",
    yukata: "和服（浴衣）",
    winter: "高中·冬装",
    summer: "高中·夏季JK制服"
  };

  var SPRITE_INDEX = {};       // 衣服名 -> {code, label, default, emotions, variants}
  var currentSprite = null;

  function loadIndex(cb) {
    fetch("/api/character/sprite/index.json", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        Object.keys(SPRITE_INDEX).forEach(function (k) { delete SPRITE_INDEX[k]; });
        Object.assign(SPRITE_INDEX, (d && d.outfits) || {});
        /* 切角色后顺手重拉演出配置（#19）：每角色的 performance.json */
        try { if (WA2.perfCfg && WA2.perfCfg.refresh) WA2.perfCfg.refresh(); } catch (e) {}
        if (cb) cb();
      })
      .catch(function () {});
  }
  loadIndex(null);

  /* #19：performance.json 里的映射表是「覆盖/追加」，没配就用内置表 */
  function cfgOverride(name) {
    try {
      if (WA2.perfCfg && typeof WA2.perfCfg[name] === "function") return WA2.perfCfg[name]() || {};
    } catch (e) {}
    return {};
  }

  function mergeMap(base, extra) {
    var out = {}, k;
    for (k in base) { if (Object.prototype.hasOwnProperty.call(base, k)) out[k] = base[k]; }
    for (k in extra) { if (Object.prototype.hasOwnProperty.call(extra, k)) out[k] = extra[k]; }
    return out;
  }

  /* 名字 / 旧编号 / label → index 里的衣服名（合并过的衣服有多个编号，都在 codes 里） */
  function resolveOutfit(v) {
    v = (v || "").trim();
    if (!v) return "";
    if (SPRITE_INDEX[v]) return v;
    for (var k in SPRITE_INDEX) {
      var o = SPRITE_INDEX[k] || {};
      if (o.code === v || o.label === v) return k;
      if (o.codes && o.codes.indexOf(v) >= 0) return k;
    }
    return "";
  }

  function outfitOf(scene) {
    var name = mergeMap(OUTFIT_BY_SCENE, cfgOverride("outfitMap"))[scene];
    if (name && SPRITE_INDEX[name]) return name;
    /* 场景表没命中（或这套衣服没有立绘）：优先校服，再退到索引里的第一套 */
    var keys = Object.keys(SPRITE_INDEX);
    for (var i = 0; i < keys.length; i++) {
      var o = SPRITE_INDEX[keys[i]] || {};
      if (String(o.code || "") === "001") return keys[i];
    }
    return keys.length ? keys[0] : "";
  }

  // 程序情绪（detectMood 给的英文）→ 立绘表情名。
  // 素材里表情名大多是**中文**（认真/微笑/生气/冷淡/惊讶/其他/其他2），
  // 所以这里直接映射到中文名；老素材用英文名时靠下面的别名表兜底。
  var MOOD_TO_EMO = { angry: "生气", sad: "其他2", cold: "冷淡", smile: "微笑", warm: "微笑" };

  /* 情绪 → 立绘里可能叫的名字（按顺序试，第一个存在的就用）。
     模型 <emotion> 只会给这几个词：calm / soft / 毒舌 / 撒娇 / 哭腔 / 调侃吐槽 / 问句；
     本地 detectMood 给的是 calm / warm / sad / angry / smile。
     以前这里只认英文名、素材里又是中文名，查不到就一律退回「认真」，
     表现是「表情永远不变」（2026-11-06 用户反馈）。 */
  var EMO_ALIAS = {
    calm: ["认真", "calm", "serious"],
    soft: ["微笑", "soft", "smile"],
    warm: ["微笑", "warm", "smile"],
    smile: ["微笑", "smile"],
    "撒娇": ["害羞", "微笑", "其他", "smile"],
    "调侃吐槽": ["微笑", "其他", "smile"],
    "毒舌": ["冷淡", "生气", "cold", "angry"],
    cold: ["冷淡", "cold"],
    angry: ["生气", "angry"],
    sad: ["难过", "其他2", "其他", "sad"],
    "哭腔": ["难过", "其他2", "其他", "sad"],
    "问句": ["认真", "serious"],
    "惊讶": ["惊讶", "surprised"],
    "沉重": ["冷淡", "生气", "难过", "cold", "sad"],
    /* 新标签复用现有表情：立绘只有那么几张，按"最接近"的映射 */
    "紧张": ["紧张", "认真", "serious"],
    "激动": ["惊讶", "生气", "surprised"],
    "委屈巴巴": ["难过", "害羞", "sad"],
    "自怨自艾": ["难过", "其他2", "sad"],
    "故作轻松": ["微笑", "calm", "smile"],
    "气喘吁吁": ["害羞", "惊讶", "shy"],
    "H": ["害羞", "其他", "shy"],
    "睡觉时的呼吸": ["平静", "calm"],
    shy: ["害羞", "其他"]
  };

  /* 挑一张表情图：先试模型/程序给的词，再试覆盖表，最后按别名表挨个试 */
  function pickEmotion(o, emotion) {
    var em = (o && o.emotions) || {};
    var raw = emotion || "";
    if (!raw) return "";
    var override = cfgOverride("moodMap") || {};
    var cands = [raw, override[raw], MOOD_TO_EMO[raw]]
      .concat(EMO_ALIAS[raw] || [])
      .concat(EMO_ALIAS[override[raw]] || [])
      .concat(EMO_ALIAS[MOOD_TO_EMO[raw]] || []);
    for (var i = 0; i < cands.length; i++) {
      if (cands[i] && em[cands[i]]) return em[cands[i]];
    }
    return "";
  }

  function spritePath(outfit, emotion) {
    var o = SPRITE_INDEX[outfit];
    if (!o) return "assets/kaz_cold.png";
    var u = function (name) {
      var url = "/api/character/sprite/" + encodeURIComponent(outfit) + "/" + encodeURIComponent(name);
      /* #20 热重载：换了图之后靠 ?v= 让 WebView2 重新取，不然还吃旧缓存 */
      if (WA2.assetsVer) url += "?v=" + WA2.assetsVer;
      return url;
    };
    var file = pickEmotion(o, emotion);
    if (file) return u(file);
    // 该套衣服没有这个情绪 → 退到「认真」，再退到默认
    if (o.emotions && (o.emotions.认真 || o.emotions.serious)) return u(o.emotions.认真 || o.emotions.serious);
    return u(o.default);
  }

  /** 按场景切换服装 */
  function setSpriteByScene(scene, mood, force, wantOutfit) {
    // 服装以 WA2.state.outfit 为准；只有对话明确换衣服（wantOutfit）才改
    var cur = (WA2.state && WA2.state.outfit) || "";
    /* 顺序很重要：以前是「上次的衣服」压过「场景对应的衣服」，
       结果换场景时立绘永远穿着同一套（看着就是“立绘不切换”）。
       现在：模型明确换衣服 > 场景表 > 上次的衣服 > 场景兜底。 */
    var outfit = resolveOutfit(wantOutfit) || outfitOf(scene) || resolveOutfit(cur) || "";
    var key = outfit + "|" + (mood || "");
    if (currentSprite === key && !force) return;
    if (WA2.state) WA2.state.outfit = outfit;
    var sp = $("sprite");
    if (!sp) return;
    currentSprite = key;

    /* 立绘缺图时别让画面留个破图标：藏起来（还在淡入的旧图保留）、提示一次 */
    var onMissing = function () {
      var fading = false;
      try { fading = !!(WA2.fade && WA2.fade.enabled()); } catch (e) {}
      if (!fading) sp.classList.remove("show");
      if (!window.__spriteWarned) {
        window.__spriteWarned = true;
        (window.__toast || function () {})("立绘缺失，先用文字继续", 2500);
      }
    };

    /* 双缓冲交叉淡化：新图先预载，载完叠一层淡入，淡完把主图换掉（见 wt-fade.js） */
    try {
      if (WA2.fade && WA2.fade.crossSprite) { WA2.fade.crossSprite(spritePath(outfit, mood), null, onMissing); return; }
    } catch (e) {}

    /* 兜底（wt-fade 没加载时）：一藏一换的老做法 */
    sp.classList.remove("show");
    setTimeout(function () {
      sp.onerror = onMissing;
      sp.src = spritePath(outfit, mood);
      sp.classList.add("show");
    }, 180);
  }

  /* 切换 / 新建角色之后重拉索引，再强制重画一次当前立绘 */
  function reload(scene, emo) {
    loadIndex(function () {
      currentSprite = "";
      try { setSpriteByScene(scene || "classroom", emo || "", true); } catch (e) {}
    });
  }

  function summary() {
  var outfits = 0, files = 0;
    for (var k in SPRITE_INDEX) {
      outfits++;
      var vs = (SPRITE_INDEX[k] || {}).variants || [];
      files += vs.length;
    }
    return outfits ? (outfits + " 套衣服 · " + files + " 张") : "";
  }

  /* #20 热重载：素材变了就按「当前这套衣服 + 当前情绪」重画一次（URL 带 ?v= 绕过缓存） */
  function refreshCurrent() {
    if (!currentSprite) return false;
    var parts = String(currentSprite).split("|");
    var scene = (WA2.state && WA2.state.scene) || "";
    try {
      setSpriteByScene(scene, parts[1] || "", true, parts[0] || "");
      return true;
    } catch (e) {
      return false;
    }
  }

  WA2.sprite = {
    resolveOutfit: resolveOutfit,
    outfitOf: outfitOf,
    spritePath: spritePath,
    setSpriteByScene: setSpriteByScene,
    reload: reload,
    refreshCurrent: refreshCurrent,
    summary: summary,
    index: SPRITE_INDEX
  };

  // 兼容老调用点（game.js / menubar.js 直接用了这些全局名）
  window.__reloadSprites = function () { reload(window.__curBg || "classroom", window.__lastEmo || ""); };
  var _exp = {};
  var _names = ["loadIndex", "outfitOf", "reload", "refreshCurrent", "resolveOutfit", "setSpriteByScene", "spritePath", "summary"];
  for (var _i = 0; _i < _names.length; _i++) {
    try { var _f = eval(_names[_i]); if (typeof _f === "function") { _exp[_names[_i]] = _f; window[_names[_i]] = _f; } } catch (e) {}
  }
  WA2.sprite = _exp;
  WA2.register("sprite", _exp);
  /* __export_all__ */
  ["loadIndex", "outfitOf", "reload", "resolveOutfit", "setSpriteByScene", "spritePath", "summary", "MOOD_TO_EMO", "OUTFIT_BY_SCENE", "SPRITE_INDEX", "WA2", "_exp", "_names", "cur", "currentSprite", "emo", "key", "keys", "name", "o", "outfit", "outfits", "sp", "u", "vs"].forEach(function (n) {
    try { var v = eval(n); if (v !== undefined) { window[n] = v; } } catch (e) {}
  });
})();

/* 兼容层（必须在 IIFE 之后）：special.js 用 __spriteSummary；面板统计读 WA2.sprite.index */
(function () {
  var s = window.WA2 && window.WA2.sprite;
  if (!s) { return; }
  s.index = window.SPRITE_INDEX || {};
  window.__spriteSummary = s.summary || function () { return ""; };
})();
