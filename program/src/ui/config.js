// 音效/BGM 设置面板
(function () {
  const SND = "assets/snd/";
  let catalog = { se: [], bgm: [] };
  let prefs = {
    seHover: 55, seClick: 56, seCancel: 56, sePage: 57, seExit: 58,   // 55=悬停音 56=点击音（录屏比对得出）
    bgmMenu: 0, bgmCalm: 2, bgmWarm: 3, bgmSad: 4, bgmTense: 6,
    bgmKazusa: 1, bgmToukanai: 5, bgmAnswer: 7,   /* 一直在曲库里、之前没接进来的三首 */
    bgmVol: 30, seVol: 35,
    choiceCount: 2,          /* 选择分支：一次给几个选项（2~5） */
  };

  try {
    const s = localStorage.getItem("app_prefs");
    if (s) prefs = Object.assign(prefs, JSON.parse(s));
  } catch (e) {}

  /* 几个已知键的取值范围（2026-11-06 事故：整窗透明度曾抢用 winAlpha，
   把 0~255 的 255 写进这个 0~100 的键，于是「UI 底图透明度」被人为拉到上限） */
var PREF_RANGE = {
  winAlpha: [0, 100],      /* UI 底图透明度（--ui-alpha），100 = 完全不透明 */
  bgmVol: [0, 100],
  seVol: [0, 100],
  choiceCount: [2, 5],
  textSpeed: [0, 3]
};

function clampPref(k, v) {
  var r = PREF_RANGE[k];
  if (!r || typeof v !== "number" || !isFinite(v)) return v;
  return Math.max(r[0], Math.min(r[1], Math.round(v)));
}

function setPref(k, v) {
  prefs[k] = clampPref(k, v);
  savePrefs();
}

function getPrefs() { return prefs; }

/* ---- 持久化 ----
   真正落盘的是后端 prefs.json（EXE_DIR），localStorage 只是同一次运行里的缓存。
   为什么不只靠 localStorage：主窗口用 private_mode 启动，每次都是全新的缓存目录，
   localStorage 会被清空，设置就"重启复位"了。 */
var pushTimer = null;

function pushPrefs() {
  if (pushTimer) clearTimeout(pushTimer);
  pushTimer = setTimeout(function () {
    pushTimer = null;
    try {
      fetch("/api/prefs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(prefs)
      }).catch(function () {});
    } catch (e) {}
  }, 300);
}

/* 从后端读设置；失败就沿用 localStorage / 默认值（不能让界面起不来） */
function loadPrefs() {
  return fetch("/api/prefs", { cache: "no-store" })
    .then(function (r) { return r.json(); })
    .then(function (d) {
      if (d && d.ok && d.prefs && typeof d.prefs === "object") {
        Object.assign(prefs, d.prefs);
      }
    })
    .catch(function () {});
}

function savePrefs() {
    try { localStorage.setItem("app_prefs", JSON.stringify(prefs)); } catch (e) {}
    pushPrefs();
    window.dispatchEvent(new CustomEvent("app:prefs", { detail: prefs }));
  }

  function playPreview(file, vol) {
    const a = new Audio(SND + file);
    a.volume = vol;
    a.play().catch(() => {});
  }

  function buildPanel() {
    const box = document.createElement("div");
    box.id = "cfgPanel";
    box.innerHTML = `
      <div class="cfgCard">
        <div class="cfgTitle">系统设置</div>

        <div class="cfgSec">
          <div class="cfgLbl">界面音效</div>
          <div class="cfgRow"><span>悬停</span><select id="cfgSeHover"></select><button data-listen="seHover">试听</button></div>
          <div class="cfgRow"><span>点击</span><select id="cfgSeClick"></select><button data-listen="seClick">试听</button></div>
          <div class="cfgRow"><span>取消</span><select id="cfgSeCancel"></select><button data-listen="seCancel">试听</button></div>
        </div>

        <div class="cfgSec">
          <div class="cfgLbl">背景音乐</div>
          <div class="cfgRow"><span>平静</span><select id="cfgBgmCalm"></select><button data-listen="bgmCalm">试听</button></div>
          <div class="cfgRow"><span>温柔</span><select id="cfgBgmWarm"></select><button data-listen="bgmWarm">试听</button></div>
          <div class="cfgRow"><span>难过</span><select id="cfgBgmSad"></select><button data-listen="bgmSad">试听</button></div>
          <div class="cfgRow"><span>紧张</span><select id="cfgBgmTense"></select><button data-listen="bgmTense">试听</button></div>
        </div>

        <div class="cfgSec">
          <div class="cfgLbl">音量</div>
          <div class="cfgRow"><span>音乐</span><input id="cfgBgmVol" type="range" min="0" max="100"><b id="cfgBgmVolV"></b></div>
          <div class="cfgRow"><span>音效</span><input id="cfgSeVol" type="range" min="0" max="100"><b id="cfgSeVolV"></b></div>
        </div>


        <div class="cfgBtns">
          <button id="cfgSave">保存</button>
          <button id="cfgClose">关闭</button>
        </div>
      </div>`;
    document.body.appendChild(box);

    const seSel = ["cfgSeHover", "cfgSeClick", "cfgSeCancel"];
    const bgmSel = ["cfgBgmCalm", "cfgBgmWarm", "cfgBgmSad", "cfgBgmTense"];

    seSel.forEach(id => {
      const el = box.querySelector("#" + id);
      catalog.se.forEach((s, i) => {
        const o = document.createElement("option");
        o.value = i;
        o.textContent = s.name || ("SE " + String(i).padStart(2, "0"));
        el.appendChild(o);
      });
    });
    bgmSel.forEach(id => {
      const el = box.querySelector("#" + id);
      catalog.bgm.forEach((b, i) => {
        const o = document.createElement("option");
        o.value = i;
        o.textContent = (b.name || ("BGM " + String(i).padStart(2, "0")));
        el.appendChild(o);
      });
    });

    function sync() {
      box.querySelector("#cfgSeHover").value = prefs.seHover;
      box.querySelector("#cfgSeClick").value = prefs.seClick;
      box.querySelector("#cfgSeCancel").value = prefs.seCancel;
      box.querySelector("#cfgBgmCalm").value = prefs.bgmCalm;
      box.querySelector("#cfgBgmWarm").value = prefs.bgmWarm;
      box.querySelector("#cfgBgmSad").value = prefs.bgmSad;
      box.querySelector("#cfgBgmTense").value = prefs.bgmTense;
      box.querySelector("#cfgBgmVol").value = prefs.bgmVol;
      box.querySelector("#cfgSeVol").value = prefs.seVol;
      box.querySelector("#cfgBgmVolV").textContent = prefs.bgmVol + "%";
      box.querySelector("#cfgSeVolV").textContent = prefs.seVol + "%";
    }
    sync();

    function readBack() {
      prefs.seHover = +box.querySelector("#cfgSeHover").value;
      prefs.seClick = +box.querySelector("#cfgSeClick").value;
      prefs.seCancel = +box.querySelector("#cfgSeCancel").value;
      prefs.bgmCalm = +box.querySelector("#cfgBgmCalm").value;
      prefs.bgmWarm = +box.querySelector("#cfgBgmWarm").value;
      prefs.bgmSad = +box.querySelector("#cfgBgmSad").value;
      prefs.bgmTense = +box.querySelector("#cfgBgmTense").value;
      prefs.bgmVol = +box.querySelector("#cfgBgmVol").value;
      prefs.seVol = +box.querySelector("#cfgSeVol").value;
    }

    box.querySelectorAll("[data-listen]").forEach(btn => {
      btn.addEventListener("click", () => {
        readBack();
        const key = btn.dataset.listen;
        if (key.startsWith("se")) {
          const it = catalog.se[prefs[key]];
          if (it) playPreview(it.file, prefs.seVol / 100);
        } else {
          const it = catalog.bgm[prefs[key]];
          if (it) playPreview(it.file, prefs.bgmVol / 100);
        }
      });
    });

    box.querySelector("#cfgBgmVol").addEventListener("input", e => {
      box.querySelector("#cfgBgmVolV").textContent = e.target.value + "%";
    });
    box.querySelector("#cfgSeVol").addEventListener("input", e => {
      box.querySelector("#cfgSeVolV").textContent = e.target.value + "%";
    });

    box.querySelector("#cfgSave").addEventListener("click", () => {
      readBack();
      savePrefs();
      const it = catalog.se[prefs.seClick];
      if (it) playPreview(it.file, prefs.seVol / 100);
      box.classList.remove("show");
    });
    box.querySelector("#cfgClose").addEventListener("click", () => {
      box.classList.remove("show");
    });

    return box;
  }

window.App = { prefs: null, catalog: null };

  /* 目录和设置并行加载，两个都就绪后才创建 App：
     否则 opt.js 的还原会在拿到 prefs 之前跑，界面显示和实际值对不上 */
  Promise.all([
    fetch(SND + "catalog.json").then(r => r.json()),
    loadPrefs()
  ])
    .then(function (res) {
      catalog = res[0] || { se: [], bgm: [] };
      const panel = buildPanel();
      window.App.catalog = catalog;
      window.App.prefs = prefs;
      window.App.setPref = (k, v) => { setPref(k, v); };
      window.App.openConfig = () => { panel.classList.add("show"); };
      window.App.getPrefs = () => prefs;
      window.App.playPreview = playPreview;
      window.App.catalogGet = () => catalog;
      if (location.hash === "#config") panel.classList.add("show");
      window.dispatchEvent(new Event("app:ready"));
    })
    .catch(e => console.warn("音效目录加载失败", e));
})();

