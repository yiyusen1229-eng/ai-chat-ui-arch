// WHITE ALBUM 2 风格对话界面 —— 使用游戏原始素材
const $ = (id) => document.getElementById(id);

let history = [];
window.__history = history;   // 同一引用：读档只改内容、不换数组
let typing = false;
let fullText = "";
let turnCount = 0;
let backlogOpen = false;
/* 角色名：存在 char/name.txt，特别模式里改的就是它 */
let CHAR_NAME = "角色";

/* ---------- 素材表 ---------- */
/* 旧的四张通用背景：新场景图还没导入、或读档时遇到旧存档的场景名，就退回这里 */
const BGS = {
  classroom: "assets/bg_classroom.png",
  music:     "assets/bg_music.png",
  corridor:  "assets/bg_corridor.png",
  street:    "assets/bg_street.png",
};

/* 场景 CG：assets/cg/index.json 由 tools/import_cg.py 生成，id 就是图片文件名（去掉扩展名）。
   对话里换场景时后端已经按文件名标签匹配好了（scene_cg.py），这里只负责按 id 取图。 */
const CG_URL = {};
fetch("assets/cg/index.json", { cache: "no-store" })
  .then(function (r) { return r.json(); })
  .then(function (d) {
    (d && d.items || []).forEach(function (it) { CG_URL[it.id] = "assets/cg/" + it.file; });
    /* 目录比背景晚到时（刚打开页面就读档）补渲染一次，免得停在兜底教室图上 */
    if (currentBg) { var _n = currentBg; currentBg = null; setBg(_n); }
  })
  .catch(function () {});

/* 场景名 → 图片地址。认三种：CG 的 id / 旧的四张背景 / 都没有就用教室 */
function bgSrc(name) {
  var url = "";
  if (name && CG_URL[name]) url = CG_URL[name];
  else if (name && BGS[name]) url = BGS[name];
  else url = BGS.classroom;
  /* #20 热重载：素材换了之后加 ?v= 强制重新取图 */
  if (WA2.assetsVer) url += (url.indexOf("?") >= 0 ? "&" : "?") + "v=" + WA2.assetsVer;
  return url;
}


/* 音量/BGM 已搬到 wt-audio.js（WA2.audio），这里只留薄封装 */
function bgmVolume() { return WA2.audio.bgmVolume(); }
function voiceVolume() { return WA2.audio.voiceVolume(); }

