/* mood.js —— 从 game.js 搬出的功能块（逻辑未改）
 * 挂载：WA2.mood；同时同步到 window 以兼容旧调用。
 */
(function () {
  var WA2 = window.WA2 = window.WA2 || {};
  var $ = (WA2.util && WA2.util.$) || function (id) { return document.getElementById(id); };

  function detectMood(text) {
    const t = text || "";
    if (/滚|烦|吵|闭嘴|笨蛋|无聊|别管|哼|少来|自作多情/.test(t)) return "angry";
    if (/抱歉|对不起|哭|寂寞|后悔|离开|再见|讨厌我/.test(t)) return "sad";
    if (/早点睡|注意|吃药|别勉强|你也|小心|累了吧|照顾好/.test(t)) return "warm";
    if (/呵|笑|有趣|真拿你没办法|好吧/.test(t)) return "smile";
    if (/……|没事|没什么|有事|随便|不知道/.test(t)) return "calm";
    return "calm";
  }

  const MOOD_BGM = { angry: "tense", sad: "sad", warm: "warm", smile: "warm", calm: "calm" };

  /* 每个氛围允许多首：切到该氛围时轮流挑一首，避免同一首听一整局。
     顺序就是「更常用 → 更有画面感」。 */
  var SLOT_TRACKS = {
    calm: ["calm", "kazusa"],
    warm: ["warm", "kazusa"],
    sad: ["sad", "toukanai"],
    tense: ["tense", "answer"]
  };
  /* 曲库 41 首按 slot 字段分组；有 catalog 就用它，没有就退回上面那 4 组 */
  (function buildSlotTracks() {
    try {
      var C = (window.App && window.App.catalogGet) ? window.App.catalogGet() : null;
      var list = (C && C.bgm) || [];
      if (!list.length) return;
      var by = {};
      for (var i = 0; i < list.length; i++) {
        var it = list[i];
        if (!it || !it.slot || it.slot === "menu") continue;
        (by[it.slot] || (by[it.slot] = [])).push(it.name);
      }
      var keys = Object.keys(by);
      if (!keys.length) return;
      for (var k = 0; k < keys.length; k++) SLOT_TRACKS[keys[k]] = by[keys[k]];
    } catch (e) {}
  })();
  /* 反查：某首曲子属于哪个氛围（判断「当前氛围」用） */
  var TRACK_SLOT = {
    menu: "menu", calm: "calm", kazusa: "calm", warm: "warm",
    sad: "sad", toukanai: "sad", tense: "tense", answer: "tense"
  };
  var slotRound = {};

  function nextTrack(slot) {
    var list = SLOT_TRACKS[slot] || [slot];
    var i = (slotRound[slot] = (slotRound[slot] || 0) + 1) - 1;
    return list[i % list.length];
  }
  /* 情绪→表情不在这里：立绘那边已改成中文名别名表（wt-sprite.js 的 EMO_ALIAS），
     这里的 MOOD_SPRITE 早就没人用了，删掉省得以后两边映射打架。 */

  /* ---------- BGM 槽位：优先信模型给的 <emotion>，没有才回退到文本规则 ----------
     2026-11-06 用户反馈「BGM 只在寂静的冬夜和冰之刃之间切」。
     实测（用本机 8 个存档、473 句真实台词跑）：正则判出 angry 11%，靠累积算法
     最终只在 calm↔tense 之间跳；而模型给的 <emotion> 里根本没有「沉重」这个词，
     于是「争执/分手」和「日常嘴硬」走的是同一条路 —— 冰之刃满天飞。
     现在：毒舌/问句算日常（calm），soft/撒娇/调侃吐槽算温柔（warm），
     哭腔算难过（sad），「沉重」留给真的争执（词表里已补，见 performance_hints）。 */
  var EMO_BGM = {
    calm: "calm", "毒舌": "calm", "问句": "calm",
    soft: "warm", "撒娇": "warm", "调侃吐槽": "comic",
    "哭腔": "sad",
    "沉重": "tense",
    /* 新标签：按"这段该配什么氛围"归到四个槽位 */
    "紧张": "tense",
    "激动": "answer",
    "委屈巴巴": "sad",
    "自怨自艾": "sad",
    "故作轻松": "calm",
    "气喘吁吁": "warm",
    "H": "warm",
    "睡觉时的呼吸": "calm"
  };
  /* 没有 <emotion> 时的回退规则（分两档：真的伤人话才算 tense，嘴硬不算） */
  var TEXT_SLOT = [
    ["tense", /分手|别碰我|滚出去|恨你|讨厌你|离开我|受够|够了[，,。]|再也别|不要再/],
    ["sad", /哭|寂寞|后悔|对不起|抱歉|难过|别走/],
    ["warm", /早点睡|注意身体|吃药|别勉强|小心|累了吧|照顾好|谢谢你|辛苦了/],
    ["warm", /呵|笑|有趣|真拿你没办法|好吧/],
    // 「笨蛋/哼/少来/别管/烦」是她日常的嘴硬，落回 calm，不再算 tense
  ];

  /* 唱歌 / 演出这类场面：热闹、往上走，不该配「氷の刃」这种紧张曲 */
  var MUSIC_RE = /歌|唱|舞台|学园祭|学園祭|演奏|合练|排练|ライブ|バンド|乐队|主唱|合唱/i;

  function bgmSlot(text, emo) {
    var e = (emo || "").trim();
    if (MUSIC_RE.test(text || "")) return "answer";
    if (e && EMO_BGM[e]) return EMO_BGM[e];
    var t = text || "";
    for (var i = 0; i < TEXT_SLOT.length; i++) {
      if (TEXT_SLOT[i][1].test(t)) return TEXT_SLOT[i][0];
    }
    return "calm";
  }

  /* ---------- 氛围累积（BGM 不随单句变化） ---------- */
  const moodScore = { calm: 0, warm: 0, sad: 0, tense: 0, comic: 0, kazusa: 0, answer: 0 };
  let moodStable = { name: "calm", turns: 0 };

  function accumulateMood(reply, emo) {
    const bgm = bgmSlot(reply, emo);
    // 每轮衰减，越近的话权重越大（0.65 太黏：calm 占八成时谁都翻不了身，
    // 实测 473 句只切 2 次；0.5 + 差 1.5 分能到 9 次，且 warm/sad 都能出现）
    for (const k in moodScore) moodScore[k] *= 0.50;
    moodScore[bgm] = (moodScore[bgm] || 0) + 2;
    // 长句、强烈情绪加权
    if ((reply || "").length > 40) moodScore[bgm] += 0.8;

    // 当前氛围的分数
    let best = "calm", bestV = -1;
    for (const k in moodScore) {
      if (moodScore[k] > bestV) { bestV = moodScore[k]; best = k; }
    }
    const curV = moodScore[currentBgmMood()] || 0;

    // 需要连续两轮保持领先才切换
    if (best !== moodStable.name) {
      moodStable = { name: best, turns: 1 };
    } else {
      moodStable.turns++;
    }
    const enough = bestV - curV >= 1.5 && moodStable.turns >= 2;
    if (best !== currentBgmMood() && enough) {
      setBgm(nextTrack(best));
    }
  }

  function currentBgmMood() {
    var cur = WA2.audio.current || "";
    if (TRACK_SLOT[cur]) return TRACK_SLOT[cur];
    for (const k in MOOD_BGM) {
      if (MOOD_BGM[k] === cur) return k === "smile" ? "warm" : MOOD_BGM[k];
    }
    return cur || "calm";
  }

  /* 开新游戏：氛围分归零（不然上一局的「难过/紧张」会带进新局，BGM 一开就偏） */
  function reset() {
    for (const k in moodScore) moodScore[k] = 0;
    for (const k in slotRound) slotRound[k] = 0;
    moodStable = { name: "calm", turns: 0 };
    return true;
  }


  /* 合并版：按当前角色给开场白 */
  var OPENING_LINE = { zh: "「……」", ja: "", emo: "calm" };
  var _exp = {};
  var _names = ["accumulateMood", "currentBgmMood", "detectMood", "bgmSlot", "reset"];
  for (var _i = 0; _i < _names.length; _i++) {
    try { var _f = eval(_names[_i]); if (typeof _f === "function") { _exp[_names[_i]] = _f; window[_names[_i]] = _f; } } catch (e) {}
  }
  WA2.mood = _exp;
  WA2.register("mood", _exp);
  /* __export_all__ */
  ["accumulateMood", "currentBgmMood", "detectMood", "bgmSlot", "MOOD_BGM", "EMO_BGM", "OPENING_LINE", "WA2", "_exp", "_names", "best", "bgm", "curV", "enough", "mood", "moodScore", "moodStable", "reset", "t"].forEach(function (n) {
    try { var v = eval(n); if (v !== undefined) { window[n] = v; } } catch (e) {}
  });
})();
