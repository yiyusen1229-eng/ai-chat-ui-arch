// 系统设置界面（重建中：目前只有顶栏和底栏）

/* CT / CT2（OPTION 1 / OPTION 2 的控件表）必须在 IIFE 之外可见：
   文件末尾的 __ct1State / __ct2State / applyCt* / 启动还原都读它们，
   放在 IIFE 里就是 ReferenceError（上一轮 OPTION 2 的坑）。 */
var CT, CT2;

(function () {
  function byId(id) { return document.getElementById(id); }

  // 单个模块出错不影响整个界面打开
  function safe(fn) {
    try { fn(); } catch (err) {
      if (window.console && console.error) console.error("[opt]", err);
    }
  }

  function open() {
    /* 统一入口：__showUIScreen 会先关掉其它浮层（特别模式/模型管理/存读档），
       再显示自己 —— 别在这里各自写 classList.add("show")。 */
    var s = window.__showUIScreen ? window.__showUIScreen("optScreen") : byId("optScreen");
    if (!s) return;
    safe(bindTabs);
    safe(bindPager);
    safe(bindContent);
    safe(bindContent2);
    safe(bindModelFields);
    safe(bindExit);
    safe(refreshTabs);
  /* 淡入走 screens.js 的统一实现（同一个效果不要两处各写一遍） */
  if (window.__fadeInScreen) window.__fadeInScreen(s);
    window.__optOpen = true;
    // 界面显示后让统一文字渲染器补扫一遍（隐藏时它不会处理）
    if (window.__uiTextScan) {
      window.__uiTextScan(s);
    }
  }

  function close() {
    var s = byId("optScreen");
    if (!s) return;
    s.classList.remove("show");
    window.__optOpen = false;
  }


  // ---------- 三个 OPTION 标签：悬停 / 选中 ----------
  var current = 1;

  function setTab(n, state) {
    var img = document.querySelector("#optT" + n + " img");
    if (!img) return;
    img.src = "assets/ui/opt/tab" + n + "_" + state + ".png";
  }

  function refreshTabs() {
    for (var i = 1; i <= 3; i++) setTab(i, i === current ? "on" : "off");
    // 内容区目前只做了 OPTION 1，其余两页留空
    var c1 = document.getElementById("optContent");
    var c2 = document.getElementById("optContent2");
    var c3 = document.getElementById("optContent3");
    if (c1) c1.style.display = (current === 1) ? "block" : "none";
    if (c2) c2.style.display = (current === 2) ? "block" : "none";
    if (c3) c3.style.display = (current === 3) ? "block" : "none";
    if (current === 3 && window.__uiTextScan && c3) window.__uiTextScan(c3);
  }

  var tabsBound = false;
  function bindTabs() {
    if (tabsBound) return;
    tabsBound = true;
    for (var i = 1; i <= 3; i++) {
      (function (n) {
        var el = byId("optT" + n);
        if (!el) return;
        el.addEventListener("mouseenter", function () {
          if (n !== current) setTab(n, "hover");
        });
        el.addEventListener("mouseleave", function () {
          if (n !== current) setTab(n, "off");
        });
        el.addEventListener("click", function (e) {
          e.stopPropagation();
          if (n === current) return;
          var cur = document.getElementById("optContent" + (current === 1 ? "" : current));
          var nxt = document.getElementById("optContent" + (n === 1 ? "" : n));
          var run = function () { current = n; refreshTabs(); };
          if (nxt) nxt.style.display = "block";
          run();
        });
      })(i);
    }
  }

  window.__optTab = function (n) { current = n; refreshTabs(); };


  var pagerBound = false;
  function bindPager() {
    if (pagerBound) return;
    pagerBound = true;
    [["optPrev", "l"], ["optNext", "r"]].forEach(function (pair) {
      var el = byId(pair[0]);
      if (!el) return;
      var img = el.querySelector("img");
      if (!img) return;
      var base = "assets/ui/opt/pager_" + pair[1] + "1.png";
      var hover = "assets/ui/opt/pager_" + pair[1] + "2.png";
      img.src = base;
      el.addEventListener("mouseenter", function () { img.src = hover; });
      el.addEventListener("mouseleave", function () { img.src = base; });
      el.addEventListener("click", function (e) {
        e.stopPropagation();
        if (typeof window.__optTab !== "function") return;
        var n = current + (pair[1] === "l" ? -1 : 1);   /* 标签编号是 1~3 */
        if (n < 1) n = 3;                               /* 翻到头就绕回去 */
        if (n > 3) n = 1;
        window.__optTab(n);
      });
    });
  }

  // ---------- OPTION 1 内容区：按控件贴图 ----------
  CT = {
    W: 1024, H: 464,
    W_STAGE: 645.27, H_STAGE: 292.45,
    /* 控件分组：
         1 = 窗口/全屏（0 窗口模式 / 1 全屏模式）
         2 = 文本显示速度（2 瞬间 / 3 高速 / 4 普通 / 5 顺滑）
         4 = 画面特效（10/11，先不接功能）
         0 = 左右箭头（12/13 是「窗口透明度（普通场景）」那一行的）
         5 = 窗口 ON / OFF（14 开 / 15 关）
       注意：控件图是按**索引**命名的（assets/ui/opt/ct/wNN_sel.png），
       所以删行不能重新编号，只能把位置留空（null），否则图会张冠李戴。
       6/7 = 文本快进模式、8/9 = 自动模式速度 —— 按用户要求删除；底图也裁掉了，
       原「视觉设置」整段上移 74px，所以下面这些 y 都已经减过 74。 */
    widgets: [
      [458, 14, 252, 36, 1], [714, 14, 252, 36, 1],
      [458, 78, 124, 36, 2], [586, 78, 124, 36, 2],
      [714, 78, 124, 36, 2], [842, 78, 124, 36, 2],
      null, null, null, null,
      [458, 148, 252, 36, 4], [714, 148, 252, 36, 4],
      [482, 188, 52, 36, 0], [890, 188, 52, 36, 0],
      [388, 198, 80, 26, 5], [388, 228, 80, 26, 5],
      [482, 228, 52, 36, 0], [890, 228, 52, 36, 0],
      [482, 268, 52, 36, 0], [890, 268, 52, 36, 0],
      [482, 308, 52, 36, 0], [890, 308, 52, 36, 0]
    ],
    sliders: [
      { x: 543, y: 188, n: 21, pitch: 16, h: 26 },
      { x: 543, y: 228, n: 21, pitch: 16, h: 26 },
      { x: 543, y: 268, n: 21, pitch: 16, h: 26 },
      { x: 543, y: 308, n: 21, pitch: 16, h: 26 }
    ],
    sel: { 0: 1, 4: 1, 11: 1, 14: 1 },
    slideVal: [21, 0, 0, 0]
  };


/* bindContent / paint / makeBar 已搬到 optWidgets.js（WA2.optWidgets） */

/* txtUrl / modelApi / bindModelFields 已搬到 optModel.js（WA2.optModel） */

  function bindExit() {
    if (exitBound) return;
    exitBound = true;
    /* EXIT: shared .uiExit (sys_09100 3 states, CSS-driven) */
    var el = document.querySelector("#optScreen .uiExit");
    if (!el) return;
    el.addEventListener("click", function (e) {
      e.stopPropagation();
      close();
    });
  }
  window.__openOptions = function () { open(); return false; };
  window.__closeOptions = close;

  document.addEventListener("DOMContentLoaded", function () {
    var s = byId("optScreen");
    if (!s) return;
    // 点空白处关闭（后续加内容时改成只关弹层）
    s.addEventListener("click", function (e) {
      if (e.target === s) close();
    });
  });

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && window.__optOpen) close();
  });

  /* opt-widgets / opt-model 是从本文件拆出去的，它们要用这里的私有函数和变量 */
  window.__optExported = true;
  ["applyCt1", "applyCt2", "bindExit", "bindPager", "bindTabs", "byId", "close", "ct1Pct", "ct2Pct", "open", "refreshTabs", "restoreCt1", "restoreCt2", "safe", "setTab", "P", "a", "base", "c1", "c2", "c3", "cur", "current", "el", "hover", "img", "n", "nxt", "on", "pagerBound", "run", "s", "sp", "speed", "src", "st", "tabsBound"].forEach(function (n) {
    try { var v = eval(n); if (v !== undefined) window[n] = v; } catch (e) {}
  });
})();