/* ---------- 开场动画 ---------- */
(function intro() {
  const scr = $("introScreen");
  const vid = $("introVideo");
  if (!scr) return;
  // 没有视频元素（已改成静态开场）：直接点任意处 / 1.2 秒后进入标题
  if (!vid) {
    let done2 = false;
    function finish() {
      if (done2) return;
      done2 = true;
      scr.classList.remove("active");
      const t = document.getElementById("titleScreen");
      if (t) t.classList.add("active");
      try { setBgm("menu", 900); } catch (e) {}
    }
    window.__endIntro = finish;
    scr.addEventListener("click", finish);
    document.addEventListener("keydown", function (e) {
      if (!done2 && (e.key === "Escape" || e.key === " " || e.key === "Enter")) finish();
    });
    setTimeout(finish, 1200);
    return;
  }
  let done = false;

  function endIntro() {
    if (done) return;
    done = true;
    /* 跳过时必须把视频彻底停掉：只 pause 不够——fetch 完之后那串 play() 的异步链
       可能在你点过之后又把音轨拉起来，于是画面没了声音还在放。
       这里静音 + 断源 + load()，把解码器一起关掉。 */
    try {
      vid.pause();
      vid.muted = true;
      vid.removeAttribute("src");
      vid.load();
    } catch (e) {}
    scr.classList.remove("active");
    $("titleScreen").classList.add("active");
    if (window.App && window.App.getPrefs) { try { setBgm("menu", 900); } catch (e) {} }
  }
  window.__endIntro = endIntro;

  vid.muted = false;
  vid.volume = 0.9;

  /* 开屏按原版顺序播两段：Leaf 社 logo → 游戏 OP → 标题
     点一下 / Esc = 跳过当前这一段，跳到下一段；最后一段再跳过才进标题 */
  var SEQ = ["assets/leaf_logo.mp4", (vid.dataset.src || "assets/opening.mp4")];
  var seqIdx = 0;

  function playSeq(i) {
    if (done) return;
    if (i >= SEQ.length) { endIntro(); return; }
    seqIdx = i;
    var url = SEQ[i];
    fetch(url, { cache: "force-cache" })
      .then(function (r) { return r.blob(); })
      .then(function (b) {
        if (done || seqIdx !== i) return;
        vid.src = URL.createObjectURL(b);
        vid.load();          /* preload="none" 时必须显式 load，否则一直卡在 readyState=0 */
        /* 一段播完再切下一段：用 onended（不用全局 ended 监听，避免旧段的残留事件把序列顶掉） */
        vid.onended = function () {
          if (done || seqIdx !== i) return;
          playSeq(i + 1);
        };
        var p = vid.play();
        if (p && p.catch) p.catch(function () {
          try { vid.muted = true; vid.play().catch(function () {}); } catch (e) {}
        });
      })
      .catch(function () { if (seqIdx === i) playSeq(i + 1); });   /* 取不到就跳下一段 */
  }

  function nextOrFinish() {
    if (done) return;
    if (seqIdx + 1 < SEQ.length) {
      try { vid.pause(); } catch (e) {}
      vid.onended = null;
      playSeq(seqIdx + 1);
    } else {
      endIntro();
    }
  }

  scr.addEventListener("click", nextOrFinish);
  document.addEventListener("keydown", (e) => {
    if (!done && (e.key === "Escape" || e.key === " " || e.key === "Enter")) nextOrFinish();
  });

  // 等首屏资源（界面图/字体/音频）都加载完再拉视频，避免抢带宽
  function startVideo() {
    if (vid.dataset.started) return;
    vid.dataset.started = "1";
    playSeq(0);          /* 从 Leaf logo 开始，后面的段由 playSeq 按顺序拉 */
  }
  if (document.readyState === "complete") {
    setTimeout(startVideo, 300);
  } else {
    window.addEventListener("load", function () { setTimeout(startVideo, 300); });
  }

  // 开场视频是从本地 HTTP 服务慢慢下载的（38MB，可能要 20~30 秒），
  // 所以不能按「3 秒没播起来就跳过」来判断——那会导致没点屏幕就自动跳过。
  // 只在真正出错（解码失败 / 文件缺失）时才自动跳过；其余情况等用户点击或播完。
  vid.addEventListener("stalled", () => {}, { passive: true });
  let loadFail = 0;
  const failTimer = setInterval(() => {
    if (done) { clearInterval(failTimer); return; }
    // readyState=0 且已经等很久，才认为加载失败
    if (vid.readyState === 0 && ++loadFail >= 60) {   // 60 × 2s = 120s
      clearInterval(failTimer);
      endIntro();
    }
  }, 2000);
})();

/* ---------- 音频：实现搬到 wt-audio.js ---------- */
function playSE(kind) { return WA2.audio.playSE(kind); }
function setBgm(name, fadeMs) { return WA2.audio.setBgm(name, fadeMs); }

/* ---------- 情绪判断 ---------- */
/* detectMood / accumulateMood / currentBgmMood 已搬到 mood.js（WA2.mood） */

function loadOpeningLine() {
  return fetch("/api/character/opening", { cache: "no-store" })
    .then(function (r) { return r.json(); })
    .then(function (d) {
      if (d && d.ok && (d.zh || d.ja)) {
        OPENING_LINE = { zh: d.zh || "「……」", ja: d.ja || "", emo: d.emotion || "calm" };
      }
    })
    .catch(function () {});
}
window.__loadOpeningLine = loadOpeningLine;
loadOpeningLine();

function openingLine() { return OPENING_LINE; }

/* ---------- 会话存档 ---------- */
/* sessionSave / sessionRestore 已搬到 session.js（WA2.session） */

