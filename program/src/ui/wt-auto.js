/* wt-auto.js —— 自动续说（工具栏 ▶）
 *
 * 需求（2026-11-07 用户）：
 *   · 开着时不等人回话，她接着自己往下说；
 *   · **最多只生成「当前正在播的那句」再往后的三条**，不许无限生成；
 *   · 生成到三条就停：停在当前这句，之后像 galgame 一样**左键点一下出一句**，
 *     出完这三条，输入框重新出现；
 *   · 语音没播完不推进下一句（以前会抢话）。
 *
 * 做法：队列 + 上限。队列里放「已生成但还没显示」的回复。
 *   显示一句 → 等这句语音播完（renderQueued 的 promise）→ 还开着就继续补队列。
 *   补满 CAP 条就关掉自动模式、停在当前这句；之后左键一句句放完。
 */
(function () {
  var WA2 = window.WA2 = window.WA2 || {};
  var $ = (WA2.util && WA2.util.$) || function (id) { return document.getElementById(id); };

  var CAP = 3;                 // 最多预生成三条
  var GAP_MS = 900;            // 句与句之间的停顿

  var state = {
    on: false,
    queue: [],                 // 已生成、待显示的回复
    generating: false,
    showing: false,            // 当前这句还在播
    timer: null,
    retry: null,
    errors: 0
  };

  function btn() {
    return document.querySelector('#toolbar .tb[data-act="play"]');
  }

  function toast(msg, ms) {
    try { if (window.__toast) window.__toast(msg, ms || 2400); } catch (e) {}
  }

  function paint() {
    var b = btn();
    if (b) b.classList.toggle("is-on", !!state.on);
    /* 左下角那块：小白条常在，绿色 ▶ 只在自动模式出现 */
    try { document.body.classList.toggle("autoOn", !!state.on); } catch (e) {}
  }

  function savePref() {
    try { window.App && window.App.setPref && window.App.setPref("autoContinue", state.on); } catch (e) {}
  }

  function clearTimer() {
    if (state.timer) { clearTimeout(state.timer); state.timer = null; }
    if (state.retry) { clearTimeout(state.retry); state.retry = null; }
  }

  /* 这一轮没轮到（正在打字 / 没拿到台词）：过一会儿再看一次 */
  function retry(ms) {
    if (!state.on) return;
    if (state.retry) clearTimeout(state.retry);
    state.retry = setTimeout(function () {
      state.retry = null;
      if (state.on) pump();
    }, ms || 1500);
  }

  function chat() { return WA2.chat || {}; }

  function busy() {
    try { return !!(chat().isBusy && chat().isBusy()); } catch (e) { return false; }
  }

  function blocked() {
    if (busy()) return "正在打字";
    if (document.body.classList.contains("uiHidden")) return "界面已隐藏";
    try {
      var c = WA2.choices && WA2.choices.pendingOptions ? WA2.choices.pendingOptions() : null;
      if (c && c.length) return "有选项";
    } catch (e) {}
    try { if (window.__charSwitching) return "切角色中"; } catch (e) {}
    return "";
  }

  function stop(reason, keepQueue) {
    clearTimer();
    var was = state.on;
    state.on = false;
    paint();
    savePref();
    if (!keepQueue) state.queue.length = 0;
    if (was && reason) toast("自动续说已关闭（" + reason + "）");
  }

  /* 显示队列里的下一条；队列空了就把输入框交还玩家 */
  function showNext() {
    if (state.showing) return false;
    var d = state.queue.shift();
    if (!d) return false;
    state.showing = true;
    var p = null;
    try { p = chat().renderQueued ? chat().renderQueued(d) : null; } catch (e) { p = null; }
    /* 关键：这句正在播的时候就把后面几条预生成出来（否则队列永远是空的，
       因为生成只在“这句播完”之后才发生） */
    if (state.on) pump();
    Promise.resolve(p).then(function () {
      state.showing = false;
      if (state.on) {
        if (state.queue.length) schedule(GAP_MS);
        else pump();
      } else if (!state.queue.length) {
        backToPlayer();
      }
    }).catch(function () { state.showing = false; });
    return true;
  }

  function backToPlayer() {
    try { document.body.classList.remove("autoOn"); } catch (e) {}
    try { var i = $("input"); if (i) i.focus(); } catch (e) {}
  }

  function schedule(ms) {
    clearTimer();
    if (!state.on) return;
    state.timer = setTimeout(function () {
      state.timer = null;
      if (!state.on) return;
      var why = blocked();
      if (why) {
        if (why === "正在打字") { retry(1200); return; }
        stop(why); return;
      }
      if (state.queue.length) showNext();
      else pump();
    }, ms === undefined ? GAP_MS : ms);
  }

  /* 补队列，直到 CAP 条 */
  function pump() {
    if (!state.on) return false;
    /* 多角色/导演真的在跑：演出由导演那条链路负责，这里不要再自己调模型。
       __directorOn 只在「导演开着 + 阵容里有演员」时为真，所以阵容空时这里照常生成。 */
    try {
      if (window.WA2 && WA2.play && WA2.play.isDirectorOn && WA2.play.isDirectorOn()) return false;
    } catch (e) {}
    /* 缓冲语义：最多领先「正在播的那句」CAP 条；满了就等播完再补，**不是**总共只生成三条 */
    if (state.queue.length >= CAP) return false;
    if (state.generating) return false;
    var why = blocked();
    if (why) {
      /* 「正在打字」只是这一轮还没轮到：不该关自动模式，但必须排一次重试，
         否则这一句之后就再也没人叫 pump 了（表现就是「只生成一条」）。 */
      if (why === "正在打字") { retry(1200); return false; }
      stop(why); return false;
    }
    if (!chat().generateContinue) { retry(2000); return false; }

    state.generating = true;
    chat().generateContinue().then(function (d) {
      state.generating = false;
      if (!d) {
        state.errors++;
        if (state.errors >= 3) stop("连续出错");
        else retry(2000);
        return;
      }
      state.errors = 0;
      /* 动作/神态只留在左上角动作区（renderThinking 已经会显示），
         不再另外推一条 narr 进对话框——否则同一句话会出现两次。 */
      state.queue.push(d);
      if (!state.showing) showNext();
      /* 还没满就继续补（播完一条会自动再补一条） */
      if (state.on && state.queue.length < CAP) pump();
    }).catch(function () {
      state.generating = false;
      state.errors++;
      if (state.errors >= 3) stop("连续出错");
      else retry(2000);
    });
    return true;
  }

  function start() {
    if (state.on) return true;
    state.queue.length = 0;
    state.errors = 0;
    /* 单角色：先把上一场多角色留下的「拍」清掉，别让它们冒出来 */
    try {
      var _dir = !!(window.WA2 && WA2.play && WA2.play.isDirectorOn && WA2.play.isDirectorOn());
      if (!_dir) fetch("/api/play/clear", { method: "POST" }).catch(function () {});
    } catch (e) {}
    state.on = true;
    paint();
    savePref();
    /* 不再弹提示：右下角的绿 ▶ 已经说明状态 */
    pump();
    return true;
  }

  function toggle() {
    if (state.on) { stop(); return false; }
    return start();
  }

  /* wt-chat 每句语音播完后调 */
  function lineDone() {
    if (state.on) {
      if (state.queue.length) schedule(GAP_MS);
      else pump();
      return true;
    }
    if (!state.queue.length) backToPlayer();
    return false;
  }

  /* 左键点画面：还有预生成的句子就放下一句（galgame 手感） */
  function onStageClick(e) {
    if (state.on || state.showing || !state.queue.length || busy()) return;
    var t = e.target;
    if (t && t.closest && t.closest('#toolbar, #inputBar, #menuBar, .choiceWrap')) return;
    e.preventDefault();
    e.stopPropagation();
    showNext();
  }

  function init() {
    var b = btn();
    if (b) {
      b.addEventListener("click", function () { toggle(); });
      delete b.dataset.tip;
    }
    try {
      var p = window.App && window.App.getPrefs ? window.App.getPrefs() : {};
      state.on = !!p.autoContinue;
    } catch (e) {}
    paint();
    if (state.on) pump();
    document.addEventListener("click", onStageClick, true);
  }

  var api = {
    toggle: toggle, start: start, stop: stop, pump: pump, lineDone: lineDone,
    showNext: showNext, isOn: function () { return state.on; },
    queueLen: function () { return state.queue.length; }
  };
  WA2.auto = api;
  WA2.register && WA2.register("auto", api);

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
