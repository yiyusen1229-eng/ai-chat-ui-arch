/* wt-audio.js —— BGM / SE / 语音音量（从 game.js 搬出来的第一个功能模块）
 *
 * 对外（挂在 WA2.audio）：
 *   bgmSrc(name)        名字 → 音频地址
 *   bgmVolume()         当前 BGM 音量（0~1，已乘全体音量）
 *   voiceVolume()       当前语音音量（0~1，已乘全体音量）
 *   playSE(kind)        播放点击/翻页/取消等音效
 *   setBgm(name, ms)    淡出换 BGM（ms 是淡入淡出时长）
 *   pauseBgm()          暂停 BGM（静音开关用）
 *   applyVolume()       按 prefs 立即应用音量，返回 {bgm, voice}
 *   current / enabled   BGM 名字 / BGM 开关
 */
(function () {
  var WA2 = window.WA2 = window.WA2 || {};

  var BGM_KEY = { menu: "bgmMenu", calm: "bgmCalm", warm: "bgmWarm", sad: "bgmSad", tense: "bgmTense",
                 kazusa: "bgmKazusa", toukanai: "bgmToukanai", answer: "bgmAnswer" };
  var SE_BASE = "assets/snd/";
  var seCache = {};

  function prefs() { return (window.App && window.App.getPrefs) ? window.App.getPrefs() : {}; }
  function catalog() { return (window.App && window.App.catalogGet) ? window.App.catalogGet() : { bgm: [], se: [] }; }

  function bgmSrc(name) {
    var P = prefs(), C = catalog();
    var id = P[BGM_KEY[name] || "bgmCalm"];
    var item = C.bgm && C.bgm[id === undefined ? 0 : id];
    return item ? "assets/snd/" + item.file : "";
  }

  /* OPTION 2 的 BGM 滑块优先（volBgm），没有就退回旧的 bgmVol；两者都要乘全体音量 */
  function bgmVolume() {
    var P = prefs();
    var b = (P.volBgm !== undefined ? P.volBgm : (P.bgmVol === undefined ? 28 : P.bgmVol));
    var m = (P.volMaster === undefined ? 100 : P.volMaster) / 100;
    return (b / 100) * m;
  }

  /* 语音音量：VOICE 滑块 × 全体音量 */
  function voiceVolume() {
    var P = prefs();
    var v = (P.volVoice === undefined ? 90 : P.volVoice);
    var m = (P.volMaster === undefined ? 100 : P.volMaster) / 100;
    return (v / 100) * m;
  }

  function playSE(kind) {
    var P = prefs(), C = catalog();
    var key = kind === "exit" ? (P.seExit || 58)
      : kind === "page" ? (P.sePage || 57)
        : (kind === "click" ? P.seClick : (kind === "cancel" ? P.seCancel : P.seHover));
    var item = C.se && C.se[key || 0];
    if (!item) return;
    var master = (P.volMaster === undefined ? 100 : P.volMaster) / 100;
    var vol = ((P.seVol === undefined ? 35 : P.seVol) / 100) * master;
    var a = seCache[item.file];
    if (!a) { a = new Audio(SE_BASE + item.file); a.preload = "auto"; seCache[item.file] = a; }
    a.volume = vol;
    try { a.currentTime = 0; a.play().catch(function () {}); } catch (e) {}
  }

  var bgm = new Audio();
  bgm.loop = true;
  bgm.volume = 0;
  bgm.preload = "auto";

  var state = { current: null, enabled: true };

  /* 设置界面改音量时立即生效 */
  window.addEventListener("app:prefs", function (e) {
    var P = e.detail || {};
    if (bgm && !bgm.paused) {
      var b = (P.volBgm !== undefined ? P.volBgm : (P.bgmVol === undefined ? 30 : P.bgmVol));
      var m = (P.volMaster === undefined ? 100 : P.volMaster) / 100;
      bgm.volume = (b / 100) * m;
    }
  });

  function setBgm(name, fadeMs) {
    if (!state.enabled) return;
    fadeMs = (fadeMs === undefined) ? 1200 : fadeMs;
    var src = bgmSrc(name);
    if (state.current === name && !bgm.paused) return;
    state.current = name;
    var from = bgm.volume;
    var fadeOut = setInterval(function () {
      bgm.volume = Math.max(0, bgm.volume - from / 12);
    }, fadeMs / 12);
    setTimeout(function () {
      clearInterval(fadeOut);
      bgm.src = src;
      bgm.volume = 0;
      bgm.play().then(function () {
        var to = bgmVolume();
        var v = 0;
        var fadeIn = setInterval(function () {
          v += to / 14;
          bgm.volume = Math.min(to, v);
          if (v >= to) clearInterval(fadeIn);
        }, fadeMs / 14);
      }).catch(function () {});
    }, fadeMs);
  }

  function pauseBgm() {
    try { bgm.pause(); } catch (e) {}
  }

  /* ---------- 音频设备变化（#7）----------
     拔耳机 / 切蓝牙之后，正在播的 <audio> 还挂在已经不存在的设备上，表现是
     「进度在走、没声音」或者干脆停住。处理办法：
       1. 立刻暂停（别让进度空跑），记下 BGM / 语音各自的播放位置
       2. 等 600ms 让系统把默认设备切完
       3. 重建播放器再续播：续不上就提示「重播这句」，不静默失败
     BGM 换一个新 Audio 元素（元素绑的是创建时的设备）；语音由 wt-voice 负责续播。 */
  var DEV = { supported: false, changes: 0, lastAt: 0 };
  var deviceTimer = null;

  function notifyReplay() {
    state.needsReplay = true;
    var msg = "音频设备已切换，若没声音请重播这句";
    try {
      if (window.__saveHint) window.__saveHint(msg);
      else if (window.__toast) window.__toast(msg, 2600);
    } catch (e) {}
  }

  /* 暂停并把「刚才在播什么」记下来 */
  function suspendForDevice() {
    var was = { bgm: false, voice: false, vol: 0, src: "" };
    try {
      if (bgm && bgm.src && !bgm.paused) {
        was.bgm = true;
        was.src = bgm.src;
        was.vol = bgm.volume;
        bgm.pause();
      }
    } catch (e) {}
    try {
      was.voice = !!(WA2.voice && WA2.voice.suspendForDevice && WA2.voice.suspendForDevice());
    } catch (e) {}
    return was;
  }

  function rebuildAfterDevice(was) {
    if (!was) return;
    if (was.bgm) {
      try {
        var src = was.src || bgm.src;
        var vol = was.vol || bgmVolume();
        var old = bgm;
        bgm = new Audio();
        bgm.loop = true;
        bgm.preload = "auto";
        bgm.volume = vol;
        bgm.src = src;
        var p = bgm.play();
        if (p && p.catch) p.catch(function () { notifyReplay(); });
        try { if (old) old.pause(); } catch (e) {}
      } catch (e) {
        notifyReplay();
      }
    }
    if (was.voice) {
      var vp = null;
      try { vp = WA2.voice.resumeAfterDevice ? WA2.voice.resumeAfterDevice() : null; } catch (e) {}
      if (vp && vp.then) vp.then(function (ok) { if (!ok) notifyReplay(); }).catch(function () { notifyReplay(); });
      else if (!vp) notifyReplay();
    }
  }

  function onDeviceChange() {
    DEV.changes++;
    DEV.lastAt = Date.now();
    try { if (window.__dbg) window.__dbg("AUDIO", "devicechange #" + DEV.changes); } catch (e) {}
    var was = suspendForDevice();
    if (deviceTimer) clearTimeout(deviceTimer);
    deviceTimer = setTimeout(function () {
      deviceTimer = null;
      rebuildAfterDevice(was);
    }, 600);
  }

  function installDeviceWatcher() {
    try {
      var md = (typeof navigator !== "undefined") ? navigator.mediaDevices : null;
      if (!md || !md.addEventListener) return false;
      md.addEventListener("devicechange", onDeviceChange);
      DEV.supported = true;
      return true;
    } catch (e) {
      DEV.supported = false;
      return false;
    }
  }
  installDeviceWatcher();

  /* 立即按 prefs 应用音量（给 __applyVolumes 用） */
  function applyVolume() {
    var bv = 0, vv = 0;
    try { bv = bgmVolume(); } catch (e) {}
    try { vv = voiceVolume(); } catch (e) {}
    try { bgm.volume = bv; } catch (e) {}
    return { bgm: bv, voice: vv };
  }

  /* 对外接口：函数从模块内部取，状态用 getter 暴露（见下面的 defineProperty） */
  var _exp = {};
  var _names = ["applyVolume", "bgmSrc", "bgmVolume", "catalog", "pauseBgm", "playSE", "prefs", "setBgm", "voiceVolume",
                "suspendForDevice", "rebuildAfterDevice", "onDeviceChange"];
  for (var _i = 0; _i < _names.length; _i++) {
    try { var _f = eval(_names[_i]); if (typeof _f === "function") { _exp[_names[_i]] = _f; window[_names[_i]] = _f; } } catch (e) {}
  }
  WA2.audio = _exp;
  /* 状态项在这里补成 getter/setter：直接挂属性的话，模块内部改 state 外面看不到，
     外面的「静音开关」也改不到模块内部（这两个以前是对不上的）。 */
  try {
    Object.defineProperty(_exp, "current", {
      get: function () { return state.current; },
      set: function (v) { state.current = v; }
    });
    Object.defineProperty(_exp, "enabled", {
      get: function () { return state.enabled; },
      set: function (v) { state.enabled = !!v; }
    });
    Object.defineProperty(_exp, "device", { get: function () { return DEV; } });
  } catch (e) {}
  WA2.register("audio", _exp);
  /* __export_all__ */
  ["applyVolume", "bgmSrc", "bgmVolume", "catalog", "pauseBgm", "playSE", "prefs", "setBgm", "voiceVolume", "DEV", "BGM_KEY", "P", "SE_BASE", "WA2", "_exp", "_names", "a", "b", "bgm", "bv", "fadeIn", "fadeOut", "from", "id", "item", "key", "m", "master", "seCache", "src", "state", "to", "v", "vol"].forEach(function (n) {
    try { var v = eval(n); if (v !== undefined) { window[n] = v; } } catch (e) {}
  });
})();