function setBg(name) {
  if (currentBg === name) return;
  currentBg = name;
  /* 存档要记「当前背景」，但 currentBg 是 game.js 的内部变量；
     顺手写到 body.dataset 上，session.js 直接读它（不新增全局） */
  try { document.body.dataset.bg = name || ""; } catch (e) {}
  const bgUrl = bgSrc(name);
  window.__curBg = new Image();
  window.__curBg.src = bgUrl;
  /* 背景缺图时留个纯色底 + 提示一次，别让画面空成黑的 */
  const missing = function () {
    const l = $("bgLayer");
    if (l) {
      l.style.backgroundImage = "none";
      l.style.backgroundColor = "#1b2430";
    }
    if (!window.__bgWarned) {
      window.__bgWarned = true;
      (window.__toast || function () {})("背景缺失，先用纯色底继续", 2500);
    }
  };
  /* 双缓冲交叉淡化（200~300ms，设置里可关）；wt-fade 没加载时才走老办法 */
  try {
    if (WA2.fade && WA2.fade.crossBg) { WA2.fade.crossBg(bgUrl, null, missing); return; }
  } catch (e) {}
  const l = $("bgLayer");
  if (!l) return;
  l.style.opacity = "0.25";
  setTimeout(() => {
    const probe = new Image();
    probe.onerror = missing;
    probe.src = bgUrl;
    l.style.backgroundImage = `url("${bgUrl}")`;
    l.style.opacity = "1";
  }, 260);
}

/* ---------- 立绘：实现搬到 wt-sprite.js（WA2.sprite） ---------- */
/* 热重载（#20）：素材变了之后强制重画一次当前背景（setBg 同名的会早退） */
window.__reapplyBg = function () {
  var name = currentBg;
  if (!name) return false;
  currentBg = null;
  try { setBg(name); return true; } catch (e) { return false; }
};
/* 切换/新建角色后重拉索引：带上当前场景，交给 wt-sprite 处理 */
window.__reloadSprites = function () {
  try { WA2.sprite.reload(currentBg || "classroom", window.__lastEmo || ""); } catch (e) {}
};

/* ---------- 飘雪 ---------- */
(function snow() {
  const box = $("snowLayer");
  for (let i = 0; i < 34; i++) {
    const f = document.createElement("div");
    f.className = "flake";
    const s = 1.4 + Math.random() * 3;
    f.style.width = s + "px"; f.style.height = s + "px";
    f.style.left = Math.random() * 100 + "vw";
    f.style.animationDuration = (11 + Math.random() * 16) + "s";
    f.style.animationDelay = (-Math.random() * 26) + "s";
    f.style.opacity = 0.18 + Math.random() * 0.45;
    box.appendChild(f);
  }
})();

function showScreen(id) {
  document.querySelectorAll(".screen").forEach(s => s.classList.remove("active"));
  $(id).classList.add("active");
}

/* ---------- GDI 字体渲染(复刻游戏原生方案) ---------- */
const NAME_BOOST = 1.0;   // 人名比正文大一点点(按像素比对)
/* gdiRenderName / gdiRenderMsg / __gdiFallback 已搬到 gdi.js（WA2.gdi） */

function emoOf(text) {
  const m = detectMood(text || "");
  if (m === "angry") return "angry";
  if (m === "sad") return "sad";
  if (m === "warm" || m === "smile") return "warm";
  return "neutral";
}

/* currentVoiceLang / stopSpeak / fetchVoice 已搬到 voice.js（WA2.voice） */

/* splitPages / renderPage / showReply 已搬到 text.js（WA2.text） */

/* addHistory / rebuildBacklog / toggleBacklog 已搬到 backlog.js（WA2.backlog） */

/* showChoices 已搬到 choices.js（WA2.choices） */

/* send 已搬到 chat.js（WA2.chat） */

/* playTitleBgm / quitGame / backToTitle 已搬到 nav.js（WA2.nav） */

