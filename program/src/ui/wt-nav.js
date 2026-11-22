/* nav.js —— 从 game.js 搬出的功能块（逻辑未改）
 * 挂载：WA2.nav；同时同步到 window 以兼容旧调用。
 */
(function () {
  var WA2 = window.WA2 = window.WA2 || {};
  var $ = (WA2.util && WA2.util.$) || function (id) { return document.getElementById(id); };

  function playTitleBgm() {
    if (titleBgmStarted) return;
    titleBgmStarted = true;
    setBgm("menu", 400);
  }
  document.addEventListener("pointerdown", function once(e) {
    if ($("titleScreen").classList.contains("active")) playTitleBgm();
  }, { once: false });

  /* ---------- 菜单 ---------- */
  /* 结束游戏：先确认，再依次尝试 pywebview 桥接 → HTTP → window.close。
     （WebView2 里裸 window.close() 不一定关得掉，这是原来点了没反应的原因） */
  function quitGame(skipConfirm) {
    let yes = true;
    if (!skipConfirm) {
      try { yes = window.confirm("要结束游戏吗？"); } catch (e) { yes = true; }
    }
    /* 只有明确点了“取消”才不关；confirm 不可用（返回 undefined）时按继续处理 */
    if (yes === false) return false;
    try {
      if (window.pywebview && window.pywebview.api && window.pywebview.api.win) {
        window.pywebview.api.win("close");
      }
    } catch (e) {}
    try { fetch("/api/window?action=close", { cache: "no-store" }); } catch (e) {}
    setTimeout(function () { try { window.close(); } catch (e) {} }, 120);
    return true;
  }
  $("mainMenu").addEventListener("click", (e) => {
    const btn = e.target.closest("button");
    if (!btn) return;
    playSE("click");
    const act = btn.dataset.act;
    if (act === "quit") {
      /* 结束游戏：pywebview 里 window.close() 不一定生效，优先走桥接 / HTTP 关窗口 */
      quitGame();
      return;
    }
          if (act === "continue") {
            if (window.__openScreen) window.__openScreen("save", "load");
            return;
          }
          if (act === "config") { if (window.__openOptions) window.__openOptions(); return; }
          if (act === "special") { if (window.__openSpecial) window.__openSpecial(); return; }

      /* 进游戏前关掉所有浮层（统一走 screens.js，别在这里各自 remove("show")） */
    if (window.__closeScreens) window.__closeScreens();
    showScreen("gameScreen");
    titleBgmStarted = true;
    setBg("classroom");
    setSpriteByScene("classroom", "calm", true);
    gdiRenderName(CHAR_NAME, $("namePlate"));
    setBgm("calm", 600);
    if (window.__wantRestore) {
      window.__wantRestore = false;
    sessionRestore().then(function (ok) {
        if (!ok) setTimeout(() => { var _o = openingLine(); typewriter(_o.zh); speakLineJa(_o.ja, _o.emo); }, 200);
      });
    } else {
      // 开始游戏 = 新对话：清空历史与自动存档
      // 注意：历史数组在 wt-session.js 里，只能走它的 clearHistory()。
      // 这里以前写的是 `history = []`，那其实是浏览器的 window.history（History API），
      // 等于把 API 覆盖成数组、真正的历史一条没清 —— 点完「开始游戏」回想里还是上一局（2026-11-06）。
      try {
        if (window.WA2 && WA2.session && WA2.session.clearHistory) WA2.session.clearHistory();
        else if (window.__resetHistory) window.__resetHistory();
        else window.__history = [];
      } catch (e) { window.__history = []; }
      /* 同类的「上一局残留」一起清干净：轮数、氛围分、上一句的情绪/待切场景 */
      try { turnCount = 0; } catch (e) {}
      try { if (WA2.mood && WA2.mood.reset) WA2.mood.reset(); } catch (e) {}
      try { if (WA2.chat && WA2.chat.resetPending) WA2.chat.resetPending(); } catch (e) {}
      try { if (WA2.chat && WA2.chat.setSceneFlag) WA2.chat.setSceneFlag(0); } catch (e) {}   /* 新游戏：场面回到普通 */
      try { rebuildBacklog(); } catch (e) {}   /* 新游戏：回想清空 */
      try {
        fetch("/api/session/save", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ slot: "auto", messages: [], meta: {} })
        }).catch(function () {});
      } catch (e) {}
      setTimeout(() => { var _o = openingLine(); typewriter(_o.zh); speakLineJa(_o.ja, _o.emo); }, 460);
    }
    setTimeout(() => $("input").focus(), 700);
  });

  $("mainMenu").addEventListener("mouseover", (e) => {
    if (e.target.closest("button")) playSE("hover");
  });

  (function () {
    const b = $("cfgBtn");
    if (b) b.addEventListener("click", () => {
      playSE("click");
      if (window.__openScreen) window.__openScreen("opt");
    });
  })();

  ($("sendBtn")||{addEventListener:function(){}}).addEventListener("click", send);
  ($("input")||{addEventListener:function(){}}).addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); send(); }
  });
  /* 回标题：菜单「返回标题」和 ESC 共用 */
  function backToTitle() {
    playSE("cancel");
    showScreen("titleScreen");
    toggleBacklog(false);
    /* 选择分支整层由它自己收（含状态机与按钮等待态），别只摘 show */
    if (window.__closeChoices) window.__closeChoices();
    else $("choiceScreen").classList.remove("show");
    titleBgmStarted = false;
    setBgm("menu", 800);
  }

  ($("backBtn")||{addEventListener:function(){}}).addEventListener("click", backToTitle);

  /* ESC：对话界面直接回标题（回想 / 浮层先各自关掉，不抢） */
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if (backlogOpen) return;
    /* 选择分支开着：这一下归它自己关（它的 keydown 已经拦了，这里是兜底） */
    if (window.WA2 && WA2.choices && WA2.choices.isOpen && WA2.choices.isOpen()) return;
    if (window.__optOpen) return;
    if (window.__isScreenOpen && window.__isScreenOpen()) return;
    var gs = $("gameScreen");
    if (!gs || !gs.classList.contains("active")) return;
    backToTitle();
  });

  ($("msgBox")||{addEventListener:function(){}}).addEventListener("click", () => {
    if (typing) { stopReveal(); updatePageCursor(); return; }   // 打字途中点一下 = 立刻显示整句
    nextPage();          // 还有下一页就翻页
  });

  /* 工具栏 */
  document.querySelectorAll("#toolbar .tb").forEach(el => {
    el.addEventListener("click", () => {
      const a = el.dataset.act;
      if (a === "save")  window.__openScreen && window.__openScreen("save", "save");
      if (a === "load")  window.__openScreen && window.__openScreen("save", "load");
      /* 快速存档 / 快速读档：暂不实现（按钮先提示一下，避免误以为打开的是存档界面） */
      if (a === "qsave") { if (window.__saveHint) window.__saveHint("快速存档暂未开放"); return; }
      if (a === "qload") { if (window.__saveHint) window.__saveHint("快速读档暂未开放"); return; }
          if (a === "config") { window.__openOptions && window.__openOptions(); return; }
      if (a === "rew")   toggleBacklog(true);          // 浏览本次对话历史
      if (a === "close") {
        /* 右下角那个 ✕ = 隐藏窗口（只留 CG 与立绘），和右键「隐藏窗口」同一套；
           再点一次右键或这个按钮就回来 */
        document.body.classList.toggle("uiHidden");
        return;
      }
    });
    el.addEventListener("mouseenter", () => {
      playSE("hover");
      $("tbHint").textContent = el.dataset.tip ? "▶ " + el.dataset.tip : "";
    });
    el.addEventListener("mouseleave", () => { $("tbHint").textContent = ""; });
    el.addEventListener("click", () => {
      playSE("click");
      if (el.dataset.act === "log") toggleBacklog();
      if (el.dataset.act === "bgm") {
        WA2.audio.enabled = !WA2.audio.enabled;
        if (!WA2.audio.enabled) WA2.audio.pauseBgm(); else setBgm(WA2.audio.current || "calm", 300);
        el.style.opacity = WA2.audio.enabled ? "1" : ".4";
      }
    });
  });

  /* 键盘 */
  document.addEventListener("keydown", (e) => {
    if (e.target.tagName === "INPUT") return;
    if (e.key === "c") showChoices();
    if (e.key === "l" || e.key === "L") toggleBacklog();
    if (e.key === "Escape") toggleBacklog(false);
  });






  /* ---------- 思考显示开关 ---------- */
  function thinkOn() {
    var v = localStorage.getItem("app_show_think");
    if (v === null) { try { localStorage.setItem("app_show_think", "1"); } catch (e) {} return true; }
    return v === "1";
  }

  /* 动作文本的「字模」渲染：和对话框走**同一套**参数 —— 后端按 FONT_SIZE=28 带描边烤图，
     前端按 TEXT_SCALE（默认 0.625）缩放，所以字形、描边、颜色、字重和对话框完全一致
     （显示字号 ≈ 28 × 0.625 ≈ 17.5px，和对话框一样）。
     另外按 chars_per_line 折行：图片高度随行数长，宽度按缩放算死，
     不会被挤小、也没有行数裁剪。
     为什么不用 uitext.js：它把整段渲成**一张单行图**（height=15.5px, width:auto），
     长句就会被压成小字。 */
  var ACT_TX_VER = "20261122b";
  var ACT_FONT_PX = 28;          /* 后端字模字号（sub_render.FONT_SIZE） */
  function renderActionImage(box) {
    var text = (box.textContent || "").trim();
    if (!text) return;
    /* 显示比例要和对话框用**同一个**（用户在设置里能调，存在 localStorage：
       app_textScale；window.TEXT_SCALE 是模块加载时的旧副本，不能直接用） */
    var scale = 0.625;
    try {
      var _ls = parseFloat(localStorage.getItem("app_textScale") || "");
      if (_ls > 0.2 && _ls < 3) scale = _ls;
      else if (window.TEXT_SCALE > 0.2 && window.TEXT_SCALE < 3) scale = window.TEXT_SCALE;
    } catch (e) {}
    /* 框的**逻辑**宽度：#stage 固定 800 宽，#thinkBox 是 36% → 288px。
       不能拿 getBoundingClientRect()：它含 fitStage() 的缩放（窗口变大时会被放大），
       会算出偏大的每行字数；而图片宽度是按逻辑 px 设置的，两者必须同一套坐标。 */
    var boxW = 800 * 0.36;
    /* 一行放几个字：按「显示后的字宽」算（28px 字模 × 缩放 = 显示字宽） */
    var glyphPx = ACT_FONT_PX * scale;
    var perLine = Math.max(4, Math.floor(boxW / Math.max(8, glyphPx)));
    /* 和对话框同样的参数（s/e 是 GLIB 兜底用的，plain 不传 = 带描边）；
       w 给大值，让折行只由 chars_per_line 决定 */
    var url = "/api/text?text=" + encodeURIComponent(text) + "&s=56&w=2000&e=1" +
              "&chars_per_line=" + perLine + "&v=" + ACT_TX_VER;
    var img = new Image();
    img.onload = function () {
      if (box.textContent.trim() !== text) return;      /* 期间又换了内容：丢弃旧图 */
      box.textContent = "";
      img.setAttribute("data-uitxt", text);
      img.style.cssText = "display:block;width:" + (img.naturalWidth * scale).toFixed(1) +
                          "px;height:auto;pointer-events:none";
      img.alt = "";
      box.appendChild(img);
    };
    img.onerror = function () { /* 渲染失败就保持真文字，不让人看不到 */ };
    img.src = url;
  }

  function renderThinking() {
    const box = $("thinkBox");
    const btn = $("thinkBtn");
    const on = thinkOn();
    if (btn) {
    var v = btn.querySelector("img.val");
    var k = on ? "log_act_onv" : "log_act_offv";
    if (v && v.dataset.baked !== k) { v.dataset.baked = k; v.src = "assets/ui/text/" + k + ".png"; }
    }
    if (!box) return;
    const act = (window.__lastAction || "").trim();
    const t = (window.__lastThinking || "").trim();
    const body = act || t;
    if (on && body) {
      // 模型有时自己带了（）/「」，先剥掉再统一包一层
      var clean = body.replace(/^[（(【\[]+/, "").replace(/[）)】\]]+$/, "").trim();
      box.textContent = "（" + clean + "）";
      /* 字号固定成和对话框一致（15.5px），不按长度缩、也不裁行数：
         字多就往下多占几行（样式见 game450.css 的 #thinkBox）。 */
      box.style.fontSize = "";
      box.style.webkitLineClamp = "";
      box.classList.remove("hidden");
      /* 换成字模图（和对话框同一套字体）；拿不到就保持真文字 */
      try { renderActionImage(box); } catch (e) {}
    } else {
      box.textContent = "";
      box.classList.add("hidden");
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    const btn = $("thinkBtn");
    if (btn) {
      btn.addEventListener("click", function () {
        localStorage.setItem("app_show_think", thinkOn() ? "0" : "1");
        renderThinking();
      });
    }
    renderThinking();
  });


  /* ---------- 按钮三态: 松开时闪现第三帧 ---------- */
  document.addEventListener("DOMContentLoaded", function () {
    (function bakeSend() {
      var b = document.getElementById("sendBtn");
      if (b && b.dataset.baked !== "log_send") {
        b.dataset.baked = "log_send";
        b.innerHTML = '<img src="assets/ui/text/log_send.png" alt="">';
      }
    })();
    ["thinkBtn", "sendBtn"].forEach(function (id) {
      const b = document.getElementById(id);
      if (!b) return;
      b.addEventListener("pointerup", function () {
        b.classList.add("released");
        setTimeout(function () { b.classList.remove("released"); }, 130);
      });
    });
  });


  /* ===================== 历史对话面板 ===================== */
  const HIST_THUMB_RATIO = 0.158;
  let histMaxTop = 1, histTopGap = 3, histBottomGap = 5;   // 滑块占滑条高度比例（游戏实测 176/1366）
  var _exp = {};
  var _names = ["backToTitle", "playTitleBgm", "quitGame", "renderThinking", "thinkOn"];
  for (var _i = 0; _i < _names.length; _i++) {
    try { var _f = eval(_names[_i]); if (typeof _f === "function") { _exp[_names[_i]] = _f; window[_names[_i]] = _f; } } catch (e) {}
  }
  WA2.nav = _exp;
  WA2.register("nav", _exp);
  /* __export_all__ */
  ["backToTitle", "playTitleBgm", "quitGame", "renderThinking", "thinkOn", "HIST_THUMB_RATIO", "L", "WA2", "_exp", "_names", "a", "act", "b", "body", "box", "btn", "clean", "fs", "gs", "histMaxTop", "k", "on", "t", "v", "yes"].forEach(function (n) {
    try { var v = eval(n); if (v !== undefined) { window[n] = v; } } catch (e) {}
  });
})();

/* 兼容：菜单/按钮可能直接调 window.__quitGame */
if (typeof window.__quitGame !== "function" && typeof quitGame === "function") {
  window.__quitGame = quitGame;
}
