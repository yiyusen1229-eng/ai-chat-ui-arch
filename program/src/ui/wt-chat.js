/* chat.js —— 从 game.js 搬出的功能块（逻辑未改）
 * 挂载：WA2.chat；同时同步到 window 以兼容旧调用。
 */
(function () {
  var WA2 = window.WA2 = window.WA2 || {};
  var $ = (WA2.util && WA2.util.$) || function (id) { return document.getElementById(id); };

  /* 渲染一条后端回复：更新历史/立绘/BGM/语音，返回「语音播完」的 Promise。
     自动续说也走这里（先 generateContinue 预生成，再 renderQueued 显示）。 */
  async function applyServerReply(d) {
    if (d && d.usage) { try { (WA2.cost && WA2.cost.render ? WA2.cost.render : window.renderCost)(d.usage); } catch (e) {} }
    if (d && d.error) { $("msgText").textContent = "[错误] " + d.error; return false; }

      if (d && d.usage) { try { (WA2.cost && WA2.cost.render ? WA2.cost.render : window.renderCost)(d.usage); } catch (e) {} }
      if (d.error) { $("msgText").textContent = "[错误] " + d.error; return; }
      /* #24：静默降级会让人以为云端模型突然变笨了，所以降级要说一声；
         预算提醒也搭这趟车（后端只在到 80% / 超额时才给这句话） */
      try {
        if (d.degraded && window.__toast) {
          window.__toast("云端不可用（" + (d.degrade_reason || "错误") + "），这一轮用的是本地模型", 3200);
        }
        if (d.budget_warn && window.__toast) window.__toast(d.budget_warn, 4000);
      } catch (e) {}
      const reply = (d.reply || "").trim();
        const replyZh = (d.reply_zh || "").trim();
        const actZh = (d.action_zh || "").trim();
    window.__lastEmo = (d.emotion || "").trim();   // 服务端给的情绪标签
    window.__nextScene = (d.bg || "").trim();      // 服务端已按文件名标签匹配好；没匹配上就是空
    window.__nextOutfit = (d.outfit || "").trim(); // 只有明确换衣服才有值
    var _isNarr = (d.line_type === "narr");
    var _speaker = (d.speaker || "").trim();
    /* 模型常把旁白整句写成「（…）」：这种也按旁白处理 */
    if (!_isNarr && /^[（(【]/.test((d.reply_zh || d.reply || "").trim())) {
      _isNarr = true;
    }
    if (_isNarr) {
      _speaker = "";
    } else if (!_speaker) {
      /* 聊天路径没有 speaker 字段：默认就是当前角色，名字牌要照常画回来 */
      try { if (typeof CHAR_NAME !== "undefined" && CHAR_NAME) _speaker = String(CHAR_NAME); } catch (e) {}
    }
    window.__lastThinking = (d.thinking || "").trim();
    window.__lastAction = (d.action_zh || d.action || "").trim();
    /* 旁白（不是角色说的话）：不进左上角动作区 */
    if (_isNarr) { window.__lastThinking = ""; window.__lastAction = ""; }
    /* 当前是不是成人场面（后端判定）：写在 body 上，回想重放旧句时要读它（不新增全局） */
    /* 场面标记要有粘性：单句没带标记不该立刻退回普通参考
       （否则同一场戏里时好时坏，回想重播反而对）。连续 3 句都没有才回落。 */
    try {
      if (d.hscene === undefined || d.hscene === null) {
        /* 队列/导演的条目不带这个字段：保持当前场面不变，也不要加连击 */
      } else if (d.hscene) {
        window.__hsceneStreak = 0;
        document.body.dataset.hscene = "1";
      } else {
        window.__hsceneStreak = (window.__hsceneStreak || 0) + 1;
        if (window.__hsceneStreak >= 3) document.body.dataset.hscene = "0";
      }
    } catch (e) {}
    renderThinking();
      history.push({ role: "assistant", content: reply });
      window.__history = history;
      // 对话框显示中文（有译文用译文），历史记录也显示中文
      /* 和原版一致：角色台词 = 「…」（名字在名字牌上）；旁白 = 没有名字、也没有「」 */
      var _lineText = (replyZh || reply).trim();
      let shown;
      if (_isNarr) {
        shown = _lineText
          .replace(/^[（(【\[「]+/, "")
          .replace(/[）)】\]」]+$/, "")
          .trim();
      } else {
        shown = "「" + _lineText.replace(/^[「『]+|[」』]+$/g, "") + "」";
      }
      window.__speakJa = replyZh ? reply.replace(/^「|」$/g, "") : "";
      /* 中日对照：content 保留语言模型原句（含 action/emotion 未过滤），zh 存翻译模型原样输出 */
      history[history.length - 1].zh = replyZh;
      history[history.length - 1].emo = window.__lastEmo || "";   /* 重播要按当时的情绪算缓存 key */
      /* 说话人：队列条目带的是真实说话人；单角色路径用当前角色名。
         写进历史，读档/回想才不会把别人的台词标成当前角色。 */
      if (_isNarr) {
        history[history.length - 1].narr = true;     /* 旁白：回想里不写名字、不加「」 */
      } else {
        var _spkName = _speaker || _ownName || (typeof CHAR_NAME !== "undefined" ? String(CHAR_NAME) : "");
        if (_spkName) history[history.length - 1].speaker = _spkName;
      }
      addHistory("assistant", shown, reply, window.__lastEmo || "", _speaker, _isNarr);   /* 六参：旁白 */
      // 先合成语音；语音没准备好就先不出文字，等语音出来一起出
      try { if (WA2.backlog && WA2.backlog.setVoiceOnLast) WA2.backlog.setVoiceOnLast(window.__lastVoiceReq); } catch (e) {}
      const mood = (window.__lastEmo || detectMood(shown));
      /* 文字、立绘、背景在**语音开始播放**那一刻一起出（不能等播完） */
      let _rendered = false;
      const renderNow = function () {
        if (_rendered) return;
        _rendered = true;
        try {
          const _want = window.__lockOutfit ? "" : (window.__nextOutfit || "");
          /* 被「锁定场景/锁定服装」挡住时要说一声 —— 否则表现就是「场景和立绘不自动切换」，
             但界面上完全看不出原因（那两个开关在右键菜单里） */
          if (window.__nextScene && window.__lockScene && !window.__lockSceneWarned) {
            window.__lockSceneWarned = 1;
            try { (window.__toast || function () {})("场景已锁定，不会自动切换（右键菜单可解锁）", 3000); } catch (e) {}
          }
          if (window.__nextOutfit && window.__lockOutfit && !window.__lockOutfitWarned) {
            window.__lockOutfitWarned = 1;
            try { (window.__toast || function () {})("服装已锁定，不会自动切换（右键菜单可解锁）", 3000); } catch (e) {}
          }
          if (window.__nextScene && !window.__lockScene) {
            setBg(window.__nextScene);
            /* 换场景给个短提示：没看到这条就说明后端这轮没给场景标签 */
            try {
              if (window.__lastSceneToasted !== window.__nextScene) {
                window.__lastSceneToasted = window.__nextScene;
                (window.__toast || function () {})("场景：" + window.__nextScene, 1600);
              }
            } catch (e) {}
            try { console.info("[scene]", window.__nextScene); } catch (e) {}
          }
          window.__nextScene = "";
          window.__nextOutfit = "";
          setSpriteByScene((WA2.session && WA2.session.currentBg ? WA2.session.currentBg() : "") || "classroom", mood, true, _want);
          try {
            var _plate = document.getElementById("namePlate");
            if (_plate) {
              if (_speaker && typeof gdiRenderName === "function") {
                _plate.style.visibility = "";
                gdiRenderName(_speaker, _plate);
              } else if (_isNarr) {
                _plate.textContent = "";
                _plate.style.visibility = "hidden";
              }
            }
          } catch (e) {}
          showReply(shown);
        } catch (e) {}
      };
      /* 自动续写里，旁白和「你的台词」是没有语音的：
         立刻显示，然后按字数留阅读时间，时间到了才算这一条结束（自动模式据此推进）。 */
      const _lineType = (d.line_type || "her");
      const _readMs = Math.min(30000, Math.max(2000, (shown || "").length * 500));
      var _ownName = "";
      try { if (typeof CHAR_NAME !== "undefined" && CHAR_NAME) _ownName = String(CHAR_NAME); } catch (e) {}
      var _canVoice = (_lineType === "her") && (!_speaker || _speaker === _ownName);
      /* 队列里别的角色说话：他/她有自己的音色也照样念 */
      try {
        if (!_canVoice && _speaker && window.__voiceMap && window.__voiceMap[_speaker] === true) {
          _canVoice = true;
        }
      } catch (e) {}
      /* 玩家永远不出语音；说话人没有音色也不出 */
      try {
        if (window.__playerName && _speaker === window.__playerName) _canVoice = false;
        if (_speaker && window.__voiceMap && window.__voiceMap[_speaker] === false) _canVoice = false;
      } catch (e) {}
      const voiceDone = _canVoice
        ? speakReply(reply, renderNow, _speaker).then(function () {
            renderNow();      /* 没有语音 / 播失败时兜底：文字照样要出 */
          })
        : Promise.resolve().then(function () {
            renderNow();      /* 旁白 / 你的台词：先显示 */
            return new Promise(function (res) { setTimeout(res, _readMs); });  /* 再留阅读时间 */
          });
      // 立绘表情随情绪变，服装与场景只在模型明确标记时切换
      // BGM 按累积氛围切换，不随单句变。
      // 注意要喂**中文**那侧：detectMood 的规则全是中文词，喂日语原文永远判成 calm。
      // 同时把模型给的 <emotion> 一起传进去 —— BGM 优先信它
      // （毒舌是她日常嘴硬、算 calm；只有「沉重」才切冰之刃）。
      accumulateMood(replyZh || shown, window.__lastEmo || "");
      sessionSave();          // 自动存档
      turnCount++;

      /* 等这句语音真的播完，再告诉自动模式 —— 否则会抢下一句 */
      try { await voiceDone; } catch (e) {}
      try { if (WA2.auto && WA2.auto.lineDone) WA2.auto.lineDone(); } catch (e) {}
    return true;
  }

  async function send() {
    if (window.__charSwitching) return;      // 切角色过渡中：先别发，等新角色就位
    /* 新消息先打断重播：回想里正在播的那句要停，状态机也清掉 */
    if (WA2.perf) {
      WA2.perf.interrupt("REPLAYING");
      try { if (WA2.voice && WA2.voice.stopSpeak) WA2.voice.stopSpeak(); } catch (e) {}
      WA2.perf.begin("DIALOGUE_STREAMING");
    }
    const inp = $("input");
    const text = inp.value.trim();
    if (typing || !text) return;
    inp.value = "";
    if (WA2.auto && WA2.auto.isOn && WA2.auto.isOn()) WA2.auto.stop();   /* 玩家自己发话就退出自动 */
    addHistory("user", text);
    history.push({ role: "user", content: text });
    window.__history = history;      // 用户消息也要同步到全局，否则存档规则读到的是旧长度
    // 等待提示也用字模渲染，避免冒出系统字体
    if (window.gdiRenderMsg) {
      stopReveal();
      gdiRenderMsg("「……」", $("msgText"), 56, 1200, 1);
      $("msgText").classList.add("gdi");
    } else {
      $("msgText").textContent = "「……」";
    }
    hidePageCursor();

    /* 多角色开着：这句话交给导演当「剧情走向」，由队列依次演出各角色的台词。
       缓存值可能是 5 秒前的（刚打开多角色就打字），所以先快速确认一次。 */
    var _dirOn = !!(window.WA2 && WA2.play && WA2.play.isDirectorOn && WA2.play.isDirectorOn());
    if (!_dirOn) {
      try {
        var _st = await fetch("/api/direct/status", { cache: "no-store" }).then(function (r) { return r.json(); });
        var _d = (_st && _st.director) || {};
        var _others = ((_d.cast || {}).members || []).filter(function (m) { return m.role !== "player"; });
        _dirOn = !!_d.enabled && _others.length > 0;
      } catch (e) {}
    }
    if (_dirOn) {
      try {
        await fetch("/api/play/clear", { method: "POST" });
        await fetch("/api/direct/options", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ direction: text })
        });
        var _g = await fetch("/api/direct/gen", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ n: 3 })     /* 多要一拍：至少让两个在场角色都开口 */
        });
        var _gd = await _g.json().catch(function () { return null; });
        if (_gd && _gd.error && window.__toast) window.__toast("导演没能接话：" + _gd.error, 3200);
      } catch (e) {}
      if (WA2.perf) WA2.perf.end("DIALOGUE_STREAMING");
      return;                       /* 余下的交给 wt-play 的队列 */
    }

    try {
      const r = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify((function () {
      let act = {};
      try { act = JSON.parse(localStorage.getItem("app_active_model") || "{}"); } catch (e) {}
      // 不写死模型: 只有用户在设置里明确选过才传, 否则由后端用 DEFAULT_MODEL
        // 注意：整段历史都发给后端 —— 裁剪/记忆书由后端按 token 预算决定（前端不砍）
        const body = { messages: history.map(function (m) { return { role: m.role, content: m.content }; }) };

        if (act.model) body.model = act.model;
      if (act.url) body.base_url = act.url;
      // 让后端做一次剧本检索：和这句话相关的「原作里发生过的事」会单独拼进 system
      body.memory = true;
      // 当前背景一并带上：模型只说地点没说时间时，新场景沿用现在的时段（白天/晚上）
      var _bgNow = (WA2.session && WA2.session.currentBg) ? WA2.session.currentBg() : "";
      if (_bgNow) body.bg = _bgNow;
      if (WA2.state.outfit) body.outfit = WA2.state.outfit;
      try { var _pm = localStorage.getItem("app_period") || "auto"; if (_pm !== "auto") body.period = _pm; } catch (e) {}
      return body;
    })()),
      });
      const d = await r.json();
      await applyServerReply(d);
    } catch (e) {
      $("msgText").textContent = "[网络错误] " + e.message;

    } finally {
      if (WA2.perf) WA2.perf.end("DIALOGUE_STREAMING");
    }
  }

  /* 标题 BGM: 首次交互后播放 */
  let titleBgmStarted = false;

  /* 开新游戏：把「上一局留下的待办量」清掉（这些量的归属在这里，别人别直接写 window.__xxx） */
  function resetPending() {
    window.__lastEmo = "";
    window.__nextScene = "";
    window.__nextOutfit = "";
    window.__lastThinking = "";
    window.__lastAction = "";
    return true;
  }

  /* 自动续说：只请求、不渲染（预生成用） */
  /* 场面标记（当前是不是特殊场面）：读档 / 新游戏 / 每句回复都从这里改 */
  function setSceneFlag(isSpecial) {
    try {
      window.__hsceneStreak = 0;
      document.body.dataset.hscene = isSpecial ? "1" : "0";
    } catch (e) {}
  }

  async function generateContinue() {
    try {
      const r = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify((function () {
          let act = {};
          try { act = JSON.parse(localStorage.getItem("app_active_model") || "{}"); } catch (e) {}
          const b = { messages: history.map(function (m) { return { role: m.role, content: m.content }; }),
                      continue: true, memory: true,
                      last_emo: (window.__lastEmo || "") };
          if (act.model) b.model = act.model;
          if (act.url) b.base_url = act.url;
          var _bgNow2 = (WA2.session && WA2.session.currentBg) ? WA2.session.currentBg() : "";
          if (_bgNow2) b.bg = _bgNow2;
          if (WA2.state.outfit) b.outfit = WA2.state.outfit;
          try { var _pm = localStorage.getItem("app_period") || "auto"; if (_pm !== "auto") b.period = _pm; } catch (e) {}
          return b;
        })())
      });
      const d = await r.json();
      if (!d || d.error) return null;
      if (!String(d.reply || '').trim()) {
        /* 模型这一拍只写了旁白/动作（line_type=narr 且 reply 为空）：
           这是合法的，当成一条旁白播下去，不要算成失败——
           否则连续几次就把「自动续说」关掉了。 */
        const act = String(d.action_zh || d.action || '').trim();
        if (!act) return null;
        d.line_type = 'narr';
        d.reply = act;
        d.reply_zh = act;
      }
      return d;
    } catch (e) { return null; }
  }

  /* 显示一条预生成的回复（返回语音播完的 Promise） */
  function renderQueued(d) {
    if (!d) return Promise.resolve(false);
    return applyServerReply(d);
  }

  WA2.chat = { send: send, resetPending: resetPending, generateContinue: generateContinue,
              setSceneFlag: setSceneFlag,
              renderQueued: renderQueued,
              isBusy: function () { return !!typing; } };
  WA2.register("chat", WA2.chat);
  window.send = send;
  /* __export_all__ */
  ["resetPending", "WA2", "_want", "act", "actZh", "body", "d", "inp", "mood", "r", "reply", "replyZh", "shown", "text", "titleBgmStarted"].forEach(function (n) {
    try { var v = eval(n); if (v !== undefined) { window[n] = v; } } catch (e) {}
  });
})();