function syncHistThumb() {
  const list = $("historyList"), thumb = $("hsThumb"), track = $("hsTrack");
  if (!list || !thumb || !track) return;
  const trackH = track.clientHeight || 1;
  // 滑块长度固定，按轨道高度算，不随内容变化
  const fixedH = Math.max(18, Math.round(trackH * HIST_THUMB_RATIO));
  thumb.style.height = fixedH + "px";
  const thumbH = fixedH;
  histTopGap = 3; histBottomGap = 5;   // 底部留白（游戏实测 16px@2730 ≈ 4.7px@450）
  histMaxTop = Math.max(1, trackH - thumbH - histBottomGap - histTopGap);
  const scrollable = Math.max(0, list.scrollHeight - list.clientHeight);
  // 内容不足一屏时停在顶部
  // 打开即最新: 内容不足一屏时滑块也停在底部
  const ratio = scrollable > 2 ? Math.min(1, Math.max(0, list.scrollTop / scrollable)) : 1;
  thumb.style.top = Math.round(histTopGap + histMaxTop * ratio) + "px";
  /* 内容不足一屏时不允许拖动 */
  if (scrollable > 2) delete thumb.dataset.nodrag; else thumb.dataset.nodrag = "1";
  thumb.style.display = "block";
}

(function initHistory() {
  const list = $("historyList");
  if (list) list.addEventListener("scroll", syncHistThumb);

  // 上下箭头
  document.querySelectorAll("#historyScroll .hsArrow").forEach((btn) => {
    btn.addEventListener("click", () => {
      const dir = parseInt(btn.dataset.dir || "1", 10);
      if (list) list.scrollTop += dir * (list.clientHeight * 0.85);
      playSE("click");
    });
  });

  // 拖动滑块
  const thumb = $("hsThumb"), track = $("hsTrack");
  if (thumb && track) {
    let dragging = false, startY = 0, startTop = 0;
    thumb.addEventListener("mousedown", (e) => {
      if (thumb.dataset.nodrag === "1") return;      /* 内容不足一屏：不给拖 */
      dragging = true; startY = e.clientY;
      startTop = Math.max(0, parseInt(thumb.style.top || "0", 10) - histTopGap);
      e.preventDefault();
    });
    window.addEventListener("mousemove", (e) => {
      if (!dragging || !list) return;
      const maxTop = histMaxTop;
      // 滑块位移 -> 文本滚动量：同样的位移，内容越多滚得越多
      const scrollable = Math.max(0, list.scrollHeight - list.clientHeight);
      let top = Math.min(maxTop, Math.max(0, startTop + (e.clientY - startY)));
      thumb.style.top = (histTopGap + top) + "px";
      list.scrollTop = (top / maxTop) * scrollable;
    });
    window.addEventListener("mouseup", () => { dragging = false; });
  }

  // 轨道点击翻页
  if (track) {
    track.addEventListener("click", (e) => {
      if (e.target.closest("#hsThumb") || !list) return;
      const r = track.getBoundingClientRect();
      list.scrollTop += (e.clientY < r.top + r.height / 2 ? -1 : 1) * list.clientHeight * 0.85;
    });
  }

  // EXIT 返回
  const ex = $("historyExit");
  if (ex) ex.addEventListener("click", () => toggleBacklog(false));

  window.addEventListener("resize", syncHistThumb);
})();

/* 启动时问一次语音模型是否可用：可用 → 和纱的回想图标是白气泡（可重播），否则用灰气泡 */
(function probeVoice() {
  fetch("/api/voice/ok", { cache: "no-store" })
    .then(function (r) { return r.json(); })
    .then(function (d) {
      window.__voiceOk = !!(d && d.ok);
      try { rebuildBacklog(); } catch (e) {}
    })
    .catch(function () { window.__voiceOk = false; });
})();
/* 内容淡入淡出：换页/换标签时统一走这里，时长和实机一致（约 0.2s 出 + 0.2s 入） */

/* 回想（历史对话）面板滚轮翻动时播翻页音 */
(function () {
  function attach() {
    var box = document.getElementById("historyBox") || document.getElementById("blogList") ||
              document.getElementById("histBox") || document.querySelector(".historyBox");
    if (!box || box.dataset.seWheel) return;
    box.dataset.seWheel = "1";
    var acc = 0;
    box.addEventListener("wheel", function (e) {
      acc += Math.abs(e.deltaY);
      if (acc < 60) return;          /* 累积一点再响，避免一滑响一串 */
      acc = 0;
      try { if (window.playSE) window.playSE("page"); } catch (err) {}
    }, { passive: true });
  }
  document.addEventListener("DOMContentLoaded", attach);
  window.addEventListener("load", attach);
  setInterval(attach, 1500);
})();