/* ---------- OPTION 2 的取值真正生效 ----------
   sliders: 0=全体 1=BGM 2=SE 3=VOICE（1~21 格 → 0~100%）
   widgets 分组：1=是/否确认 2=右键功能（3 选 1）
   索引以 assets/ui/opt/ct2/widgets.js.txt 的原始顺序为准：
     0=确认 1=不确认 | 2=隐藏窗口 3=设置菜单 4=存档
*/
function ct2Pct(i) {
  var n = CT2.slideVal[i] || 1;
  return Math.round((n - 1) / 20 * 100);
}
window.__ct2State = function () {
  return {
    confirm: !!CT2.sel[0],
    rightClick: CT2.sel[2] ? "hide" : (CT2.sel[3] ? "options" : "save"),
    volMaster: ct2Pct(0),
    volBgm: ct2Pct(1),
    volSe: ct2Pct(2),
    volVoice: ct2Pct(3)
  };
};
function applyCt2() {
  var st = window.__ct2State();
  try {
    if (window.App && window.App.setPref) {
      window.App.setPref("confirmAsk", st.confirm);
      window.App.setPref("rightClick", st.rightClick);
      window.App.setPref("volMaster", st.volMaster);
      window.App.setPref("volBgm", st.volBgm);
      window.App.setPref("seVol", st.volSe);
      window.App.setPref("volVoice", st.volVoice);
    }
  } catch (e) {}
  try { if (window.__applyVolumes) window.__applyVolumes(); } catch (e) {}
}
window.__applyCt2 = applyCt2;

