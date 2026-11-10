/* session.js —— 从 game.js 搬出的功能块（逻辑未改）
 * 挂载：WA2.session；同时同步到 window 以兼容旧调用。
 */
(function () {
  var WA2 = window.WA2 = window.WA2 || {};
  var $ = (WA2.util && WA2.util.$) || function (id) { return document.getElementById(id); };

  function sessionSave() {
    try {
      /* 返回 promise：切角色前要等这一笔写完再换人，
         否则后端已经把存档目录切到新角色，这一笔会落到别人名下 */
      return fetch("/api/session/save", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          slot: "auto",
          messages: (window.__history || history || []),
          meta: (window.__saveMeta && window.__saveMeta()) ||
                { bg: (document.body.dataset.bg || ""), outfit: (WA2.state.outfit || ""), at: Date.now() }
        })
      }).catch(function () {});
    } catch (e) { return null; }
  }

  /* 读档：存档里带着「当时还没选的分支」就原样摆回来，不再问模型。
     反过来，读的档里没有这一项时，手上还开着的旧选项也要收掉——
     否则会出现「读了个没分支的档，屏幕上还挂着上一个档的选项」。 */
  function restoreChoices(meta) {
    try {
      var c = meta && meta.choice;
      var has = !!(c && c.length);
      var ch = window.WA2 && WA2.choices;
      if (has && ch && ch.show) {
        ch.show(c);
        return true;
      }
      if (ch && ch.isOpen && ch.isOpen()) ch.hide();
    } catch (e) {}
    return false;
  }

  /* 读档：保持 history 是同一个数组（别的模块持有引用），只换内容 */
  function applyHistory(arr) {
    history.length = 0;
    (arr || []).forEach(function (m) { history.push(m); });
    window.__history = history;
  }

  /* 最后一句和纱的话（读档后要显示并重播）。
     原来两处各抄了一遍同样的 for 循环，合成一个。 */
  function lastAssistant() {
    for (var i = history.length - 1; i >= 0; i--) {
      if (history[i] && history[i].role === "assistant") {
        /* 带上当时的情绪：不带就会退回 calm，重播既变平静又要重新合成 */
        return { zh: history[i].zh || history[i].content || "",
                 ja: history[i].content || "",
                 emo: history[i].emo || "" };
      }
    }
    return { zh: "", ja: "" };
  }

  /* 读档回显那一句：和平时一样，角色台词包「」，旁白不包 */
  function quoteLine(text) {
    var s = (text || "").trim();
    if (!s) return "";
    if (/^[（(【]/.test(s)) {                          /* 旁白：去括号、不加「」 */
      return s.replace(/^[（(【\[]+/, "").replace(/[）)】\]]+$/, "").trim();
    }
    return "「" + s.replace(/^[「『]+|[」』]+$/g, "") + "」";
  }

  /* 读档 / 新游戏时按存档重置「当前是不是特殊场面」（标记归 WA2.chat 管） */
  function setSceneFlag(isSpecial) {
    try {
      if (window.WA2 && WA2.chat && WA2.chat.setSceneFlag) WA2.chat.setSceneFlag(!!isSpecial);
      else document.body.dataset.hscene = isSpecial ? "1" : "0";
    } catch (e) {}
  }


  function sessionRestore() {
    return fetch("/api/session/load?slot=auto", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d || !d.ok) {
          /* 存档 schema 读不了的时候说清楚原因，别静默当没有存档 */
          var e = d && d.error, t = window.__toast || function () {};
          if (e === "save_too_new") t("存档来自更新版本，请先升级程序");
          else if (e === "corrupt") t("存档已损坏，读不出来");
          else if (e === "migrate_failed" || e === "migrate_missing") t("存档升级失败，原档已备份");
          return false;
        }
        if (!d.messages || !d.messages.length) return false;
        applyHistory(d.messages);
        const meta = d.meta || {};
        if (meta.bg) { setBg(meta.bg); }
        else if (!currentBgName()) { setBg("classroom"); }   /* 旧档没存背景：别让它是空的，否则之后存的档也空 */
        if (meta.outfit) { WA2.state.outfit = meta.outfit; }
        /* 立绘表情 / BGM 按存档还原（以前这里只还原背景，表情停在默认） */
        try {
          var _hsNow = (meta.hscene === 1 || meta.hscene === "1");
          setSceneFlag(_hsNow);
          try { WA2.voice.setEmotion(meta.emo || (_hsNow ? "H" : "calm")); } catch (e) {}
          var _spriteEmo = _hsNow ? "H" : (meta.emo || "calm");
          setSpriteByScene(meta.bg || "classroom", _spriteEmo, true, meta.outfit || "");
          if (meta.bgm) setBgm(meta.bgm, 600);
        } catch (e) {}
        var line = lastAssistant();
        if (line.zh) {
          try { showReply(quoteLine(line.zh)); } catch (e) { $("msgText").textContent = quoteLine(line.zh); }
          if (line.ja && !/^[（(【]/.test((line.zh || "").trim())) {
            speakLineJa(line.ja, line.emo || window.__lastEmo || "");
          }
        }
        if (meta.outfit) {
          try { setSpriteByScene(meta.bg || "classroom", "calm", true, meta.outfit); } catch (e) {}
        }
        console.log("[session] 已恢复", history.length, "条");
        try { rebuildBacklog(); } catch (e) {}
        restoreChoices(meta);
        return true;
      })
      .catch(function () { return false; });
  }

  /* 启动时问后端：上次是不是正常退出。不是就提示一句（存档本身仍走「继续」恢复） */
  function runStateCheck() {
    return fetch("/api/run/state", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d && d.ok && d.dirty_exit) {
          var t = window.__toast || function () {};
          t(d.has_autosave ? "上次没有正常退出，可从「继续」恢复" : "上次没有正常退出", 4000);
          console.log("[run] 上次异常退出", d.prev || {});
        }
        return d || null;
      })
      .catch(function () { return null; });
  }

  document.addEventListener("DOMContentLoaded", function () {
    setTimeout(runStateCheck, 1500);
  });

  /* 读档：把存档恢复到当前对话并进入游戏 */
  /* 当前背景名：真正的值由 game.js 的 setBg 写在 body.dataset.bg 上；
     这个文件里那个 currentBg 从来没被赋过值，别再读它 */
  function currentBgName() {
    try { return document.body.dataset.bg || ""; } catch (e) { return ""; }
  }

  window.__loadSession = function (data) {
    try {
      applyHistory(data.messages);
      const meta = data.meta || {};
      showScreen("gameScreen");
      titleBgmStarted = true;
      if (meta.bg) { setBg(meta.bg); }
      else if (!currentBgName()) { setBg("classroom"); }
      if (meta.outfit) { WA2.state.outfit = meta.outfit; }
      var _hsNow2 = (meta.hscene === 1 || meta.hscene === "1");
      setSceneFlag(_hsNow2);
      try { WA2.voice.setEmotion(meta.emo || (_hsNow2 ? "H" : "calm")); } catch (e) {}
      var _spriteEmo = _hsNow2 ? "H" : (meta.emo || "calm");
      setSpriteByScene(meta.bg || "classroom", _spriteEmo, true, meta.outfit || "");
      gdiRenderName(CHAR_NAME, $("namePlate"));
      setBgm(meta.bgm || meta.emo || "calm", 600);
      var line = lastAssistant();
      if (line.zh) {
        try { showReply(quoteLine(line.zh)); } catch (e) { $("msgText").textContent = quoteLine(line.zh); }
        if (line.ja && !/^[（(【]/.test((line.zh || "").trim())) {
          speakLineJa(line.ja, line.emo || window.__lastEmo || "");
        }
      }
      try { rebuildBacklog(); } catch (e) {}
      /* 存档里存着「还没选的分支」：摆回来，并且不要抢输入框焦点 */
      var hadChoices = restoreChoices(meta);
      if (!hadChoices) setTimeout(function () { $("input").focus(); }, 300);
    } catch (e) { console.warn("load session failed", e); }
  };

  /* 存档界面用：取当前会话要写进 meta 的东西（背景 / 立绘 / 待选分支）
     currentBg、currentOutfit 是模块内变量，这里只读不写 */
  window.__saveMeta = function () {
    var meta = {
      /* 背景名以 setBg 写进 body.dataset 的为准 */
      bg: (document.body.dataset.bg || ""),
      outfit: (WA2.state.outfit || ""),
      /* 立绘表情和 BGM 也要存：以前读档写死 calm，导致「回到默认立绘和场景」 */
      emo: (window.__lastEmo || ""),
      /* 当时是不是成人场面：表情标签可能被写成 calm，光靠 emo 会还原成平静脸 */
      hscene: (document.body.dataset.hscene === "1") ? 1 : 0,
      bgm: (WA2.audio && WA2.audio.current) || "",
      at: Date.now()
    };
    /* 选择分支开着、而且一条都没选 → 把这几个选项一起存。
       已经选过的这一层已经关了，pendingOptions() 会给 null。 */
    try {
      var c = (WA2.choices && WA2.choices.pendingOptions) ? WA2.choices.pendingOptions() : null;
      if (c && c.length) meta.choice = c;
    } catch (e) {}
    return meta;
  };
  /* 存档界面用：按场景名取背景图，给槽位缩略图用 */
  window.__bgThumb = function (name) { return bgSrc(name); };
  /* 调试/探针用：直接切到某张场景图（传 CG 的 id，或旧背景名） */
  window.__scenePreview = function (name) { setBg(name); return { bg: currentBgName(), url: bgSrc(name) }; };
  /* 工具栏提示（快速存档/读档暂未开放时用） */
  window.__saveHint = function (msg) {
    var h = document.getElementById("tbHint");
    if (!h) return;
    h.textContent = msg;
    clearTimeout(window.__saveHintTimer);
    window.__saveHintTimer = setTimeout(function () { h.textContent = ""; }, 1500);
  };

  /* 存档界面用：把当前画面（背景 CG + 立绘）截成缩略图
     160x100，和槽位的缩略图框同比例 */
  window.__grabThumb = function () {
    try {
      var W = 160, H = 100;
      var cv = document.createElement("canvas");
      cv.width = W; cv.height = H;
      var g = cv.getContext("2d");
      g.fillStyle = "#0a1622"; g.fillRect(0, 0, W, H);

      var bg = window.__curBg;
      if (bg && bg.complete && bg.naturalWidth) {
        var s = Math.max(W / bg.naturalWidth, H / bg.naturalHeight);
        var w = bg.naturalWidth * s, h = bg.naturalHeight * s;
        g.drawImage(bg, (W - w) / 2, (H - h) / 2, w, h);
      }
      var stage = document.getElementById("stage");
      var sp = document.getElementById("sprite");
      if (stage && sp && sp.naturalWidth) {
        var sr = stage.getBoundingClientRect();
        var r = sp.getBoundingClientRect();
        if (sr.width > 0 && sr.height > 0) {
          g.drawImage(sp,
            (r.left - sr.left) / sr.width * W,
            (r.top - sr.top) / sr.height * H,
            r.width / sr.width * W,
            r.height / sr.height * H);
        }
      }
      return cv.toDataURL("image/jpeg", 0.72);
    } catch (e) { return ""; }
  };

  /* ---------- 场景切换 ---------- */

  /* 开新游戏：清空对话历史（**必须走这里**）。
     以前 wt-nav 里直接写 `history = []` —— 那不是本模块的数组，而是浏览器的
     `window.history`，等于把 History API 覆盖成数组、而真正的历史一条没清：
     点「开始游戏」后回想里还是上一局内容（2026-11-06 用户反馈）。
     顺带用同一个数组引用（applyHistory 保持引用不变），别处的 window.__history 也跟着空。 */
  function clearHistory() {
    applyHistory([]);
    return true;
  }

  var _exp = {};
  var _names = ["sessionRestore", "sessionSave", "runStateCheck", "clearHistory"];
  for (var _i = 0; _i < _names.length; _i++) {
    try { var _f = eval(_names[_i]); if (typeof _f === "function") { _exp[_names[_i]] = _f; window[_names[_i]] = _f; } } catch (e) {}
  }
  WA2.session = _exp;
  /* 用函数覆盖导出串里的同名变量（那个 currentBg 永远是 null） */
  try { WA2.session.currentBg = currentBgName; } catch (e) {}
  WA2.register("session", _exp);
  /* __export_all__ */
  ["sessionRestore", "sessionSave", "clearHistory", "W", "WA2", "_exp", "_names", "bg", "cv", "g", "h", "meta", "r", "s", "sp", "sr", "stage", "w"].forEach(function (n) {
    try { var v = eval(n); if (v !== undefined) { window[n] = v; } } catch (e) {}
  });
})();
