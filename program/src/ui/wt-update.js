/* wt-update.js —— 启动时检查新版本，标题栏弹一条通知
 *
 * 两种模式（后端 /api/update 的 `auto` 决定）：
 *   auto=true  源码安装（git clone / 解压的源码包）→ 点一下**自己更新**
 *   auto=false 打包版 exe（代码冻在 exe 里）      → 点一下**打开下载页**，没法原地更新
 *
 * 提醒策略在后端（api_update.py）：`webview.start(private_mode=True)` 每次启动都是
 * 全新的 WebView2 数据目录，localStorage 存不住 —— 「看过没有」记在数据根的
 * update_seen.json。前端只在 `should_show` 为真时插 chip；菜单「检查更新」用
 * `?force=1` 绕过策略。
 *
 * 流程：
 *   1. GET /api/update  → 有新版且该提示 → 往 #menuBar 插一条 chip
 *   2. 点 chip → auto：POST /api/update/apply；否则打开 url 并发 POST /api/update/ack
 *   3. 轮询 GET /api/update/status，chip 上显示「正在更新 42% · 覆盖代码与文档」
 *   4. 成功：「已更新到 x.y · 关掉重开生效」；失败：退回「点此打开下载页」
 *
 * 探针：window.__checkUpdate() 检查；window.__autoUpdate() 直接更新；window.WA2.update.state 看状态
 */
(function () {
  var WA2 = window.WA2 = window.WA2 || {};
  var state = {
    checked: false, newer: false, latest: "", current: "", url: "", error: "",
    auto: false, mode: "", portable: false, hint: "", shouldShow: false,
    updating: false, percent: 0, message: "", done: false, failed: false
  };
  var timer = null;

  function chip() {
    return document.getElementById("updateNoti");
  }

  function ensureChip() {
    var bar = document.getElementById("menuBar");
    if (!bar) return null;
    var el = chip();
    if (el) return el;
    el = document.createElement("div");
    el.id = "updateNoti";
    el.className = "upd";
    el.style.display = "none";
    el.addEventListener("click", function (e) {
      e.stopPropagation();
      if (state.updating) return;
      /* 源码安装且还没更新过：直接一键更新 */
      if (state.auto && !state.done && !state.failed) { startUpdate(); return; }
      /* 打包版 / 更新失败 / 已经更新过：打开下载页 */
      if (!state.url) return;
      openPage();
    });
    var firstBtn = bar.querySelector(".mbWinBtn");
    if (firstBtn) bar.insertBefore(el, firstBtn); else bar.appendChild(el);
    return el;
  }

  function setText(t) {
    var el = ensureChip();
    if (!el) return;
    el.textContent = t;
    el.style.display = "";
  }

  function label(info) {
    if (info.auto) return "发现新版本 " + info.latest + " · 点此一键更新";
    return "发现新版本 " + info.latest + "（当前 " + (info.current || "") + "）· 点此打开下载页";
  }

  function show(info) {
    state.newer = true;
    state.latest = info.latest || "";
    state.current = info.current || "";
    state.url = info.url || "";
    state.auto = !!info.auto;
    state.mode = info.mode || "";
    state.portable = !!info.portable;
    state.hint = info.hint || "";
    state.shouldShow = true;
    var el = ensureChip();
    if (el) {
      el.classList.remove("done");
      el.title = state.auto ? "发现新版本，点击直接更新"
                            : "打包版没法原地更新：下载新压缩包，解压后替换整个文件夹";
    }
    setText(label(info));
  }

  /* 打开下载页：用 fetch 而不是 location.href —— 后者会把整个界面导航到一个 JSON 页面上 */
  function openPage() {
    var el = ensureChip();
    if (el) { el.classList.add("done"); el.title = "已在浏览器打开下载页"; }
    setText("已打开下载页 · 解压后替换整个文件夹（存档在 characters/ 里不受影响）");
    ack();
    fetch("/api/open_url?url=" + encodeURIComponent(state.url), { cache: "no-store" })
      .catch(function () {});
  }

  function ack() {
    fetch("/api/update/ack", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      cache: "no-store",
      body: JSON.stringify({ version: state.latest })
    }).catch(function () {});
  }

  function flash(text) {
    setText(text);
    var el = chip();
    if (el) el.classList.add("done");
    setTimeout(function () {
      var e2 = chip();
      if (e2 && !state.updating && !state.done) e2.style.display = "none";
    }, 4000);
  }

  function startUpdate() {
    state.updating = true;
    state.failed = false;
    setText("正在更新… 0%");
    fetch("/api/update/apply", { method: "POST", cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d || d.ok === false) {
          fail((d && d.error) || "启动更新失败");
          return;
        }
        poll();
      })
      .catch(function (e) { fail(String(e)); });
  }

  function poll() {
    if (timer) clearTimeout(timer);
    fetch("/api/update/status", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var p = (d && d.progress) || {};
        state.percent = p.percent || 0;
        state.message = p.message || "";
        if (p.ok === true) {
          state.updating = false;
          state.done = true;
          setText("已更新到 " + (p.version || state.latest) + " · 关掉窗口重开生效");
          var el = chip();
          if (el) { el.classList.add("done"); el.title = "更新完成，请重新启动程序"; }
          return;
        }
        if (p.ok === false) {
          fail(p.error || "更新失败");
          return;
        }
        setText("正在更新… " + state.percent + "%" +
                (state.message ? " · " + state.message : ""));
        timer = setTimeout(poll, 700);
      })
      .catch(function (e) { fail(String(e)); });
  }

  function fail(msg) {
    state.updating = false;
    state.failed = true;
    state.error = msg;
    /* 打包版没有「一键更新」这条路，失败文案直接说下载页 */
    setText(state.portable || !state.auto ? "发现新版本 " + (state.latest || "") + " · 点此打开下载页"
                                          : "更新失败 —— 点此打开下载页");
    var el = chip();
    if (el) el.title = String(msg).slice(0, 200);
  }

  function check(force) {
    var url = "/api/update" + (force ? "?force=1" : "");
    return fetch(url, { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        state.checked = true;
        if (!d) return state;
        if (d.error) state.error = d.error;
        state.current = d.current || "";
        state.auto = !!d.auto;
        state.mode = d.mode || "";
        state.portable = !!d.portable;
        state.hint = d.hint || "";
        if (!d.newer) {
          if (force) flash("已是最新版本 " + (d.current || ""));
          return state;
        }
        /* 提示过就不再烦（后端记住的）；手动检查 force 时照弹 */
        if (!force && d.should_show === false) return state;
        show(d);
        return state;
      })
      .catch(function (e) { state.checked = true; state.error = String(e); return state; });
  }

  WA2.update = {
    state: state,
    check: check,
    forceCheck: function () { return check(true); },
    show: show,
    chip: ensureChip,
    apply: startUpdate
  };
  WA2.register("update", WA2.update);
  window.__checkUpdate = check;
  window.__autoUpdate = startUpdate;

  /* 启动 1.2 秒后再查（不跟首屏抢资源；查不到就算了） */
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      setTimeout(function () { check(false); }, 1200);
    });
  } else {
    setTimeout(function () { check(false); }, 1200);
  }
})();
