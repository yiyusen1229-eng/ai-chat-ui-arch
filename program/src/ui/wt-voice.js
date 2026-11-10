/* voice.js —— 从 game.js 搬出的功能块（逻辑未改）
 * 挂载：WA2.voice；同时同步到 window 以兼容旧调用。
 */
(function () {
  var WA2 = window.WA2 = window.WA2 || {};
  var $ = (WA2.util && WA2.util.$) || function (id) { return document.getElementById(id); };
  /* 桥接：emoOf 还在 game.js（全局函数），语音对象由本模块持有 */
  var emoOf = function (t) { return window.emoOf ? window.emoOf(t) : "calm"; };
  var ttsAudio = null;
  function _tts(v) { if (v !== undefined) ttsAudio = v; return ttsAudio; }

  function currentVoiceLang() {
    // 每次都用最新设置（菜单切换后立即生效）
    try {
      return localStorage.getItem("app_voiceLang") || window.voiceLang || "zh";
    } catch (e) {
      return "zh";
    }
  }

  function stopSpeak() {
    try { if (_voiceResolve) { _voiceResolve(false); _voiceResolve = null; } } catch (e) {}
    if (ttsAudio) { try { ttsAudio.pause(); } catch (e) {} ttsAudio = null; }
    _devState = null;
  }

  /* ---------- 音频设备变化（#7）----------
     拔耳机 / 切蓝牙之后，正在播的 <audio> 还挂在已经不存在的设备上：
     记下位置、暂停；设备重建完再从原位置续播。续不上由调用方提示「重播这句」。 */
  var _devState = null;

  function suspendForDevice() {
    if (!ttsAudio) return false;
    try {
      _devState = { t: ttsAudio.currentTime || 0, playing: !ttsAudio.paused };
      ttsAudio.pause();
    } catch (e) {
      _devState = null;
      return false;
    }
    return !!(_devState && _devState.playing);
  }

  function resumeAfterDevice() {
    var st = _devState;
    _devState = null;
    if (!st || !ttsAudio) return Promise.resolve(false);
    var a = ttsAudio;
    return new Promise(function (resolve) {
      try {
        if (st.t > 0) { try { a.currentTime = st.t; } catch (e) {} }
        try { a.volume = voiceVolume(); } catch (e) {}
        var p = a.play();
        if (p && p.then) p.then(function () { resolve(true); }).catch(function () { resolve(false); });
        else resolve(true);
      } catch (e) {
        resolve(false);
      }
    });
  }

  /** 合成语音并返回 Audio（失败或超时返回 null） */
  /* 这次合成该用哪个情绪参考：
     模型标签优先，但当前场面是特殊场面时，平静/温柔/空 一律按特殊参考走
     （后端只会纠正很短的纯拟声句，稍长的台词会漏过去，听起来就变平静）。 */
  function emoFor(text) {
    var e = (window.__lastEmo || (window.emoOf ? window.emoOf(text) : "") || "").trim();
    var low = e.toLowerCase();
    try {
      if (document.body.dataset.hscene === "1" &&
          (!e || low === "calm" || low === "soft" || low === "neutral" || low === "calm(平静)")) {
        e = "H";
      }
    } catch (err) {}
    return e;
  }

  /* 这句该按什么语言合成：
     · 有日文原文（单角色正常对话）→ ja；
     · 没有日文原文但知道说话人（导演写的台词）→ ja，让后端先翻成日文；
     · 其它情况尊重设置（默认 zh）。 */
  function voiceLangFor(text, speaker) {
    if (window.__speakJa) return "ja";
    if (speaker) return "ja";
    return currentVoiceLang();
  }

  /* 说话人 → &char=xxx（队列里别人的台词要用他自己的音色） */
  function charQ(speaker) {
    var s = (speaker || "").trim();
    return s ? "&char=" + encodeURIComponent(s) : "";
  }

  function fetchVoice(text, timeoutMs, speaker) {
    if (!ttsOn) return Promise.resolve(null);
    /* 把这次真正用到的参数记下来：回想重放要原样复用，否则参数一漂就缓存未命中
       （用户报「同一句话重放还要等加载」）。 */
    try {
      window.__lastVoiceReq = {
        t: text,
        l: voiceLangFor(text, speaker),
        e: emoFor(text),
        c: speaker || ""
      };
    } catch (e) {}
    return new Promise((resolve) => {
      const ctrl = (typeof AbortController !== "undefined") ? new AbortController() : null;
      let done = false;
      /* 超时要随文本长度放宽：长句合成实测要 17~20 秒，固定 15 秒会把整句丢掉
         （表现就是「出字没出音」，稍后重播反而是好的 —— 服务端已经合成并缓存了）。 */
      const _budget = (timeoutMs && timeoutMs > 0)
        ? timeoutMs
        : Math.min(60000, 20000 + (text || "").length * 500);
      const timer = setTimeout(() => {
        if (done) return;
        done = true;
        if (ctrl) { try { ctrl.abort(); } catch (e) {} }
        console.warn("[voice] 等待超时（%dms），重试一次：", _budget, text && text.slice(0, 20));
        /* 超时后服务端往往还在合成（实测长句 17~20 秒）。再请求一次：
           它会排在合成锁后面，等前一次写进缓存后立刻命中 —— 通常这一下就有声了。 */
        setTimeout(function () {
          if (done) return;
          done = true;
          fetch("/api/tts?text=" + encodeURIComponent(text) + "&lang=" + voiceLangFor(text, speaker) +
                "&emotion=" + encodeURIComponent(emoFor(text)) + charQ(speaker) +
                (window.__speakJa ? "&speak_ja=" + encodeURIComponent(window.__speakJa) : ""))
            .then(function (r) { if (!r.ok) throw new Error("tts " + r.status); return r.blob(); })
            .then(function (blob) {
              const u = URL.createObjectURL(blob);
              const a = new Audio(u);
              a.onended = function () { URL.revokeObjectURL(u); };
              resolve(a);
            })
            .catch(function () { resolve(null); });
        }, 1500);
        return;
      }, _budget);
      fetch("/api/tts?text=" + encodeURIComponent(text) + "&lang=" + voiceLangFor(text, speaker) + "&emotion=" + encodeURIComponent(emoFor(text)) + charQ(speaker) + (window.__speakJa ? "&speak_ja=" + encodeURIComponent(window.__speakJa) : ""), ctrl ? { signal: ctrl.signal } : {})
        .then((r) => {
          if (!r.ok) throw new Error("tts " + r.status);
          return r.blob();
        })
        .then((blob) => {
          if (done) return;
          done = true; clearTimeout(timer);
          const url = URL.createObjectURL(blob);
          const a = new Audio(url);
          a.onended = () => { URL.revokeObjectURL(url); };
          resolve(a);
        })
        .catch(() => {
          if (done) return;
          done = true; clearTimeout(timer);
          /* 语音服务没起来时别静默失败：提示一次（同一局里不反复弹） */
          if (!window.__voiceWarned) {
            window.__voiceWarned = true;
          if (window.__saveHint) window.__saveHint("语音暂时不可用，先显示文字");
          }
          resolve(null);
        });
    });
  }


  /* ---------- 开场白语音 ---------- */
  function waitTtsReady(cb, tries) {
    if (!ttsOn) return;
    fetch("/api/voice/ok", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d && d.ready) cb();
        else if (tries > 0) setTimeout(function () { waitTtsReady(cb, tries - 1); }, 1000);
      })
      .catch(function () {
        if (tries > 0) setTimeout(function () { waitTtsReady(cb, tries - 1); }, 1000);
      });
  }

  function speakLineJa(ja, emo, speaker) {
    if (!ttsOn) return;
    var clean = (ja || "").replace(/^[「『]|[」』]$/g, "").trim();
    if (!clean) return;
    window.__speakJa = clean;
  window.__lastEmo = emo || window.__lastEmo || "soft";
    waitTtsReady(function () { speakReply(clean, null, speaker); }, 60);
  }

  /* 播放并**等它播完**再 resolve —— 自动续说靠这个避免"语音没说完就下一句"。
     onStart：音频真正开始播放时回调一次，文字/立绘要跟它同时出，
     不能等播完（否则就是「语音响了，字还没出」）。 */
  function speakReply(text, onStart, speaker) {
    stopSpeak();
    return fetchVoice(text, 0, speaker).then((a) => new Promise((resolve) => {
      if (!a) { resolve(false); return; }
      ttsAudio = a;
      _voiceResolve = resolve;          /* stopSpeak 里用它收尾，别让 Promise 挂着 */
      try { a.volume = voiceVolume(); } catch (e) {}
      var fired = false;
      var fire = function () {
        if (fired) return;
        fired = true;
        _voiceResolve = null;
        try { onStart && onStart(); } catch (e) {}
      };
      var finish = function (ok) { fire(); resolve(ok); };
      /* 兜底：音频时长 + 3 秒还没结束（被暂停/切走/播放器卡住）也放行 */
      var _dur = (a.duration && isFinite(a.duration)) ? a.duration : 12;
      setTimeout(function () { finish(true); }, (_dur + 3) * 1000);
      a.addEventListener("playing", fire, { once: true });   /* 真正开始出声 */
      a.addEventListener("ended", function () { finish(true); }, { once: true });
      a.addEventListener("pause", function () { finish(true); }, { once: true });   /* stopSpeak 会 pause */
      a.addEventListener("error", function () { finish(false); }, { once: true });
      try {
        var pr = a.play();
        if (pr && pr.then) pr.then(fire, function () { finish(false); });
      } catch (e) { finish(false); }
    }));
  }
  var _voiceResolve = null;
  var _exp = {};
  var _names = ["emoFor", "_tts", "currentVoiceLang", "fetchVoice", "speakLineJa", "speakReply", "stopSpeak",
                "suspendForDevice", "resumeAfterDevice", "waitTtsReady"];
  for (var _i = 0; _i < _names.length; _i++) {
    try { var _f = eval(_names[_i]); if (typeof _f === "function") { _exp[_names[_i]] = _f; window[_names[_i]] = _f; } } catch (e) {}
  }
  WA2.voice = _exp;
  WA2.register("voice", _exp);
  /* __export_all__ */
  ["emoFor", "_tts", "currentVoiceLang", "fetchVoice", "speakLineJa", "speakReply", "stopSpeak", "waitTtsReady", "WA2", "_exp", "_names", "a", "clean", "ctrl", "done", "emoOf", "timer", "ttsAudio", "url"].forEach(function (n) {
    try { var v = eval(n); if (v !== undefined) { window[n] = v; } } catch (e) {}
  });
})();
