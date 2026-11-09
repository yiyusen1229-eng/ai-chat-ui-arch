/* wt-play.js —— 播放队列的消费端（最小闭环）
 *
 * 后端 playqueue 里排好的「拍」由这里取出来显示。
 * 现在只做最朴素的一条：取一条 → 当成一句台词显示 → 再取下一条。
 * 多角色排版、按角色说话、语音，都等导演接上后再细化。
 *
 * 触发方式：轮询 /api/play/next（1.2 秒一次，只在空闲时取）。
 */
(function () {
  var WA2 = window.WA2 = window.WA2 || {};
  var timer = null;
  var busy = false;
  var playing = false;

  function idle() {
    try {
      if (WA2.chat && WA2.chat.isBusy && WA2.chat.isBusy()) return false;
    } catch (e) {}
    var g = document.getElementById("gameScreen");
    if (g && g.className.indexOf("active") < 0) return false;
    return true;
  }

  function show(line) {
    if (!line || !line.text) return Promise.resolve();
    var speaker = line.speaker || "";
    var text = line.text;
    /* 多人台词先用「名字：「…」」的形式，等排版做好再换成正式版 */
    var body = speaker ? (speaker + "：「" + text + "」") : text;
    try {
      if (WA2.chat && WA2.chat.renderQueued) {
        /* 只给 reply，别带空字段：applyServerReply 会拿 scene/emo 去改画面 */
        return WA2.chat.renderQueued({ reply: body });
      }
    } catch (e) {}
    var el = document.getElementById("msgText");
    if (el) el.textContent = body;
    return Promise.resolve();
  }

  function tick() {
    if (busy || !idle()) return;
    busy = true;
    fetch("/api/play/next", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d || !d.ok || !d.line) { busy = false; return; }
        playing = true;
        return show(d.line).then(function () { playing = false; busy = false; });
      })
      .catch(function () { busy = false; });
  }

  function start() {
    if (timer) return;
    timer = setInterval(tick, 1200);
    window.__playRunning = true;
  }

  function stop() {
    if (timer) { clearInterval(timer); timer = null; }
    window.__playRunning = false;
  }

  var api = { start: start, stop: stop, tick: tick, isPlaying: function () { return playing; } };
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
