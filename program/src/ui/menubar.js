/* ============================================================
   顶部菜单栏（做成窗口标题栏，和游戏一致）
   ------------------------------------------------------------
   · 无边框窗口 -> 菜单栏就是最顶上那条
   · 菜单项: 画面设置 / 环境设置 / 系统 / 帮助
   · 画面设置 -> 全屏模式 / 窗口模式 ▸ 1280x720(推荐) 1024x576 800x450
   · 字号是独立通道：无论选什么分辨率，文字大小都不变
   ============================================================ */
(function () {
  var RES = [
    { w: 1280, h: 720, label: "1280x720（推荐）" },
    { w: 1024, h: 576, label: "1024x576" },
    { w: 800, h: 450, label: "800x450" }
  ];

  // 字体固定比例（不随分辨率变化）
  var FIXED_TEXT_SCALE = 0.625;

  function getRes() {
    try {
      var r = JSON.parse(localStorage.getItem("app_renderRes") || "null");
      if (r && r.w && r.h) {
        for (var i = 0; i < RES.length; i++) {
          if (RES[i].w === r.w && RES[i].h === r.h) return RES[i];
        }
      }
    } catch (e) {}
    return RES[0];
  }

  function applyRes(r) {
    localStorage.setItem("app_renderRes", JSON.stringify(r));
    // 字号独立：永远保持固定比例，分辨率不影响文字大小
    window.TEXT_SCALE = FIXED_TEXT_SCALE;
    try {
      localStorage.setItem("app_textScale", String(FIXED_TEXT_SCALE));
    } catch (e) {}
    if (typeof window.__applyTextScale === "function") {
      window.__applyTextScale();
    }
    updateChecks();
  }

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  function closeAll() {
    var items = document.querySelectorAll("#menuBar .mbItem");
    for (var i = 0; i < items.length; i++) items[i].classList.remove("open");
    var subs = document.querySelectorAll("#menuBar .has-sub");
    for (var j = 0; j < subs.length; j++) subs[j].classList.remove("open");
    document.body.classList.remove("mbOpen");
  }

  function makeMenu(title, rows) {
    var item = el("div", "mbItem", title);
    var menu = el("div", "mbMenu");
    rows.forEach(function (r) {
      if (r === "-") { menu.appendChild(el("div", "mi-sep")); return; }
      var row = el("div", "mi-row", r.label);
      if (r.sub) {
        row.classList.add("has-sub");
        var sub = el("div", "mbSub");
        r.sub.forEach(function (s) {
          var sr = el("div", "mi-row", s.label);
          sr.dataset.res = s.w + "x" + s.h;
          sr.addEventListener("click", function (ev) {
            ev.stopPropagation();
            if (s.run) { try { s.run(); } catch (e) {} closeAll(); return; }
            if (s.act) {
              localStorage.setItem("app_voiceLang", s.act);
              window.voiceLang = s.act;
              closeAll();
              return;
            }
            applyRes({ w: s.w, h: s.h });
            closeAll();
          });
          sub.appendChild(sr);
        });
        row.appendChild(sub);
        row.addEventListener("mouseenter", function () { row.classList.add("open"); });
        row.addEventListener("mouseleave", function () { row.classList.remove("open"); });
      } else {
        row.addEventListener("click", function (ev) {
          ev.stopPropagation();
          if (r.action) r.action();
          closeAll();
        });
      }
      menu.appendChild(row);
    });
    item.appendChild(menu);
    item.addEventListener("click", function (ev) {
      ev.stopPropagation();
      var wasOpen = item.classList.contains("open");
      closeAll();
      if (!wasOpen) {
        item.classList.add("open");
        document.body.classList.add("mbOpen");
      }
    });
    return item;
  }

  function updateChecks() {
    var cur = getRes();
    var rows = document.querySelectorAll("#menuBar .mi-row[data-res]");
    for (var i = 0; i < rows.length; i++) {
      var r = rows[i];
      if (r.dataset.res === cur.w + "x" + cur.h) r.classList.add("checked");
      else r.classList.remove("checked");
    }
  }

  function toggleFullscreen() {
    /* 不要用浏览器的 document.documentElement.requestFullscreen()：
       在无边框 WebView2 上会把窗口铺成一层“点不动”的透明层（实测会把整个桌面挡住）。
       改走 pywebview 自己的全屏开关（后端 /api/window?action=togglefull）。 */
    try {
      if (window.__dbg) window.__dbg("FULL", "togglefull");
      fetch("/api/window?action=togglefull", { cache: "no-store" })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (window.__dbg) window.__dbg("FULL", JSON.stringify(d).slice(0, 40));
          /* OPTION 1 的「窗口模式 / 全屏模式」高亮要跟着变 */
          if (d && typeof d.fullscreen === "boolean" && window.__ct1SetWinModeUI) {
            window.__ct1SetWinModeUI(d.fullscreen);
          }
        })
        .catch(function () {});
    } catch (e) {}
  }

  /* 演出过渡开关：写进 prefs（xfade），下一次换背景 / 立绘就按新设置走 */
  function fadeToggle(on) {
    try {
      if (window.WA2 && WA2.fade && WA2.fade.set) WA2.fade.set(on);
      else if (window.App && window.App.setPref) window.App.setPref("xfade", !!on);
    } catch (e) {}
    try {
      if (window.__toast) window.__toast(on ? "演出过渡：交叉淡化" : "演出过渡：已关闭", 1800);
    } catch (e) {}
    try { if (window.__dbg) window.__dbg("FADE", on ? "on/" + (window.WA2 && WA2.fade ? WA2.fade.ms() : "?") + "ms" : "off"); } catch (e) {}
  }

  /* #18 桌面行为：菜单统一入口（模块没加载就提示一次，别让菜单点了没反应） */
  function winSet(fn, arg) {
    try {
      if (window.WA2 && WA2.win && typeof WA2.win[fn] === "function") return WA2.win[fn](arg);
    } catch (e) {}
    try { if (window.__toast) window.__toast("桌面行为模块没加载（wt-window.js）", 2400); } catch (e) {}
    return null;
  }

  /* 诊断包（#15）：交给后端 /api/log/bundle 打 zip，路径直接告诉用户 */
  function exportDiag(turns) {
    var url = "/api/log/bundle" + (turns ? "?turns=" + turns : "");
    try { if (window.__toast) window.__toast("正在打包诊断包…", 1500); } catch (e) {}
    fetch(url, { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d || !d.ok) {
          alert("导出失败：" + ((d && d.error) || "未知错误"));
          return;
        }
        var kb = Math.round((d.bytes || 0) / 1024);
        alert("诊断包已生成：\n" + d.path + "\n\n" +
              kb + " KB · " + (d.note || "") + "\n" +
              "（日志里的密钥、对话查询串、绝对路径都已抹掉）");
        try { if (window.__dbg) window.__dbg("DIAG", "bundle " + kb + "KB turns=" + turns); } catch (e) {}
      })
      .catch(function (e) { alert("导出失败：" + String(e).slice(0, 80)); });
  }

  // 窗口控制（无边框窗口需要自己实现）
  function winCtrl(act) {
    try { if (window.__dbg) window.__dbg("WIN", act); } catch (e) {}
    /* 双通道：先走 pywebview 桥接（不经过 HTTP），同时发一条 HTTP 兜底。
       哪条通就用哪条，避免一条链路坏掉就完全动不了窗口。 */
    try {
      if (window.pywebview && window.pywebview.api && window.pywebview.api.win) {
        window.pywebview.api.win(act);
        try { if (window.__dbg) window.__dbg("BRIDGE", act); } catch (e) {}
      }
    } catch (e) {}
    try {
      fetch("/api/window?act=" + act, { cache: "no-store" })
        .then(function (r) { return r.json(); })
        .then(function (d) { try { if (window.__dbg) window.__dbg("HTTP", JSON.stringify(d).slice(0, 40)); } catch (e) {} })
        .catch(function (err) { try { if (window.__dbg) window.__dbg("HTTP!", String(err).slice(0, 40)); } catch (e) {} });
    } catch (e) {}
  }


  /* ================= 顶部菜单：场景 / 服装 ================= */
  var _sceneMeta = null;
  function sceneMeta() {
    if (_sceneMeta) return _sceneMeta;
    var keys = [];
    try { keys = Object.keys(CG_URL); } catch (e) {}
    if (!keys.length) return null;
    var map = {}, ids = {}, places = [], times = {};
    keys.forEach(function (id) {
      var p = id.split("（")[0];
      var m = id.match(/（(白天|黄昏|晚上|黑夜|深夜)）/);
      var t = m ? m[1] : "默认";
      if (!ids[p]) { ids[p] = []; places.push(p); }
      ids[p].push({ id: id, time: t });
      map[id] = { place: p, time: t };
    });
    places.sort(function (a, b) { return a.localeCompare(b, "zh"); });
    places.forEach(function (p) {
      var ts = [];
      ids[p].forEach(function (x) { if (ts.indexOf(x.time) < 0) ts.push(x.time); });
      times[p] = ts;
    });
    _sceneMeta = { map: map, ids: ids, places: places, times: times };
    return _sceneMeta;
  }

  function sceneState(meta) {
    var cur = (typeof currentBg === "string") ? currentBg : "";
    var p = "", t = "";
    if (cur && meta.map[cur]) { p = meta.map[cur].place; t = meta.map[cur].time; }
    if (!p || !meta.ids[p]) {
      p = meta.ids["教室"] ? "教室" : meta.places[0];
      t = (meta.times[p] || [""])[0];
    }
    if (!t || (meta.times[p] || []).indexOf(t) < 0) t = (meta.times[p] || [""])[0];
    return { place: p, time: t };
  }

  function applySceneById(id) {
    try { currentBg = null; setBg(id); sessionSave(); } catch (e) {}
    window.__nextScene = "";
  }

  function applyOutfitByName(name) {
    try {
      currentOutfit = name;
      setSpriteByScene(currentBg || "classroom", window.__lastEmo || "", true, name);
      sessionSave();
    } catch (e) {}
    window.__nextOutfit = "";
  }

  function menuSub(title, items, current, onPick) {
    var row = el("div", "mi-row has-sub", title);
    var sub = el("div", "mbSub");
    items.forEach(function (v) {
      var r = el("div", "mi-row", v);
      if (v === current) r.classList.add("checked");
      r.addEventListener("click", function (ev) {
        ev.stopPropagation();
        onPick(v);
        closeAll();
      });
      sub.appendChild(r);
    });
    row.appendChild(sub);
    row.addEventListener("mouseenter", function () { row.classList.add("open"); });
    row.addEventListener("mouseleave", function () { row.classList.remove("open"); });
    return row;
  }

  function menuToggle(title, on, menu, fn) {
    var row = el("div", "mi-row", title + "：" + (on ? "开" : "关"));
    if (on) row.classList.add("checked");
    row.addEventListener("click", function (ev) {
      ev.stopPropagation();
      fn(!on);
      refreshSceneMenu(menu);
    });
    return row;
  }

  function refreshSceneMenu(menu) {
    menu.innerHTML = "";
    var meta = sceneMeta();
    if (!meta) {
      menu.appendChild(el("div", "mi-row", "场景数据加载中…"));
      return;
    }
    var st = sceneState(meta);
    menu.appendChild(menuSub("场景地点", meta.places, st.place, function (p) {
      var ts = meta.times[p] || [];
      var t = ts.indexOf(st.time) >= 0 ? st.time : (ts[0] || "");
      var ids = meta.ids[p] || [];
      var hit = null;
      for (var i = 0; i < ids.length; i++) if (ids[i].time === t) { hit = ids[i]; break; }
      if (!hit) hit = ids[0];
      if (hit) applySceneById(hit.id);
    }));
    menu.appendChild(menuSub("场景时段", meta.times[st.place] || [], st.time, function (t) {
      var ids = meta.ids[st.place] || [];
      var hit = null;
      for (var i = 0; i < ids.length; i++) if (ids[i].time === t) { hit = ids[i]; break; }
      if (!hit) hit = ids[0];
      if (hit) applySceneById(hit.id);
    }));
    menu.appendChild(el("div", "mi-sep"));

    var outfits = [];
    try { outfits = Object.keys(SPRITE_INDEX).sort(function (a, b) { return a.localeCompare(b, "zh"); }); } catch (e) {}
    var curOut = (typeof currentOutfit === "string") ? currentOutfit : "";
    menu.appendChild(menuSub("服装", outfits, curOut, function (n) { applyOutfitByName(n); }));
    var _pm = "auto";
    try { _pm = localStorage.getItem("app_period") || "auto"; } catch (e) {}
    var _plabels = [["自动", "auto"], ["IC（高中）", "ic"], ["CC（大学/出道）", "cc"], ["CODA（成年）", "coda"]];
    var _plabel = "自动";
    for (var _pi = 0; _pi < _plabels.length; _pi++) {
      if (_plabels[_pi][1] === _pm) _plabel = _plabels[_pi][0];
    }
    menu.appendChild(menuSub("时期", _plabels.map(function (x) { return x[0]; }), _plabel, function (lab) {
      var val = "auto";
      for (var _pj = 0; _pj < _plabels.length; _pj++) if (_plabels[_pj][0] === lab) val = _plabels[_pj][1];
      try { localStorage.setItem("app_period", val); } catch (e) {}
      closeAll();
    }));
    menu.appendChild(el("div", "mi-sep"));

    menu.appendChild(menuToggle("锁定场景", !!window.__lockScene, menu, function (v) {
      window.__lockScene = v;
      try { if (window.App && window.App.setPref) window.App.setPref("lockScene", v); } catch (e) {}
      try { localStorage.setItem("lockScene", v ? "1" : "0"); } catch (e) {}
    }));
    menu.appendChild(menuToggle("锁定服装", !!window.__lockOutfit, menu, function (v) {
      window.__lockOutfit = v;
      try { if (window.App && window.App.setPref) window.App.setPref("lockOutfit", v); } catch (e) {}
      try { localStorage.setItem("lockOutfit", v ? "1" : "0"); } catch (e) {}
    }));
  }

  function makeSceneMenu() {
    var item = el("div", "mbItem", "场景 / 服装");
    var menu = el("div", "mbMenu");
    item.appendChild(menu);
    item.addEventListener("click", function (ev) {
      ev.stopPropagation();
      var wasOpen = item.classList.contains("open");
      closeAll();
      if (!wasOpen) {
        refreshSceneMenu(menu);
        item.classList.add("open");
        document.body.classList.add("mbOpen");
      }
    });
    return item;
  }


  /* 选择分支数量：写进 prefs（后端落盘 prefs.json），选项条下次打开就用新值。
     设置模块的入口是 `window.App.setPref`（它会同时写 localStorage、POST /api/prefs、
     派发 app:prefs 事件）；**没有 `WA2.prefs`**。以前先试 `WA2.prefs.setPref`，
     拿不到才走下面那段自己拼的兜底——兜底能存但读不回来（wt-choices 也读的 WA2.prefs），
     结果就是「不管改成几都是 2 个」（2026-11-06）。 */
  function setChoiceCount(n) {
    n = Math.max(2, Math.min(5, n | 0));
    try {
      if (window.App && window.App.setPref) window.App.setPref("choiceCount", n);
      else {
        var raw = localStorage.getItem("app_prefs");
        var p = raw ? JSON.parse(raw) : {};
        p.choiceCount = n;
        localStorage.setItem("app_prefs", JSON.stringify(p));
        fetch("/api/prefs", { method: "POST", headers: { "Content-Type": "application/json" },
                              body: JSON.stringify(p) }).catch(function () {});
      }
    } catch (e) {}
    try { if (window.__toast) window.__toast("选择分支：" + n + " 个选项", 1600); } catch (e) {}
  }
  function build() {
    if (document.getElementById("menuBar")) return;
    var bar = el("div", "");
    bar.id = "menuBar";

    bar.appendChild(makeMenu("画面设置", [
      { label: "全屏模式", action: toggleFullscreen },
      { label: "窗口模式", sub: RES },
      /* #18 桌面行为：置顶 / 不透明度 / 拉回本屏（点击穿透做过又删了，用不上） */
      { label: "窗口置顶", sub: [
          { label: "开（压在其他程序上面）", w: 0, h: 0, run: function () { winSet("setTopmost", true); } },
          { label: "关", w: 0, h: 0, run: function () { winSet("setTopmost", false); } }
        ] },
      { label: "窗口不透明度", sub: [
          { label: "100%（不透明）", w: 0, h: 0, run: function () { winSet("setAlpha", 255); } },
          { label: "90%", w: 0, h: 0, run: function () { winSet("setAlpha", 230); } },
          { label: "80%", w: 0, h: 0, run: function () { winSet("setAlpha", 204); } },
          { label: "70%", w: 0, h: 0, run: function () { winSet("setAlpha", 178); } },
          { label: "60%（很淡）", w: 0, h: 0, run: function () { winSet("setAlpha", 153); } }
        ] },
      { label: "拉回本屏（多显示器）", action: function () {
          if (window.WA2 && WA2.win && WA2.win.clampBack) WA2.win.clampBack();
        } }
    ]));
    bar.appendChild(makeMenu("环境设置", [
      { label: "配音语言", sub: [
          { label: "日文（贴近原版声线）", w: 0, h: 0, act: "ja" },
          { label: "中文", w: 0, h: 0, act: "zh" },
        ] },
      /* 演出过渡：双缓冲交叉淡化的开关（性能 / 无障碍：关掉就是直接切） */
      { label: "演出过渡", sub: [
          { label: "交叉淡化（240ms）", w: 0, h: 0, run: function () { fadeToggle(true); } },
          { label: "直接切换（关）", w: 0, h: 0, run: function () { fadeToggle(false); } }
        ] },
      /* #20：素材检查 / 向导（缺哪些表情、哪些场景没图，一次说清） */
      { label: "素材检查", action: function () {
          if (window.__openAssetWizard) window.__openAssetWizard();
          else alert("素材向导没加载（asset-wizard.js）");
        } },
      /* #17：数据清理入口（清缓存 / 导出数据 / 删角色，删前二次确认） */
      { label: "数据清理", action: function () {
          if (window.__openCleanup) window.__openCleanup();
          else alert("数据清理没加载（cleanup.js）");
        } }
    ]));
    bar.appendChild(makeSceneMenu());
    bar.appendChild(makeMenu("系统", [
      { label: "系统设置", action: function () {
          if (window.__openOptions) window.__openOptions();
      } },
      /* #23：首次设置检查（可重跑）+ 导出对话 */
      { label: "设置检查（缺什么配什么）", action: function () {
          if (window.__openFirstRun) window.__openFirstRun();
          else alert("首次自检没加载（wt-firstrun.js）");
        } },
      { label: "导出对话（Markdown）", action: function () {
          if (window.__openExport) window.__openExport();
          else alert("导出模块没加载（wt-export.js）");
        } },
      { label: "记忆书", action: function () {
          if (window.__openMemory) window.__openMemory();
      } },
      /* 选择分支：一次给几个选项（2~5）。OPTION 界面是烤死的图，放这里最省事也找得到 */
      { label: "选择分支数量", sub: [2, 3, 4, 5].map(function (n) {
          return { label: n + " 个", run: function () { setChoiceCount(n); } };
        }) },
      { label: "返回标题", action: function () { location.reload(); } }
    ]));

    /* 调试面板开关：不放子菜单，点一下直接开/关左上角那块绿字 */
    var dbgBtn = el("div", "mbItem", "调试");
    dbgBtn.title = "显示 / 隐藏左上角调试面板";
    dbgBtn.addEventListener("click", function (ev) {
      ev.stopPropagation();
      closeAll();
      var p = document.getElementById("dbgPanel");
      if (p) {
        var showing = (p.style.display === "none");
        p.style.display = showing ? "block" : "none";
        /* 打开就立刻拉一次状态，别等那 1.5 秒的轮询 */
        if (showing && window.__dbgState) { try { window.__dbgState(); } catch (e) {} }
      }
    });
    bar.appendChild(dbgBtn);

    var spacer = el("div", "mbSpacer");
    bar.appendChild(spacer);

    bar.appendChild(makeMenu("帮助", [
      { label: "导出运行日志（诊断卡死）", action: function () {
          fetch("/api/log/dump", { cache: "no-store" })
            .then((r) => r.json())
            .then((d) => { alert("已写入日志：\\n" + (d.log || "")); })
            .catch(() => alert("日志导出失败"));
        } },
      /* 诊断包（#15）：默认不含对话；含对话的那条必须二次确认 */
      { label: "导出诊断包（不含对话）", action: function () { exportDiag(0); } },
      { label: "导出诊断包（含最近 20 轮）", action: function () {
          if (!confirm("这个诊断包会包含最近 20 轮对话原文。\n" +
                       "发给别人之前请自己先看一遍，确认没有不想外传的内容。\n\n继续？")) return;
          exportDiag(20);
        } },
      { label: "检查更新", action: function () {
          /* ?force=1：绕过「提示过就不再提示」的策略，手动点就一定给结果 */
          if (window.WA2 && WA2.update && WA2.update.forceCheck) { WA2.update.forceCheck(); return; }
          alert("更新模块还没加载完，稍后再试");
        } },
      { label: "关于本程序" }
    ]));

    // 版本号(从页面角标取文字)
    var stampSrc = document.getElementById("__BUILDSTAMP");
    if (stampSrc) {
      var st = (stampSrc.textContent || "").trim();
      if (st) {
        var mb = el("div", "mbBuild", st);
        mb.title = st;
        bar.appendChild(mb);
      }
    }

    // 窗口按钮
    var minBtn = el("div", "mbWinBtn", "—");
    minBtn.title = "最小化";
    minBtn.addEventListener("click", function (ev) {
      ev.stopPropagation();
      winCtrl("min");
    });
    bar.appendChild(minBtn);

    var maxBtn = el("div", "mbWinBtn", "□");
    maxBtn.title = "最大化";
    function syncMaxBtn(maxed) {
      window.__maxed = !!maxed;
      maxBtn.textContent = maxed ? "❐" : "□";
      maxBtn.title = maxed ? "向下还原" : "最大化";
    }
    function toggleMax() {
      fetch("/api/window?act=togglemax", { cache: "no-store" })
        .then(function (r) { return r.json(); })
        .then(function (d) { syncMaxBtn(d && d.maxed); })
        .catch(function () {});
    }
    maxBtn.addEventListener("click", function (ev) {
      ev.stopPropagation();
      toggleMax();
    });
    bar.appendChild(maxBtn);

    // 打开时同步一次当前状态，并跟随窗口尺寸变化刷新
    fetch("/api/window?act=state", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (d) { syncMaxBtn(d && d.maxed); })
      .catch(function () {});
    window.addEventListener("resize", function () {
      fetch("/api/window?act=state", { cache: "no-store" })
        .then(function (r) { return r.json(); })
        .then(function (d) { syncMaxBtn(d && d.maxed); })
        .catch(function () {});
    });

    var closeBtn = el("div", "mbWinBtn mbClose", "✕");
    closeBtn.title = "关闭";
    closeBtn.addEventListener("click", function (ev) {
      ev.stopPropagation();
      winCtrl("close");
    });
    bar.appendChild(closeBtn);

    document.body.appendChild(bar);

    function api() {
      return (window.pywebview && window.pywebview.api) ? window.pywebview.api : null;
    }

    /* ---------- 拖动窗口 ----------
       坐标换算全部交给后端：按下时只喊一声 "开始拖"，后端用 GetCursorPos + SetWindowPos
       自己盯光标挪窗口（同一套物理像素坐标，不会有 CSS/物理像素错位，也不会排队追不上）。 */
    bar.addEventListener("mousedown", function (ev) {
      if (ev.button !== 0) return;
      if (ev.target.closest(".mbItem") || ev.target.closest(".mbWinBtn")) return;
      ev.preventDefault();
      document.body.classList.add("mbDragging");
      var a = api();
      if (a && a.drag_begin) {
        a.drag_begin().catch(function () {});
      } else {
        fetch("/api/window?act=dragbegin", { cache: "no-store" }).catch(function () {});
      }
    });

    document.addEventListener("mouseup", function () {
      document.body.classList.remove("mbDragging");
    });

    bar.addEventListener("dblclick", function (ev) {
      if (ev.target.closest(".mbItem") || ev.target.closest(".mbWinBtn")) return;
      ev.preventDefault();
      toggleMax();
    });

    document.addEventListener("click", closeAll);
  /* fullscreenchange 监听已移除：不再使用浏览器全屏 API */

    updateChecks();
    applyRes(getRes());
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", build);
  } else {
    build();
  }
})();

  // ---------- 标题栏自动隐藏 ----------
  (function autoHideBar() {
    var bar = null;
    var tries = 0;
    function wait() {
      bar = document.getElementById("menuBar");
      if (!bar && tries++ < 50) { setTimeout(wait, 100); return; }
      if (!bar) return;
      init();
    }
    function init() {

    // 顶部热区: 鼠标碰到最上面 3px 就弹出
    var hot = document.createElement("div");
    hot.id = "mbHotZone";
    document.body.appendChild(hot);

    var hideTimer = null;
    function show() {
      if (hideTimer) { clearTimeout(hideTimer); hideTimer = null; }
      bar.classList.remove("mbHidden");
    }
    function hideSoon() {
      if (hideTimer) clearTimeout(hideTimer);
      hideTimer = setTimeout(function () {
        // 菜单展开时不收
        if (bar.querySelector(".mbItem.open")) return;
        bar.classList.add("mbHidden");
      }, 700);
    }

    document.addEventListener("mousemove", function (e) {
      var h = bar.offsetHeight || 22;
      if (e.clientY <= h + 6) show();
      else hideSoon();
    });
    hot.addEventListener("mouseenter", show);
    bar.addEventListener("mouseenter", show);
    hot.addEventListener("mouseleave", hideSoon);

    // 菜单展开期间保持可见
    bar.addEventListener("click", function () {
      if (bar.querySelector(".mbItem.open")) show();
    });

    // 键盘操作时也亮出来
    document.addEventListener("keydown", function (e) {
      if (e.key === "Alt") show();
    });
    }
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", wait);
    } else {
      wait();
    }
  })();