/* 启动时按已存的 prefs 还原滑块与选项。
   注意 window.App 要等 config.js 的 catalog.json 加载完才存在，
   所以 DOMContentLoaded 和 app:ready 各跑一次（重复跑是幂等的）。 */
function restoreCt2() {
  try {
    var P = (window.App && window.App.getPrefs) ? window.App.getPrefs() : {};
    /* 旧版只有 bgmVol / seVol，没有 volMaster / volVoice：第一次跑要能接上旧值，
       否则用户之前的音量设置会被这里的默认值顶掉 */
    var src = [
      (typeof P.volMaster === "number" ? P.volMaster : 100),
      (typeof P.volBgm === "number" ? P.volBgm : (typeof P.bgmVol === "number" ? P.bgmVol : 30)),
      (typeof P.seVol === "number" ? P.seVol : 35),
      (typeof P.volVoice === "number" ? P.volVoice : 90)
    ];
    src.forEach(function (v, i) {
      CT2.slideVal[i] = Math.max(1, Math.min(21, Math.round(v / 100 * 20) + 1));
    });
    /* 默认「确认」：savepages 只有在 confirmAsk === false 时才跳过二次确认，
       这里如果默认成「不确认」，界面和实际行为会对不上 */
    if (P.confirmAsk === false) { delete CT2.sel[0]; CT2.sel[1] = 1; }
    else { delete CT2.sel[1]; CT2.sel[0] = 1; }
    /* 右键功能三选一：2=隐藏窗口 3=设置菜单 4=存档（默认存档） */
    [2, 3, 4].forEach(function (i) { delete CT2.sel[i]; });
    if (P.rightClick === "hide") CT2.sel[2] = 1;
    else if (P.rightClick === "options") CT2.sel[3] = 1;
    else CT2.sel[4] = 1;
    /* 界面已经建好时立刻重画一次（否则要等下次打开设置才看得到） */
    if (window.__ct2Bound && window.__ct2RepaintAll) window.__ct2RepaintAll();
    /* 无论界面建没建好都存一遍：否则第一次进游戏时 prefs 里没有这些键，
       savepages 拿不到 confirmAsk，界面显示和实际行为会对不上 */
    if (window.App && window.App.setPref) window.__applyCt2();
  } catch (e) {}
}
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", restoreCt2);
} else {
  restoreCt2();
}
window.addEventListener("app:ready", restoreCt2);

