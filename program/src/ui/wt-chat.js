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
    /* 角色 id：立绘和音色都按它找（speaker 只负责显示名字）。
       老存档 / 单角色路径没有这个字段，下面一律回落到名字。 */
    var _cid = (d.cid || "").trim();
    /* 模型常把旁白整句写成「（…）」：这种也按旁白处理 */
    if (!_isNarr && /^[（(【]/.test((d.reply_zh || d.reply || "").trim())) {
      _isNarr = true;
    }
    if (_isNarr) {
      _speaker = "";
      _cid = "";
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
      /* 这句里到底有没有"台词"：单角色路径会把她说的话包成「…」，
         纯旁白/动作描写没有引号。用来区分"该配音"和"不该配音"。 */
      var _hasSpeech = /[「『]/.test(_lineText || "");
      window.__speakJa = replyZh ? reply.replace(/^「|」$/g, "") : "";
      if (d && d.ja) window.__speakJa = String(d.ja);   /* 角色自己写的日语原文优先 */
      /* 旁白 / 纯动作描写不配音：以前也会走 TTS，把"（她在路灯下停了一瞬…）"
         这种长句翻成日语念出来，听上去就是一长串怪声（日志里出现过 900KB 的音频） */
      if (_isNarr && !_hasSpeech) window.__speakJa = "";
      /* 存这一句「真正的日语原文」：队列路径的 reply 是**中文字幕**，
         以前把它当日语原文存进历史，回想重播时拿中文去喂日语模型 —— 这就是
         「实时正常、回想里胡言乱语」的原因。旁白 / 玩家没有日语，存空。 */
      var _jaForHist = _isNarr ? "" : String(window.__speakJa || "").trim();
      /* 中日对照：content 保留语言模型原句（含 action/emotion 未过滤），zh 存翻译模型原样输出 */
      history[history.length - 1].zh = replyZh;
      history[history.length - 1].emo = window.__lastEmo || "";   /* 重播要按当时的情绪算缓存 key */
      if (_jaForHist) history[history.length - 1].ja = _jaForHist;
      /* 这一句当时是不是成人场面：回想重放要用同一套参考音频，
         否则会出现「实时平常语气、回想才是 H 语气」 */
      var _hsLine = !!(d && d.hscene);
      if (_hsLine) history[history.length - 1].hscene = true;
      /* 说话人：队列条目带的是真实说话人；单角色路径用当前角色名。
         写进历史，读档/回想才不会把别人的台词标成当前角色。 */
      if (_isNarr) {
        history[history.length - 1].narr = true;     /* 旁白：回想里不写名字、不加「」 */
      } else {
        var _spkName = _speaker || _ownName || (typeof CHAR_NAME !== "undefined" ? String(CHAR_NAME) : "");
        if (_spkName) history[history.length - 1].speaker = _spkName;
        if (_cid) history[history.length - 1].cid = _cid;   /* 换角色 / 改名后重播还找得对人 */
      }
      addHistory("assistant", shown, reply, window.__lastEmo || "", _speaker, _isNarr, _cid,
                 _jaForHist, _hsLine);
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
          /* 多角色：立绘跟着说话人走（没有立绘的角色保持原样）。
             换角色要等服装表拉到再画，否则画的时候用的还是上一位的表。 */
          var _spriteFor = function (cb) {
            try {
              if (WA2.sprite && WA2.sprite.useCharacter) {
                var _ownNow = (typeof CHAR_NAME !== "undefined" && CHAR_NAME) ? String(CHAR_NAME) : "";
                var _ownId = (window.__activeCharId || "");
                /* 优先按 id 找立绘；没有 id（玩家 / 龙套 / 老数据）才退回名字 */
                var _who = _cid || _speaker;
                if (_cid && _ownId && _cid === _ownId) _who = "";
                else if (!_cid && _speaker && _speaker === _ownNow) _who = "";
                WA2.sprite.useCharacter(_who, cb || function () {});
                return;
              }
            } catch (e) {}
            if (cb) cb();
          };
          var _drawSprite = function () {
            setSpriteByScene((WA2.session && WA2.session.currentBg ? WA2.session.currentBg() : "") || "classroom", mood, true, _want);
          };
          _spriteFor(_drawSprite);
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
      var _ownId = String(window.__activeCharId || "");
      /* 找音色也是 id 优先：名字只用来显示 */
      var _who = _cid || _speaker;
      /* 旁白 / 动作描写一律不配音：它们没有"台词"，念出来就是长串怪声。
         以前只按 line_type 判断，而单角色路径的 line_type 恒为 her，
         于是「（她在路灯下停了一瞬…）」这种也被合成成日语念出来。
         判据：带引号的 = 台词（要念）；不带引号的旁白 = 不念。
         动作+台词混在一句里（"（她别过脸）「…」"）仍然要念。 */
      var _canVoice = (_lineType === "her") && (_hasSpeech || !_isNarr) &&
                      (!_who || _who === _ownName || (_cid && _ownId && _cid === _ownId));
      /* 队列里别的角色说话：他/她有自己的音色也照样念 */
      try {
        if (!_canVoice && _who && window.__voiceMap && window.__voiceMap[_who] === true) {
          _canVoice = true;
        }
      } catch (e) {}
      /* 玩家永远不出语音；说话人没有音色也不出 */
      try {
        if (window.__playerName && _speaker === window.__playerName) _canVoice = false;
        if (_who && window.__voiceMap && window.__voiceMap[_who] === false) _canVoice = false;
      } catch (e) {}
      const voiceDone = _canVoice
        ? speakReply(reply, renderNow, _who).then(function () {
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
        /* 清队列（丢掉上一轮没播完的预生成），但**不清场景原话**：
           以前这里连带把「刚才发生的事」清了，于是每一轮导演和角色都从零开始
           —— 表现就是「每轮对话接不上」。 */
        await fetch("/api/play/clear", { method: "POST" });
        /* 和单角色 /api/chat 一样：把整段 history 带上去。
           导演和角色因此看到的就是「从存档读到现在的全部」，
           不用再单独记玩家这句话、也不用读档后另做铺回。
           对话框里打的是玩家的台词；剧情走向在标题栏「多角色」菜单里单独填。 */
        var _g = await fetch("/api/direct/gen", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ n: 3, messages: (history || []).slice(-200) })
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
      /* 上一句的情绪：后端拿它写「情绪连续」提示、并给情绪迟滞播种
         （读档 / 重启后第一句不用从 calm 从零开始）。优先看历史里最后一行的 emo。 */
      body.last_emo = (function () {
        try {
          for (var i = history.length - 1; i >= 0; i--) {
            if (history[i] && history[i].role === "assistant" && history[i].emo) {
              return history[i].emo;
            }
          }
        } catch (e) {}
        return window.__lastEmo || "";
      })();
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
