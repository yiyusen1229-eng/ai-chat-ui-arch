/* backlog.js —— 从 game.js 搬出的功能块（逻辑未改）
 * 挂载：WA2.backlog；同时同步到 window 以兼容旧调用。
 */
(function () {
  var WA2 = window.WA2 = window.WA2 || {};
  var $ = (WA2.util && WA2.util.$) || function (id) { return document.getElementById(id); };

  /* 把「这句当初合成语音用的参数」记到最后一条她的行上。
   调用方（wt-chat）在语音请求发出后调用一次即可。 */
  function setVoiceOnLast(req) {
    if (!req || !req.t) return false;
    try {
      const list = $("historyList");
      if (!list) return false;
      const rows = list.querySelectorAll(".hline.her");
      const row = rows[rows.length - 1];
      if (!row) return false;
      row.dataset.voice = JSON.stringify({ t: req.t, l: req.l || "", e: req.e || "" });
      return true;
    } catch (e) { return false; }
  }

  function addHistory(role, text, raw, emo, speaker, narr) {
    const d = document.createElement("div");
    d.className = "hline " + (role === "user" ? "me" : "her");
    if (raw) d.dataset.ja = raw;      /* 原始日文（含 action/emotion），只存不显示 */
    /* 这句当时的情绪：重播要用它算缓存 key。
       以前重播写死 emotion=neutral，而当时合成用的是 calm/毒舌 → 缓存永远不命中，
       每次重播都要重新合成 2~5 秒（用户报「重放不像重放，像重新加载」）。 */
    if (emo) d.dataset.emo = emo;
    if (speaker) d.dataset.sp = speaker;      /* 这句是谁说的（多角色时不能都写当前角色） */
    if (narr) d.dataset.narr = "1";           /* 旁白：不加「」、不写名字 */
    const ico = document.createElement("div");
    ico.className = "ico";
    const ic = document.createElement("img");
    ic.alt = "";
    /* 图标取自 sys_00031：灰气泡=用户（无语音）、白气泡=和纱、白气泡+播放三角=悬停 */
    const canReplay = (role === "assistant") && !!raw && !!window.__voiceOk;
    ic.src = "assets/ui/history/" + (canReplay ? "icon_her" : "icon_me") + ".png";
    ico.appendChild(ic);
    const body = document.createElement("div");
    body.className = "body";
    const who = document.createElement("span");
    who.className = "who";
    who.textContent = role === "user" ? "你" : (narr ? "" : (d.dataset.sp || CHAR_NAME));
    /* 和纱的话统一加「」：存档里的 zh 是翻译原文（不带括号），实时消息带括号，这里统一 */
    if (role === "assistant" && !narr) {
      text = "「" + String(text).replace(/^[「『]/, "").replace(/[」』]$/, "") + "」";
    }
    /* 长句按面板宽度折行：每行最多 26 个全角字（游戏里也是这个宽度） */
    const CH = 26;
    const lines = [];
    for (let i = 0; i < text.length; i += CH) lines.push(text.slice(i, i + CH));
    const txt = document.createElement("div");
    txt.className = "txtwrap";
    lines.forEach(function (ln) {
      const row = document.createElement("div");
      row.className = "txt";
      row.dataset.uitxtPerLine = "26";   /* 回想面板一行 26 字，和游戏一致 */
      row.textContent = ln;
      txt.appendChild(row);
    });
    body.appendChild(who);
    body.appendChild(txt);
    d.appendChild(ico); d.appendChild(body);
    /* 悬停高亮 + 点击重播（只有和纱且有日文原句、且语音可用时才挂） */
    if (canReplay) {
      ico.style.cursor = "pointer";
      ico.title = "重播这句的语音";
      ico.addEventListener("mouseenter", function () {
        ic.src = "assets/ui/history/icon_her_hover.png";
        setRowHighlight(d, true);
      });
      ico.addEventListener("mouseleave", function () {
        ic.src = "assets/ui/history/icon_her.png";
        if (d.dataset.playing !== "1") setRowHighlight(d, false);
      });
      ico.addEventListener("click", function (e) {
        e.stopPropagation();
        replayBacklogVoice(d);
      });
    }
    $("historyList").appendChild(d);
    $("historyList").scrollTop = $("historyList").scrollHeight;
  }

  /* 高亮色：取游戏原图里黄字字芯的颜色 RGB(250,246,164) = #FAF6A4 */
  const HL_COLOR = "faf6a4";
  function setRowHighlight(row, on) {
    if (!row) return;
    /* 只切 class；黄色由 CSS 滤镜给（参数按游戏原图黄字字芯色反解出来的） */
    row.classList.toggle("hl", !!on);
  }

  /* 重播某一行：把这条记录的日文原句重新丢给语音模型 */
  let backlogPlayingRow = null;
  function replayBacklogVoice(row) {
    const ja = (row.dataset.ja || "").trim();
    /* 以前这里还要 window.__voiceOk —— 那是页面加载时查一次的标志，
       语音服务中途重启/没就绪就永远 false，点了回想一点反应都没有（连请求都不发）。
       现在不拦：真没就绪服务端会返回 503，界面会给出提示。 */
    if (!ja) return;
    if (WA2.perf && !WA2.perf.can("REPLAYING")) return;   // 切角色 / 流式输出中不许重播
    if (WA2.perf) { WA2.perf.interrupt("REPLAYING"); WA2.perf.begin("REPLAYING"); }
    if (backlogPlayingRow && backlogPlayingRow !== row) {
      delete backlogPlayingRow.dataset.playing;
      setRowHighlight(backlogPlayingRow, false);
      const prevIc = backlogPlayingRow.querySelector(".ico img");
      if (prevIc) prevIc.src = "assets/ui/history/icon_her.png";
    }
    backlogPlayingRow = row;
    try { stopSpeak(); } catch (e) {}
    row.dataset.playing = "1";
    setRowHighlight(row, true);
    const clean = ja.replace(/<[^>]*>/g, "")
                    .replace(/^[「『]/, "").replace(/[」』]$/, "").trim();
    /* 用这句当时的情绪（dataset.emo）算缓存 key，才能命中原来那段音频；
       老存档没有 emo 才退回按文本猜。 */
    /* 优先用「当初合成时」的参数（dataset.voice）：同一句重放必须命中同一段缓存。
       没有记录（老存档 / 老行）才退回按当前语言与情绪推算。 */
    let voiceReq = null;
    try { voiceReq = row.dataset.voice ? JSON.parse(row.dataset.voice) : null; } catch (e) { voiceReq = null; }
    const emo = (voiceReq && voiceReq.e) ? String(voiceReq.e)
              : ((row.dataset.emo || "").trim() ||
                 (window.emoOf ? window.emoOf(clean) : "calm"));
    const vLang = (voiceReq && voiceReq.l) ? String(voiceReq.l) : "ja";
    const done = function () {
      if (done._ended) return;
      done._ended = true;
      if (WA2.perf) WA2.perf.end("REPLAYING");
      delete row.dataset.playing;
      setRowHighlight(row, false);
      if (backlogPlayingRow === row) backlogPlayingRow = null;
    };
    const vText = (voiceReq && voiceReq.t) ? String(voiceReq.t) : clean;
    /* 旧存档里的 emo 是生成时写死的（可能是 calm）；当前确实在成人场面时，
       带 hscene=1 让后端按 H 念，和实时播放保持一致 */
    const _hs = (row.dataset.hscene === "1" ||
                 (document.body && document.body.dataset.hscene === "1")) ? "&hscene=1" : "";
    /* 多角色：这一行是谁说的就用谁的音色（没有就用当前角色） */
    var _sp = (row.dataset.sp || "").trim();
    var _charQ = _sp ? ("&char=" + encodeURIComponent(_sp)) : "";
    fetch("/api/tts?text=" + encodeURIComponent(vText) + "&lang=" + encodeURIComponent(vLang) +
          "&emotion=" + encodeURIComponent(emo) + "&speak_ja=" + encodeURIComponent(clean) + _hs + _charQ)
      .then(function (r) { if (!r.ok) throw new Error("tts " + r.status); return r.blob(); })
      .then(function (b) {
        const u = URL.createObjectURL(b);
        const a = new Audio(u);
        WA2.voice._tts(a);
        try { a.volume = voiceVolume(); } catch (e) {}
        a.onended = function () { URL.revokeObjectURL(u); done(); };
        a.onerror = function () { URL.revokeObjectURL(u); done(); };
        a.play().catch(function () { done(); });
      })
      .catch(function () { done(); });
  }
  /* 用当前 history 重建回想列表（读档 / 自动恢复 / 开新游戏时用） */
  function rebuildBacklog() {
    const list = $("historyList");
    if (!list) return;
    list.innerHTML = "";
    (window.__history || history || []).forEach(function (m) {
      if (!m) return;
      const isHer = m.role === "assistant";
      const shown = isHer ? (m.zh || m.content || "") : (m.content || "");
      if (!shown) return;
      addHistory(m.role, shown, isHer ? (m.content || "") : "",
                 isHer ? (m.emo || "") : "", isHer ? (m.speaker || "") : "",
                 isHer ? !!m.narr : false);
    });
  }

  function toggleBacklog(force) {
    backlogOpen = force !== undefined ? force : !backlogOpen;
    $("historyPanel").classList.toggle("show", backlogOpen);
    document.body.classList.toggle("histOpen", backlogOpen);
    if (backlogOpen) {
      const list = $("historyList");
      const toBottom = function () {
        if (!list) return;
        list.scrollTop = list.scrollHeight;      // 最新一句在最底部
        try { syncHistThumb(); } catch (e) {}
      };
      // 面板刚显示时高度还没算出来，分几拍滚到底
      requestAnimationFrame(function () {
        toBottom();
        requestAnimationFrame(toBottom);
      });
      // 布局稳定前多试几次（面板刚显示时高度为 0）
      [40, 80, 140, 220, 320, 480, 700].forEach(function (t) {
        setTimeout(toBottom, t);
      });
      // 历史面板里的文字同样走 /api/text 渲染（面板显示后才有尺寸）
      if (window.__uiTextScan) window.__uiTextScan(document.getElementById("historyList"));
    }
    playSE(backlogOpen ? "click" : "cancel");
  }

  /* ---------- 选项 ---------- */
  var _exp = {};
  var _names = ["addHistory", "rebuildBacklog", "replayBacklogVoice", "setRowHighlight", "setVoiceOnLast", "toggleBacklog"];
  for (var _i = 0; _i < _names.length; _i++) {
    try { var _f = eval(_names[_i]); if (typeof _f === "function") { _exp[_names[_i]] = _f; window[_names[_i]] = _f; } } catch (e) {}
  }
  WA2.backlog = _exp;
  WA2.register("backlog", _exp);
  /* __export_all__ */
  ["addHistory", "rebuildBacklog", "replayBacklogVoice", "setRowHighlight", "setVoiceOnLast", "toggleBacklog", "CH", "HL_COLOR", "WA2", "_exp", "_names", "a", "backlogPlayingRow", "body", "canReplay", "clean", "d", "done", "ic", "ico", "isHer", "ja", "lines", "list", "prevIc", "row", "shown", "toBottom", "txt", "u", "who"].forEach(function (n) {
    try { var v = eval(n); if (v !== undefined) { window[n] = v; } } catch (e) {}
  });
})();

/* 兼容：读档/切换角色后可能直接调 window.__rebuildBacklog */
if (typeof window.__rebuildBacklog !== "function") {
  window.__rebuildBacklog = function () { try { return rebuildBacklog(); } catch (e) {} };
}