/* 回想：已经翻到最底部时继续往下滚 = 直接退出回想（原版手感） */
(function () {
  var panel = document.getElementById("historyPanel");
  if (!panel) return;
  panel.addEventListener("wheel", function (e) {
    if (!backlogOpen || e.deltaY <= 0) return;
    var list = document.getElementById("historyList");
    if (!list) return;
    /* 还差 1px 以内都算到底（子像素滚动会差一点点） */
    if (list.scrollTop + list.clientHeight < list.scrollHeight - 1) return;
    toggleBacklog(false);
    e.preventDefault();
  }, { passive: false });
})();

/* ---------- 音量总控：BGM / 语音 / 音效都乘上主音量 ----------
   注意这里必须用模块内的 bgmAudio / ttsAudio：
   之前写的是 window.__bgmAudio / window.__voiceAudio，那两个变量从来没被赋值过，
   所以「全体音量」「BGM」「VOICE」三个滑块都没有实际效果。 */
window.__applyVolumes = function () {
  var bv = 0, vv = 0;
  try { bv = bgmVolume(); } catch (e) {}
  try { vv = voiceVolume(); } catch (e) {}
  try { WA2.audio.applyVolume(); } catch (e) {}
  try { var _va = WA2.voice._tts(); if (_va) _va.volume = vv; } catch (e) {}
  window.__volBgm = bv;
  window.__volVoice = vv;
};
document.addEventListener("DOMContentLoaded", function () {
  try { window.__applyVolumes(); } catch (e) {}
});

/* ---------- 界面显示（OPTION 1 的「窗口」那几项） ----------
   窗口透明度（普通场景）：只淡 UI 底图（对话框发光框 / 输入栏底框 / 按钮底板），
     字模（正文、名字、占位符、按钮标签）保持原样 —— 通过 CSS 变量 --ui-alpha 控制；
   窗口 ON / OFF：整块显示/隐藏（连字一起），这是有意的。 */
window.__applyUiPrefs = function () {
  var P = (window.App && window.App.getPrefs) ? window.App.getPrefs() : {};
  var on = (P.winVisible === undefined) ? true : !!P.winVisible;
  var a = (P.winAlpha === undefined) ? 100 : P.winAlpha;
  a = Math.max(0, Math.min(100, a)) / 100;
  try { document.documentElement.style.setProperty("--ui-alpha", String(a)); } catch (e) {}
  ["msgWrap", "inputBar"].forEach(function (id) {
    var el = document.getElementById(id);
    if (!el) return;
    if (on) {
      el.style.removeProperty("opacity");
      el.style.removeProperty("pointer-events");
    } else {
      el.style.setProperty("opacity", "0", "important");
      el.style.setProperty("pointer-events", "none", "important");
    }
  });
  document.body.classList.toggle("dlgHidden", !on);
};
window.addEventListener("app:prefs", function () {
  try { window.__applyUiPrefs(); } catch (e) {}
});
document.addEventListener("DOMContentLoaded", function () {
  try { window.__applyUiPrefs(); } catch (e) {}
});

/* 窗口 / 全屏：和标题栏那条路一样走后端 pywebview 开关（不是浏览器 Fullscreen API） */
window.__setWinMode = function (mode) {
  var on = (mode === "full") ? "1" : "0";
  try {
    fetch("/api/window?action=fullscreen&on=" + on, { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        try { if (window.__dbg) window.__dbg("WINMODE", mode + " " + JSON.stringify(d).slice(0, 36)); } catch (e) {}
        /* 后端可能退回最大化，界面高亮要跟真实状态走 */
        if (d && d.ok && typeof d.fullscreen === "boolean" && window.__ct1SetWinModeUI) {
          window.__ct1SetWinModeUI(d.fullscreen);
        }
      })
      .catch(function () {});
  } catch (e) {}
};

