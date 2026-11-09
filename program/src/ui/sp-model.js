/* sp-model.js —— 「配置角色模型」对话框
 *
 * 一行「配置角色模型」→ 下拉两个按钮（聊天模型 / 语音模型）→ 点开这个对话框：
 *   端点 / API 密钥 两个输入框（底板 sys_00110），模型名称由连接时自动探测；
 *   右下三个按钮：切换连接 / 断开连接 / 连接（底板沿用发送按钮的 btn_3state）。
 *
 * 数据落在 characters/<id>/model.json，接口见 /api/char/model*
 */
(function () {
  var WA2 = window.WA2 = window.WA2 || {};
  var $ = (WA2.util && WA2.util.$) || function (id) { return document.getElementById(id); };

  var state = { kind: "chat", data: null, busy: false };

  function toast(msg, ms) {
    try { if (window.__toast) window.__toast(msg, ms || 2000); } catch (e) {}
  }

  function api(path, body) {
    var opt = body ? {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    } : { cache: "no-store" };
    return fetch(path, opt).then(function (r) { return r.json(); });
  }

  function el(id) { return $(id); }

  function paintStatus(text, ok) {
    var s = el("spmdStatus");
    if (!s) return;
    s.textContent = text || "";
    s.style.color = ok ? "#9ff2c6" : "#ffb9b9";
  }

  function fill(kind) {
    state.kind = kind;
    var k = (state.data && state.data.kinds && state.data.kinds[kind]) || {};
    var _title = el("spmdTitle");
    if (_title) _title.textContent = (kind === "chat" ? "聊天模型" : "语音模型");
    el("spmdEndpoint").value = k.endpoint || "";
    el("spmdKey").value = "";
    el("spmdKey").placeholder = k.hasKey ? ("已保存：" + (k.keyMask || "")) : "sk-...";
    el("spmdModel").textContent = k.model || (k.globalModel ? (k.globalModel + "（全局）") : "—");
    if (k.connected) paintStatus("已连接", true);
    else if (k.usingGlobal) paintStatus("当前用全局默认配置", true);
    else paintStatus("未连接", false);
  }

  function load(kind, cb) {
    api("/api/char/model").then(function (d) {
      state.data = d || {};
      fill(kind || state.kind);
      if (cb) cb();
    }).catch(function () { toast("读取配置失败"); });
  }

  function open(kind) {
    state.kind = kind || "chat";
    load(state.kind, function () {
      var dlg = el("spModelDlg");
      if (dlg) { dlg.classList.add("show"); dlg.setAttribute("aria-hidden", "false"); }
    });
  }

  function close() {
    var dlg = el("spModelDlg");
    if (dlg) { dlg.classList.remove("show"); dlg.setAttribute("aria-hidden", "true"); }
  }

  function saveFields(cb) {
    api("/api/char/model", {
      kind: state.kind,
      endpoint: el("spmdEndpoint").value,
      key: el("spmdKey").value
    }).then(function (d) {
      if (!d || !d.ok) { toast((d && d.error) || "保存失败"); return; }
      if (cb) cb();
    }).catch(function () { toast("保存失败"); });
  }

  function doConnect() {
    if (state.busy) return;
    state.busy = true;
    paintStatus("连接中…", true);
    saveFields(function () {
      api("/api/char/model/connect", { kind: state.kind }).then(function (d) {
        state.busy = false;
        if (d && d.ok) {
          load(state.kind, function () { paintStatus("已连接" + (d.model ? "：" + d.model : ""), true); });
        } else {
          paintStatus("连接失败：" + ((d && d.error) || "未知错误"), false);
        }
      }).catch(function () { state.busy = false; paintStatus("连接失败", false); });
    });
  }

  function doDisconnect() {
    saveFields(function () {
      api("/api/char/model/disconnect", { kind: state.kind }).then(function (d) {
        if (d && d.ok) { load(state.kind, function () { paintStatus("已断开", false); }); }
        else toast((d && d.error) || "断开失败");
      });
    });
  }

  /* 切换连接：一键清空输入框与已存配置，重新输入端点/API 连新的模型 */
  function doSwitch() {
    api("/api/char/model/switch", { kind: state.kind }).then(function (d) {
      if (d && d.ok) {
        el("spmdEndpoint").value = "";
        el("spmdKey").value = "";
        el("spmdModel").textContent = "—";
        paintStatus("已清空，请重新输入端点与 API", true);
        el("spmdEndpoint").focus();
      } else toast((d && d.error) || "切换失败");
    });
  }

  function bind() {
    var dlg = el("spModelDlg");
    if (!dlg || dlg.dataset.bound) return;
    dlg.dataset.bound = "1";
    dlg.querySelectorAll(".spmdBtn").forEach(function (b) {
      b.addEventListener("click", function () {
        var a = b.dataset.act;
        if (a === "connect") doConnect();
        if (a === "disconnect") doDisconnect();
        if (a === "switch") doSwitch();
        if (a === "exit") close();
      });
    });
    dlg.addEventListener("click", function (e) { if (e.target === dlg) close(); });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && dlg.classList.contains("show")) close();
    });
  }

  /* ---------- 把「配置角色模型」那一行的面板按钮改成聊天/语音 ----------
     放在这个模块里做，是为了不动 sp-panels.js（静态检查有基线，
     在那个文件里加分支会多出几条误报）。 */
  function decoratePanel() {
    var item = document.querySelector('#specialScreen .spItem[data-item="model"]');
    if (!item) return;
    var btns = item.querySelectorAll(".spPanelBtn");
    if (!btns || btns.length < 2) return;
    var nowEl = item.querySelector(".spPanelNow");
    if (nowEl) nowEl.parentNode.removeChild(nowEl);
    var want = [
      { act: "chat",  img: "assets/ui/text/sp_btn_chat.png",   title: "聊天模型" },
      { act: "voice", img: "assets/ui/text/sp_btn_voicem.png", title: "语音模型" }
    ];
    for (var i = 0; i < 2; i++) {
      var b = btns[i];
      b.dataset.act = want[i].act;
      b.title = want[i].title;
      var im = b.querySelector("img");
      if (im) { im.src = want[i].img; im.alt = want[i].title; }
      if (!b.dataset.mod) {
        b.dataset.mod = "1";
        b.addEventListener("click", function (e) {
          e.stopPropagation();
          open(this.dataset.act);
        });
      }
    }
  }

  function hookRow() {
    var item = document.querySelector('#specialScreen .spItem[data-item="model"]');
    if (!item) return;
    var head = item.querySelector(".spHead");
    if (head && !head.dataset.modHook) {
      head.dataset.modHook = "1";
      head.addEventListener("click", function () {
        /* 面板是 sp-panels 在点击时创建的，等它建好再改按钮 */
        setTimeout(decoratePanel, 30);
      });
    }
    decoratePanel();
  }

  var apiOut = { open: open, close: close, reload: load };
  WA2.spModel = apiOut;
  WA2.register && WA2.register("spModel", apiOut);

  function boot() { bind(); hookRow(); }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
