/* wt-play.js —— 播放队列的消费端
 *
 * 从后端取「拍」显示到对话框：
 *   · 角色台词 → 「名字：「…」」，若是当前角色（有音色）就同时出语音
 *   · 旁白     → 直接显示，不出声
 *   · 翻页节奏：按字数给阅读时间（最短 2.2 秒），有语音就等语音播完
 *   · 队列空了、多角色开着，就请导演再写一拍（5 秒冷却）
 */
(function () {
  var WA2 = window.WA2 = window.WA2 || {};
  var timer = null;
  var busy = false;
  var holdUntil = 0;
  var lastGen = 0;
  var genOn = false;

  function idle() {
    try {
      if (WA2.chat && WA2.chat.isBusy && WA2.chat.isBusy()) return false;
    } catch (e) {}
    var g = document.getElementById("gameScreen");
    if (g && g.className.indexOf("active") < 0) return false;
    return true;
  }

  function readMs(text) {
    var n = (text || "").length;
    /* 用户定的：没语音时每个字 300ms */
    return Math.min(20000, Math.max(1200, Math.round(n * 300)));
  }

  function sleep(ms) {
    return new Promise(function (res) { setTimeout(res, ms); });
  }

  /* 当前角色的名字：只有她/他有音色 */
  /* 当前角色名：CHAR_NAME 是 game.js 里的 let（不在 window 上），
     只能靠词法环境读，读不到再从名字牌兜底 */
  function ownName() {
    try {
      if (typeof CHAR_NAME !== "undefined" && CHAR_NAME) return String(CHAR_NAME);
    } catch (e) {}
    var el = document.querySelector("#namePlate");
    if (!el) return "";
    var im = el.querySelector("img");
    if (im && im.getAttribute("data-uitxt")) return im.getAttribute("data-uitxt");
    return (el.textContent || "").trim();
  }

  /* 名字牌：说话人是谁就写谁；旁白清空 */
  function setName(name) {
    var el = document.querySelector("#namePlate");
    if (!el) return;
    if (!name) {
      el.textContent = "";
      el.style.visibility = "hidden";
      return;
    }
    el.style.visibility = "";
    try {
      if (typeof gdiRenderName === "function") {
        gdiRenderName(name, el);
        return;
      }
    } catch (e) {}
    el.textContent = name;
  }

  function show(line) {
    if (!line || !line.text) return Promise.resolve(0);
    var speaker = line.speaker || "";
    var text = line.text;
    var isNarr = (line.type === "narr") || !speaker;
    /* 名字交给 applyServerReply 在出字那一刻画，两边同时出现 */
    /* 旁白去掉外面的括号 */
    var body = isNarr
      ? text.replace(/^[（(【\[「]+/, "").replace(/[）)】\]」]+$/, "").trim()
      : text;

    var p = Promise.resolve();
    try {
      if (WA2.chat && WA2.chat.renderQueued) p = WA2.chat.renderQueued({ reply: body });
    } catch (e) {}
    if (!p || !p.then) {
      var el = document.getElementById("msgText");
      if (el) el.textContent = body;
      p = Promise.resolve();
    }

    return p.then(function () {
      /* 有语音的角色：出声音（旁白、龙套、其他角色都不出声） */
      var voiced = !isNarr && line.voice && speaker && speaker === ownName();
      if (voiced && WA2.voice && WA2.voice.speakReply) {
        try {
          return WA2.voice.speakReply(text).then(function () { return readMs(text); });
        } catch (e) {}
      }
      return readMs(text);
    }).then(function (ms) {
      return sleep(ms || 0).then(function () { return ms; });
    });
  }

  function maybeGenerate() {
    if (!genOn) return Promise.resolve(false);
    var now = Date.now();
    if (now - lastGen < 5000) return Promise.resolve(false);
    lastGen = now;
    return fetch("/api/direct/gen", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ n: 1 })
    }).then(function (r) { return r.json(); })
      .then(function (d) { return !!(d && d.ok); })
      .catch(function () { return false; });
  }

  function tick() {
    if (busy || !idle()) return;
    if (Date.now() < holdUntil) return;
    busy = true;
    fetch("/api/play/next", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d || !d.ok || !d.line) {
          return maybeGenerate().then(function () { busy = false; });
        }
        return show(d.line).then(function () {
          busy = false;
          holdUntil = Date.now() + 400;
        });
      })
      .catch(function () { busy = false; });
  }

  function syncFlags() {
    fetch("/api/direct/status", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (d) { genOn = !!((d && d.director) || {}).enabled; })
      .catch(function () {});
  }

  function start() {
    if (timer) return;
    syncFlags();
    setInterval(syncFlags, 5000);
    timer = setInterval(tick, 400);
    window.__playRunning = true;
  }

  function stop() {
    if (timer) { clearInterval(timer); timer = null; }
    window.__playRunning = false;
  }

  var api = { start: start, stop: stop, tick: tick, isPlaying: function () { return busy; } };
  WA2.play = api;
  WA2.register && WA2.register("play", api);
  window.__playStart = start;
  window.__playStop = stop;

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