/* 一组元素做淡出→改动→淡入（切标签页/切存档页时用，和实机一致约 0.2s） */
/* 换页动画（按原版观感实现）：
   旧页：向内收缩 + 虚化 + 变透明
   新页：从略大开始，向内收缩同时由透明迅速实化
   总时长默认 390ms（与翻页音 SE_9213 等长） */

/* ---------- 滑鼠右键功能（OPTION 2 设置项） ----------
   三种模式：hide = 隐藏所有界面只留 CG/立绘；options = 打开设置；save = 打开存档
   只在「对话界面」生效：浮层（设置 / 存读档 / 回想 / 特别模式）开着时，右键 = 关掉当前这一层，
   不再顺带触发下面的隐藏窗口，否则两个界面会同时响应同一个右键。 */
document.addEventListener("contextmenu", function (e) {
  // 输入框上仍然保留浏览器默认（方便复制粘贴）
  if (e.target && (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA")) return;
  e.preventDefault();
  /* 隐藏状态下右键 = 先把界面叫回来（不管右键功能设的是哪一项） */
  if (document.body.classList.contains("uiHidden")) {
    document.body.classList.remove("uiHidden");
    return;
  }

  /* 浮层开着：右键只负责退出这一层（等同右下角 EXIT） */
  if (window.__closeTopScreen && window.__closeTopScreen()) {
    if (window.__dbg) window.__dbg("RMB", "close-screen");
    return;
  }
  /* 回想面板不是 .uiScreen，单独收一下 */
  if (backlogOpen) {
    toggleBacklog(false);
    return;
  }

  var mode = "save";
  try {
    var P = (window.App && window.App.getPrefs) ? window.App.getPrefs() : {};
    if (P.rightClick) mode = P.rightClick;
  } catch (err) {}

  if (window.__dbg) window.__dbg("RMB", mode);

  if (mode === "hide") {
    document.body.classList.toggle("uiHidden");
    return;
  }
  if (mode === "options") {
    try { if (window.__openOptions) window.__openOptions(); } catch (err) {}
    return;
  }
  try { if (window.__openScreen) window.__openScreen("save", "save"); } catch (err) {}
});

/* ---------- 滚轮上滑：对话界面直接打开回想 ----------
   只在对话界面响应（和右键一样）：标题画面、隐藏窗口、任意浮层（设置 / 存读档 / 回想 /
   特别模式）开着的时候都不抢；回想已经开着时滚轮交给面板自己翻。 */
document.addEventListener("wheel", function (e) {
  if (!(e.deltaY < 0)) return;                                      /* 只认上滑 */
  if (backlogOpen) return;
  if (document.body.classList.contains("uiHidden")) return;
  /* 调试面板里滚日志不该弹回想 */
  if (e.target && e.target.closest && e.target.closest("#dbgPanel")) return;
  var gs = $("gameScreen");
  if (!gs || !gs.classList.contains("active")) return;              /* 只在对话界面 */
  if (window.__isScreenOpen && window.__isScreenOpen()) return;     /* 浮层开着不抢 */
  toggleBacklog(true);
  e.preventDefault();
}, { passive: false });

/* ---------- 右下角：本条 / 累计 token 与花费 ----------
   文案与 0~9 都是烤好的字模（tools/bake_cost_ui.py），这里只负责拼图，
   不再请求 /api/text —— 那个渲染器是中文网格，拉丁字母会被撑开。 */
var COST_EL = {};
/* renderCost / costNum / moneyStr 已搬到 cost.js（WA2.cost） */

function applyStartupWinMode() {
  try {
    var P = (window.App && window.App.getPrefs) ? window.App.getPrefs() : {};
    if ((P.winMode || "full") !== "full") return;
    if (window.__setWinMode) window.__setWinMode("full");
  } catch (e) {}
}
document.addEventListener("DOMContentLoaded", function () {
  setTimeout(applyStartupWinMode, 900);
});
window.addEventListener("app:ready", function () {
  setTimeout(applyStartupWinMode, 300);
});
