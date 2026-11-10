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

  /* 动态文本也走字模（和界面其它字同一条链路）；没有接口时退回普通文字 */
  function setText(el, text) {
    if (!el) return;
    if (window.__uiTextRender) {
      try { window.__uiTextRender(el, text || "", {}); return; } catch (e) {}
    }
    el.textContent = text || "";
  }

  /* 状态行已删（用户要求）：连接结果改用浮动提示 */
  function paintStatus(text) {
    if (text) toast(text);
  }

  function fill(kind) {
    state.kind = kind;
    var k = (state.data && state.data.kinds && state.data.kinds[kind]) || {};
    var _timg = el("spmdTitleImg");
    if (_timg) {
      var titles = {
        chat: ["assets/ui/text/mgr_chat.png", "聊天模型"],
        voice: ["assets/ui/text/mgr_voice.png", "语音模型"],
        director: ["assets/ui/text/mgr_director.png", "导演模型"]
      };
      var tt = titles[kind] || titles.chat;
      _timg.src = tt[0];
      _timg.alt = tt[1];
    }
    el("spmdEndpoint").value = k.endpoint || "";
    el("spmdKey").value = "";   /* 原生 placeholder 会和字模层叠字，不用 */
    setText(el("spmdModel"), k.model || (k.globalModel ? (k.globalModel + "（全局）") : "—"));
    syncPh();
    setConnectLabel(!!k.connected);
  }

  /* 导演是场景级配置（不属于任何角色），其余类别是角色级 */
  function apiBase() {
    return state.kind === "director" ? "/api/director/model" : "/api/char/model";
  }

  function load(kind, cb) {
    api(apiBase()).then(function (d) {
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
    api(apiBase(), {
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
      api(apiBase() + "/connect", { kind: state.kind }).then(function (d) {
        state.busy = false;
        if (d && d.ok) {
          load(state.kind, function () { setConnectLabel(true); paintStatus("已连接" + (d.model ? "：" + d.model : "")); });
        } else {
          paintStatus("连接失败：" + ((d && d.error) || "未知错误"), false);
        }
      }).catch(function () { state.busy = false; paintStatus("连接失败", false); });
    });
  }

  function doDisconnect() {
    saveFields(function () {
      api(apiBase() + "/disconnect", { kind: state.kind }).then(function (d) {
        if (d && d.ok) { load(state.kind, function () { setConnectLabel(false); paintStatus("已断开"); }); }
        else toast((d && d.error) || "断开失败");
      });
    });
  }

  /* 切换连接：一键清空输入框与已存配置，重新输入端点/API 连新的模型 */
  function doSwitch() {
    api(apiBase() + "/switch", { kind: state.kind }).then(function (d) {
      if (d && d.ok) {
        el("spmdEndpoint").value = "";
        el("spmdKey").value = "";
        setText(el("spmdModel"), "—");
        setConnectLabel(false);
        syncPh();
        paintStatus("已清空，请重新输入端点与 API", true);
        el("spmdEndpoint").focus();
      } else toast((d && d.error) || "切换失败");
    });
  }

  /* 连接按钮的字样：已连接 ↔ 连接 */
  function setConnectLabel(on) {
    var btn = document.querySelector('.spmdBtn[data-act="connect"]');
    if (!btn) return;
    var img = btn.querySelector("img");
    if (!img) return;
    img.src = on ? "assets/ui/text/mgr_ok.png" : "assets/ui/text/mgr_connect.png";
    img.alt = on ? "已连接" : "连接";
  }

  /* 占位图：输入框为空时显示，有内容时隐藏（占位本身是烘焙好的图） */
  function syncPh() {
    var pairs = [["spmdEndpoint", "spmdPhUrl"], ["spmdKey", "spmdPhKey"]];
    for (var i = 0; i < pairs.length; i++) {
      var inp = el(pairs[i][0]), ph = el(pairs[i][1]);
      if (!inp || !ph) continue;
      ph.style.display = inp.value ? "none" : "";
    }
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
    ["spmdEndpoint", "spmdKey"].forEach(function (id) {
      var inp = el(id);
      if (inp) inp.addEventListener("input", syncPh);
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
