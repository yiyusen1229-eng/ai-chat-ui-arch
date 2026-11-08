/* text.js —— 从 game.js 搬出的功能块（逻辑未改）
 * 挂载：WA2.text；同时同步到 window 以兼容旧调用。
 */
(function () {
  var WA2 = window.WA2 = window.WA2 || {};
  var $ = (WA2.util && WA2.util.$) || function (id) { return document.getElementById(id); };

  const PAGE_MAX_CHARS = 60;      // 3 行 × 每行 20 字（补丁 FONT_PER_LINE=20）
  const PAGE_MAX_LINES = 3;

  let pageTexts = [];
  let pageIndex = 0;

  function splitPages(text) {
    const raw = (text || "").replace(/\r/g, "").trim();
    if (!raw) return ["……"];
    const pages = [];
    let rest = raw;
    const BREAK = "。！？…，、；：」』）)]】";
    while (rest.length > 0) {
      if (rest.length <= PAGE_MAX_CHARS) { pages.push(rest); break; }
      let cut = PAGE_MAX_CHARS;
      // 在 60 字内找最后一个句读，找不到就硬切
      for (let i = PAGE_MAX_CHARS - 1; i >= PAGE_MAX_CHARS - 18 && i > 0; i--) {
        if (BREAK.indexOf(rest[i]) >= 0) { cut = i + 1; break; }
      }
      pages.push(rest.slice(0, cut));
      rest = rest.slice(cut);
    }
    return pages.length ? pages : ["……"];
  }

  function renderPage() {
    const box = $("msgText");
    const txt = pageTexts[pageIndex] || "";
    hidePageCursor();                /* 换页先收起来，打完再出 */
    if (gdiRenderMsg && window.__gdiOK !== false) {
      gdiRenderMsg(txt, box, 56, 1200, 1).then((ok) => {
        if (ok) { box.classList.add("gdi"); startReveal(box, txt); }
        else { window.__gdiOK = false; legacyTypewriter(txt); }
        updatePageCursor();
      });
    } else {
      legacyTypewriter(txt);
    }
  }

  /* ---------- 翻页提示：文字末尾的页面图标 ----------
     和原版一样：一段话打完、后面还有下一页时，在最后一个字后面闪动页面图标。
     文字是 /api/text 出的整段图（每字 28×32 的固定格子，见 sub_render.py），
     所以最后一字的位置可以直接由字数算出来，不用测量排版。 */
  function pageCursorEl() {
    const box = $("msgText");
    if (!box) return null;
    let cur = document.getElementById("pageCursor");
    if (!cur) {
      cur = document.createElement("div");
      cur.id = "pageCursor";
      box.appendChild(cur);
    } else if (cur.parentNode !== box) {
      box.appendChild(cur);          /* 每次重渲染 msgText 都会被清空，补回来 */
    }
    return cur;
  }

  function hidePageCursor() {
    const cur = document.getElementById("pageCursor");
    if (cur) cur.style.display = "none";
  }

  function updatePageCursor() {
    const cur = pageCursorEl();
    if (!cur) return;
    const text = pageTexts[pageIndex] || "";
    /* 打完这一页就该出现（不管后面还有没有页）；自动续说时由她自己往下走，不需要这个提示 */
    if (typing || !text || document.body.classList.contains("autoOn")) {
      cur.style.display = "none";
      return;
    }
    const img = $("msgText") && $("msgText").querySelector("img");
    if (!img || !img.naturalWidth || !img.clientWidth) {
      hidePageCursor();              /* 兜底打字机走文字：没有格子可对，先不显示 */
      return;
    }
    const scale = img.clientWidth / img.naturalWidth;
    const n = text.length;                       /* 1 起算的最后一个字 */
    const line = Math.floor((n - 1) / PER_LINE);
    const col = ((n - 1) % PER_LINE) + 1;
    cur.style.left = Math.round(col * CHAR_ADV * scale) + "px";
    cur.style.top = Math.round((line * LINE_H + 2) * scale) + "px";
    cur.style.transform = "scale(" + scale + ")";
    cur.style.display = "block";
  }

  function showReply(text) {
    pageTexts = splitPages(text);
    pageIndex = 0;
    renderPage();
  }

  function nextPage() {
    if (pageIndex < pageTexts.length - 1) {
      pageIndex++;
      renderPage();
      return true;
    }
    return false;
  }

  /* ---------- 打字机 ---------- */
  /* 文字显示速度（OPTION 1）：0 瞬间 / 1 高速 / 2 普通 / 3 顺滑，值是「每字毫秒」 */
  const SPEED_MS = [0, 14, 30, 50];

  /* 服务端排版是固定的：每字占 32×32 格子、水平步进 28px、每行 20 字、行高 32px
     （见 sub_render.py 的 FONT_ADVANCE / FONT_PER_LINE / FONT_OCCUPATION）。
     所以拿到整段的渲染图后，可以由「显示到第几个字」反推裁剪区域，
     用 clip-path 把还没轮到的字裁掉：不用逐字出图，也不会重排。 */
  const PER_LINE = 20, CHAR_ADV = 28, LINE_H = 32;

  function textSpeed() {
    const P = (window.App && window.App.getPrefs) ? window.App.getPrefs() : {};
    const s = parseInt(P.textSpeed, 10);
    return isNaN(s) ? 2 : Math.max(0, Math.min(3, s));
  }

  let revealTimer = null, revealEl = null;

  function stopReveal() {
    if (revealTimer) { clearTimeout(revealTimer); revealTimer = null; }
    if (revealEl) {
      revealEl.style.clipPath = "";
      revealEl.style.webkitClipPath = "";
      revealEl = null;
    }
    typing = false;
  }

  /* 只显示前 k 个字；返回 false 表示排版模型和图对不上（不做动画） */
  function revealTo(el, text, k) {
    const img = el.querySelector("img");
    if (!img || !img.naturalWidth) return false;
    const W = img.naturalWidth, H = img.naturalHeight;
    const lines = Math.ceil(text.length / PER_LINE);
    if (Math.abs(H - lines * LINE_H) > 2) return false;
    if (k >= text.length) {
      el.style.clipPath = ""; el.style.webkitClipPath = "";
      return true;
    }
    if (k <= 0) {
      el.style.clipPath = "polygon(0 0, 0 0, 0 0)";
      return true;
    }
    const line = Math.floor((k - 1) / PER_LINE);
    const col = ((k - 1) % PER_LINE) + 1;
    const y0 = line * LINE_H, y1 = (line + 1) * LINE_H;
    const x1 = col * CHAR_ADV + 4;                 /* 末字右侧还带 2px 描边 */
    const px = (v, total) => (v / total * 100).toFixed(3) + "%";
    const poly = "polygon(0 0, 100% 0, 100% " + px(y0, H) +
                 ", " + px(x1, W) + " " + px(y0, H) +
                 ", " + px(x1, W) + " " + px(y1, H) +
                 ", 0 " + px(y1, H) + ")";
    el.style.clipPath = poly;
    el.style.webkitClipPath = poly;
    return true;
  }

  function startReveal(el, text) {
    stopReveal();
    text = text || "";
    if (!el || !text) return;
    const sp = textSpeed();
    if (sp === 0) { updatePageCursor(); return; }   /* 瞬间显示：整段直接出来 */
    const img = el.querySelector("img");
    if (!img) return;
    if (!img.complete || !img.naturalWidth) {
      img.addEventListener("load", () => startReveal(el, text), { once: true });
      return;
    }
    if (!revealTo(el, text, 0)) return;       /* 模型不匹配：直接整段显示，别卡住 */
    typing = true;
    revealEl = el;
    fullText = text;
    let i = 0;
    const step = () => {
      if (revealEl !== el) return;
      i++;
      if (!revealTo(el, text, i)) { stopReveal(); return; }
      if (i >= text.length) { stopReveal(); updatePageCursor(); return; }
      const ch = text.charAt(i - 1);
      const d = SPEED_MS[sp] * (/[，。！？…、,\.!?]/.test(ch) ? 3 : 1);
      revealTimer = setTimeout(step, d);
    };
    revealTimer = setTimeout(step, SPEED_MS[sp]);
  }

  function typewriter(text, done) {
    const box = $("msgText");
    if (box && window.__gdiOK !== false) {
      // 先按 17px/520px 宽度渲染整段
      gdiRenderMsg(text, box, 56, 1200, 1).then((ok) => {
        if (ok) {
          fullText = text;
          box.classList.add("gdi");
          startReveal(box, text);
          if (done) done();
        } else {
          legacyTypewriter(text, done);
        }
      });
      return;
    }
    legacyTypewriter(text, done);
  }

  function legacyTypewriter(text, done) {
    const el = $("msgText");
    fullText = text; el.textContent = ""; typing = true;
    let i = 0;
    const base = SPEED_MS[textSpeed()];
    if (base === 0) { el.textContent = text; typing = false; done && done(); hidePageCursor(); return; }
    const tick = () => {
      if (!typing) { el.textContent = fullText; typing = false; done && done(); hidePageCursor(); return; }
      if (i >= text.length) { typing = false; done && done(); hidePageCursor(); return; }
      const ch = text[i++];
      el.textContent += ch;
      setTimeout(tick, "，。！？…、".includes(ch) ? base * 3 : base);
    };
    tick();
  }

  /* ---------- Backlog ---------- */
  var _exp = {};
  var _names = ["hidePageCursor", "legacyTypewriter", "nextPage", "pageCursorEl", "renderPage", "revealTo", "showReply", "splitPages", "startReveal", "stopReveal", "textSpeed", "typewriter", "updatePageCursor"];
  for (var _i = 0; _i < _names.length; _i++) {
    try { var _f = eval(_names[_i]); if (typeof _f === "function") { _exp[_names[_i]] = _f; window[_names[_i]] = _f; } } catch (e) {}
  }
  WA2.text = _exp;
  WA2.register("text", _exp);
  /* __export_all__ */
  ["hidePageCursor", "legacyTypewriter", "nextPage", "pageCursorEl", "renderPage", "revealTo", "showReply", "splitPages", "startReveal", "stopReveal", "textSpeed", "typewriter", "updatePageCursor", "BREAK", "P", "PAGE_MAX_CHARS", "PAGE_MAX_LINES", "PER_LINE", "SPEED_MS", "W", "WA2", "_exp", "_names", "base", "box", "ch", "col", "cur", "cut", "d", "el", "i", "img", "line", "lines", "n", "pageIndex", "pageTexts", "pages", "poly", "px", "raw", "rest", "revealTimer", "s", "scale", "sp", "step", "text", "tick", "txt", "x1", "y0"].forEach(function (n) {
    try { var v = eval(n); if (v !== undefined) { window[n] = v; } } catch (e) {}
  });
})();

/* 兼容：旧代码/探针会用这两个名字 */
if (typeof window.__startReveal !== "function") {
  window.__startReveal = function (el, text) { try { return startReveal(el, text); } catch (e) {} };
}
if (typeof window.__stopReveal !== "function") {
  window.__stopReveal = function () { try { return stopReveal(); } catch (e) {} };
}