/* ---------- OPTION 1 的取值真正生效 ----------
   控件分组见 CT 上方注释（索引沿用原图，删掉的行是 null）：
     0=窗口模式 1=全屏模式 ｜ 2~5=文字速度（瞬间/高速/普通/顺滑）｜ 10/11=画面特效（未接功能）
     12/13=窗口透明度（普通场景）左右箭头 ｜ 14=窗口ON 15=窗口OFF（互斥）
   其余三条滑块（CG 场景 / 小说背景 / 指令图标）按用户要求先不接功能。
*/
function ct1Pct(i) {
  var n = CT.slideVal[i] || 1;
  return Math.round((n - 1) / 20 * 100);
}
window.__ct1State = function () {
  var speed = CT.sel[2] ? 0 : CT.sel[3] ? 1 : CT.sel[4] ? 2 : CT.sel[5] ? 3 : 2;
  return {
    winMode: CT.sel[1] ? "full" : "window",
    textSpeed: speed,
    dialogOn: !!CT.sel[14],
    winAlpha: ct1Pct(0)
  };
};
function applyCt1() {
  var st = window.__ct1State();
  try {
    if (window.App && window.App.setPref) {
      window.App.setPref("textSpeed", st.textSpeed);
      window.App.setPref("winVisible", st.dialogOn);
      window.App.setPref("winAlpha", st.winAlpha);
      window.App.setPref("winMode", st.winMode);
    }
  } catch (e) {}
  try { if (window.__applyUiPrefs) window.__applyUiPrefs(); } catch (e) {}
}
window.__applyCt1 = applyCt1;

/* 外部同步「窗口模式 / 全屏模式」的高亮：
   标题栏菜单的全屏开关、以及后端全屏失败退回最大化时都会调到这里。 */
window.__ct1SetWinModeUI = function (full) {
  [0, 1].forEach(function (i) { delete CT.sel[i]; });
  CT.sel[full ? 1 : 0] = 1;
  if (window.__ctBound && window.__ct1RepaintAll) window.__ct1RepaintAll();
  try { if (window.App && window.App.setPref) window.App.setPref("winMode", full ? "full" : "window"); } catch (e) {}
};

/* 启动时按已存的 prefs 还原 OPTION 1（同样要等 App 就绪，所以跑两次） */
function restoreCt1() {
  try {
    var P = (window.App && window.App.getPrefs) ? window.App.getPrefs() : {};
    var sp = (typeof P.textSpeed === "number") ? Math.max(0, Math.min(3, P.textSpeed | 0)) : 2;
    [2, 3, 4, 5].forEach(function (i) { delete CT.sel[i]; });
    CT.sel[2 + sp] = 1;
    [0, 1].forEach(function (i) { delete CT.sel[i]; });
    CT.sel[P.winMode === "full" ? 1 : 0] = 1;
    [14, 15].forEach(function (i) { delete CT.sel[i]; });
    var on = (P.winVisible === undefined) ? true : !!P.winVisible;
    CT.sel[on ? 14 : 15] = 1;
    var a = (typeof P.winAlpha === "number") ? P.winAlpha : 100;
    CT.slideVal[0] = Math.max(1, Math.min(21, Math.round(a / 100 * 20) + 1));
    if (window.__ctBound && window.__ct1RepaintAll) window.__ct1RepaintAll();
    if (window.App && window.App.setPref) window.__applyCt1();
  } catch (e) {}
}
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", restoreCt1);
} else {
  restoreCt1();
}
window.addEventListener("app:ready", restoreCt1);
