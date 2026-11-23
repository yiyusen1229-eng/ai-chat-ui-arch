/* 调试面板：两块内容
 *
 * 上半「状态」：每 1.5 秒问一次后端 `/api/debug/state`（#13），把「这一轮提示词花在哪、
 *   命中了什么、缓存有没有用上、分类器 / 关系阶段什么状态」摊开。面板隐藏时不发请求。
 * 下半「时间线」：音效触发 / 界面打开 / 滚轮 / JS 报错，最近 18 条。
 *
 * 用法：默认**关闭**（点标题栏「调试」或按 Ctrl+Shift+D 打开）。
 * 只观察、不改变行为；状态那一半是纯只读（后端 diag.snapshot() 不触发任何计算）。
 * 后端只回数字和标签，不回对话正文——面板不是聊天记录查看器。
 */
(function () {
  var MAX = 18;
  var buf = [];
  var POLL_MS = 1500;

  var wrap = document.createElement("div");
  wrap.id = "dbgPanel";
  wrap.style.cssText = [
    "position:fixed", "left:6px", "top:6px", "z-index:99999",
    "max-width:560px", "padding:4px 6px", "border-radius:3px",
    "background:rgba(0,0,0,.74)", "color:#8f8", "font:10px/1.45 monospace",
    "white-space:pre", "pointer-events:none", "display:none",
    "text-shadow:0 0 2px #000"
  ].join(";");

  var stateBox = document.createElement("div");
  stateBox.id = "dbgState";
  stateBox.style.cssText = "padding-bottom:4px;margin-bottom:4px;border-bottom:1px dashed rgba(140,255,140,.35);color:#cfe";

  var feedBox = document.createElement("div");
  feedBox.id = "dbgFeed";

  wrap.appendChild(stateBox);
  wrap.appendChild(feedBox);
  document.body.appendChild(wrap);

  function stamp() {
    var d = new Date();
    return ("0" + d.getMinutes()).slice(-2) + ":" + ("0" + d.getSeconds()).slice(-2) +
           "." + ("00" + d.getMilliseconds()).slice(-3);
  }

  function log(tag, msg) {
    buf.push(stamp() + " " + tag + " " + msg);
    if (buf.length > MAX) buf.shift();
    feedBox.textContent = buf.join("\n");
  }
  window.__dbg = log;
  window.__dbgBuf = buf;

  /* ---------- 状态：后端 /api/debug/state ---------- */

  function num(v) { return (v === undefined || v === null) ? 0 : v; }
  function dur(s) {
    s = num(s);
    if (s < 60) return s + "s";
    if (s < 3600) return Math.floor(s / 60) + "m";
    return Math.floor(s / 3600) + "h" + Math.floor((s % 3600) / 60) + "m";
  }

  function renderState(d) {
    var L = [];
    if (!d || !d.ok) { stateBox.textContent = "状态不可用（后端没起？）"; return; }
    var srv = d.server || {}, c = d.counters || {}, m = d.modules || {};
    var rs = d.rounds || [];
    var r = rs.length ? rs[rs.length - 1] : null;

    L.push("── 状态 " + (srv.version || "?") + " · 运行 " + dur(srv.uptime_s) + " ──");

    if (r) {
      var seg = r.seg || {}, tok = r.tokens || {}, use = r.usage || {};
      L.push("模型 " + (r.model || "?") + " " + (r.tier === "cloud" ? "云" : "本地") +
             " · 时期 " + (r.period || "?") + " · 阶段 " + num(r.stage) + "/4");
      L.push("提示词 人设" + num(seg.persona) + " 常驻" + num(seg.memory_extra) +
             " 世界书" + num(seg.worldbook) + (seg.wb_names && seg.wb_names.length ? "(" + seg.wb_names.length + "条)" : "") +
             " 记忆" + num(seg.mem_note) + " 原作" + num(seg.story) + " 尾" + num(seg.tail));
      L.push("token  system " + num(tok.system) + " · 消息 " + num(tok.msgs) + " · fewshot " + num(tok.fewshot));
      L.push("本轮   " + (r.ms ? (r.ms / 1000).toFixed(1) + "s" : "-") +
             " · 消息 " + num(seg.msgs_sent) + "/" + num(seg.msgs_total) + " 条" +
             (use.tokens ? " · " + use.tokens + "tok" : "") +
             (use.cost ? " · ¥" + use.cost : ""));
      L.push("场景   " + (r.scene && r.scene.id ? r.scene.id + " " + num(r.scene.score) : "未换") +
             " · 情绪 " + (r.emotion || "-") + (r.outfit ? " · 衣服 " + r.outfit : ""));
    } else {
      L.push("本轮   还没有对话（发一句就会填上来）");
    }

    var mem = m.memory || {};
    L.push("记忆   条目 " + num(mem.entries) + "/" + num(mem.max_entries) +
           "（卷宗 " + num(mem.max_summary_entries) + "）· 覆盖到第 " + num(mem.covered) + " 条 · 打包 " +
           num(mem.packs) + " 次" +
           (mem.hole > 0 ? " · ⚠ 空洞 " + num(mem.hole) + " 条（后台补齐中）" : ""));

    var stg = m.stage || {};
    L.push("分类   阶段 " + num(stg.stage) + "/4 · 每 " + num(stg.every) + " 轮判一次" +
           " · 还差 " + num(stg.due_in) + " 轮 · " + (stg.busy ? "后台判断中" : "空闲"));

    var sr = m.story || {};
    L.push("检索   " + num((sr.hits || []).length) + " 命中" +
           (sr.chars ? " " + sr.chars + " 字" : "") +
           " · 索引 " + num(sr.docs) + " 块 · " + (sr.enabled ? "开" : "关"));

    var tt = m.tts || {}, last = tt.last || {};
    L.push("语音   " + (tt.ready ? "就绪" : (tt.available ? "预热中" : "不可用")) +
           (last.lufs !== undefined && last.lufs !== null
             ? " · " + last.lufs + "→" + num(last.target) + " LUFS (" + (last.gain_db > 0 ? "+" : "") + num(last.gain_db) + "dB)" : ""));

    L.push("缓存   字模 碟" + num(c["text.disk"]) + "/存" + num(c["text.mem"]) + "/渲" + num(c["text.render"]) +
           " · TTS " + num(c["tts.hit"]) + "/" + num(c["tts.render"]) +
           " · 坏 " + num((m.cache || {}).dropped));

    /* 前端自己的状态：不往后端要，直接读内存 */
    var perf = (window.WA2 && WA2.perf) ? WA2.perf.current() : "?";
    var fd = (window.WA2 && WA2.fade) ? WA2.fade.describe() : null;
    var dev = (window.WA2 && WA2.audio && WA2.audio.device) ? WA2.audio.device.changes : 0;
    L.push("前端   演出 " + perf + " · 过渡 " + (fd ? (fd.enabled ? fd.ms + "ms" : "关") : "?") +
           " · 音频设备变化 " + num(dev) + " 次");

    stateBox.textContent = L.join("\n");
  }

  function pullState() {
    if (wrap.style.display === "none") return;      /* 关着就别问，省得白跑 */
    fetch("/api/debug/state", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(renderState)
      .catch(function (e) {
        stateBox.textContent = "状态读取失败：" + String(e && e.message || e).slice(0, 48);
      });
  }
  window.__dbgState = pullState;

  setInterval(pullState, POLL_MS);

  document.addEventListener("keydown", function (e) {
    if (e.ctrlKey && e.shiftKey && (e.key === "D" || e.key === "d")) {
      var showing = wrap.style.display === "none";
      wrap.style.display = showing ? "block" : "none";
      if (showing) pullState();
    }
  });

  /* ---------- 时间线 ---------- */

  /* ---- 音效 ---- */
  function wrapSE() {
    if (!window.playSE || window.playSE.__wrapped) return false;
    var orig = window.playSE;
    window.playSE = function (kind) {
      log("SE  ", kind);
      try { return orig.apply(this, arguments); } catch (e) { log("SE! ", e.message); }
    };
    window.playSE.__wrapped = true;
    return true;
  }

  /* ---- 界面打开：采样 opacity，看有没有渐变 ---- */
  function wrapOpen() {
    if (!window.__openScreen || window.__openScreen.__wrapped) return false;
    var orig = window.__openScreen;
    window.__openScreen = function (name, mode) {
      log("OPEN", name + (mode ? "/" + mode : ""));
      var r = orig.apply(this, arguments);
      try {
        var el = document.getElementById(name === "save" ? "saveScreen" : name + "Screen");
        if (el) {
          var vals = [], t0 = performance.now();
          var iv = setInterval(function () {
            vals.push(parseFloat(getComputedStyle(el).opacity).toFixed(2));
            if (performance.now() - t0 > 300) {
              clearInterval(iv);
              var u = {}; vals.forEach(function (v) { u[v] = 1; });
              log("OPAC", Object.keys(u).join(">") + " (" + Object.keys(u).length + "值)");
            }
          }, 20);
        }
      } catch (e) {}
      return r;
    };
    window.__openScreen.__wrapped = true;
    return true;
  }

  /* ---- 滚轮：document 捕获阶段，元素被重建也不受影响 ---- */
  var BACKLOG = "#historyBox,#blogList,.historyBox,.backlog,.hlineBox";
  var acc = 0;
  var lastWheelSE = 0;

  document.addEventListener("wheel", function (e) {
    var t = e.target;
    var inBack = t && t.closest && t.closest(BACKLOG);
    log("WHEEL", (inBack ? "回想 " : "其它 ") + Math.round(e.deltaY));
    if (!inBack) return;
    acc += Math.abs(e.deltaY);
    if (acc >= 60) {
      acc = 0;
      /* 高速滚动时限流：260ms 内只响一次，低速正常触发 */
      var now = performance.now();
      if (now - lastWheelSE < 420) return;
      lastWheelSE = now;
      log("PAGE", "滚轮触发");
      try { if (window.playSE) window.playSE("page"); } catch (err) {}
    }
  }, true);

  /* ---- 兜底：脚本可能比 game.js 早执行，轮询几次把它包上 ---- */
  var tries = 0;
  var timer = setInterval(function () {
    var a = wrapSE(), b = wrapOpen();
    if ((a && b) || ++tries > 40) clearInterval(timer);
  }, 250);

  log("BOOT", "面板已启动（Ctrl+Shift+D 开关）");
})();

/* 把 JS 运行时报错也记进面板（有异常时动画会整段不执行，这条能看到原因）。
   注意：log 在 IIFE 里，外面拿不到——必须走 window.__dbg（以前这里直接调 log，
   报错被 try/catch 吞掉，等于从来没记上）。 */
window.addEventListener("error", function (e) {
  try {
    var msg = (e && e.message) ? e.message : "unknown";
    var src = (e && e.filename ? e.filename.split("/").pop() : "") + ":" + (e && e.lineno || 0);
    if (window.__dbg) window.__dbg("ERR ", src + " " + msg.slice(0, 60));
  } catch (err) {}
});
window.addEventListener("unhandledrejection", function (e) {
  try { if (window.__dbg) window.__dbg("REJ ", String(e.reason).slice(0, 60)); } catch (err) {}
});
