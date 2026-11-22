/* ============================================================
   界面文字统一走 /api/text 渲染
   ------------------------------------------------------------
   把界面上所有纯文字元素替换成由后端 sub_render 生成的图片,
   与对话框使用完全相同的字体/字号/色号(无描边模式)。

   为避免卡死:
     · 跳过不可见元素(隐藏的界面不生成图片)
     · 并发上限 4，排队处理
     · MutationObserver 防抖 120ms，只扫新增节点本身
   ============================================================ */
(function () {
  var VER = "1029atlas";
  var pending = new Set();
  var queue = [];
  var running = 0;
  var MAX_CONCURRENT = 4;

  function fontSizePx(el) {
    return parseFloat(getComputedStyle(el).fontSize) || 12;
  }

  function visible(el) {
    if (!el || !el.isConnected) return false;
    if (typeof el.offsetParent !== "undefined" && el.offsetParent === null) {
      // position:fixed 的元素 offsetParent 也是 null，需要额外判断尺寸
      var r = el.getBoundingClientRect();
      return r.width > 0 && r.height > 0;
    }
    return true;
  }

  function makeImg(text, fs, cb, perLine, tight) {
    var url = "/api/text?text=" + encodeURIComponent(text) + "&plain=1&v=" + VER +
              (perLine ? ("&chars_per_line=" + perLine) : "") +
              (tight ? "&tight=1" : "");
    var img = new Image();
    img.onload = function () {
      img.style.cssText =
        "display:inline-block;vertical-align:middle;height:" +
        (fs * 1.0).toFixed(1) + "px;width:auto;pointer-events:none";
      cb(img);
    };
    img.onerror = function () { cb(null); };
    img.src = url;
  }

  function pump() {
    while (running < MAX_CONCURRENT && queue.length) {
      var job = queue.shift();
      running++;
      (function (el, text, fs, perLine, tight) {
        makeImg(text, fs, function (img) {
          running--;
          pending.delete(el);
          if (img && el.isConnected) {
            el.textContent = "";
            img.setAttribute("data-uitxt", text);
            el.appendChild(img);
            el.style.lineHeight = "1";
          }
          pump();
        }, perLine, tight);
      })(job.el, job.text, job.fs,
         job.el.dataset ? job.el.dataset.uitxtPerLine : "",
         job.el.dataset ? job.el.dataset.uitxtTight : "");
    }
  }

  function convert(el) {
    if (!el || pending.has(el) || !el.isConnected) return;
    if (el.closest("script,style,noscript")) return;
    var tag = el.tagName;
    if (tag === "IMG" || tag === "INPUT" || tag === "TEXTAREA" ||
        tag === "SELECT" || tag === "OPTION" || tag === "CANVAS") return;
    if (el.children.length > 0) return;
    if (el.closest("#msgText") || el.closest("#namePlate")) return;
    /* 动作/心理文本必须保留**真文字**：以前短句被转成一张图片（宽度自适应→被挤小），
       超过 200 字的又不转（保持原字号）——表现就是"有的字大有的字小、长句被压缩"。
       保留真文字才能按 word-wrap 自动分行、字号恒定。 */
    if (el.closest("#thinkBox")) return;
    if (el.closest("#menuBar")) return;
    if (el.id === "__BUILDSTAMP" || el.closest("#__BUILDSTAMP")) return;
    if (el.dataset && el.dataset.noUitext) return;      // 明确要求保留原生字体
    if (el.closest("[data-no-uitext]")) return;
    if (!visible(el)) return;                       // 隐藏界面不处理

    var text = (el.textContent || "").trim();
    if (!text || text.length > 200) return;

    var old = el.querySelector("img[data-uitxt]");
    if (old) {
      if (old.getAttribute("data-uitxt") === text) return;
    }

    pending.add(el);
    queue.push({ el: el, text: text, fs: fontSizePx(el) });
    pump();
  }

  function scan(root) {
    if (!root || root.nodeType !== 1) return;
    if (root.children.length === 0) {
      convert(root);
      return;
    }
    var all = root.querySelectorAll("*");
    for (var i = 0; i < all.length; i++) {
      if (all[i].children.length === 0) convert(all[i]);
    }
  }

  var timer = null;
  var dirty = new Set();

  function flush() {
    timer = null;
    var nodes = Array.from(dirty);
    dirty.clear();
    nodes.forEach(function (n) {
      if (n && n.isConnected) scan(n);
    });
  }

  function schedule(n) {
    if (!n) return;
    dirty.add(n);
    if (!timer) timer = setTimeout(flush, 120);
  }

  function start() {
    scan(document.body);

    var mo = new MutationObserver(function (muts) {
      for (var i = 0; i < muts.length; i++) {
        var m = muts[i];
        for (var j = 0; j < m.addedNodes.length; j++) {
          var n = m.addedNodes[j];
          if (n.nodeType === 1) schedule(n);
          else if (n.nodeType === 3 && n.parentElement) schedule(n.parentElement);
        }
      }
    });
    mo.observe(document.body, { childList: true, subtree: true });

    // 对外暴露: 某些面板先 display:none, 显示后需要手动补扫描
    window.__uiTextScan = function (root) {
      if (!root) return;
      setTimeout(function () { scan(root); }, 30);
      setTimeout(function () { scan(root); }, 150);
    };

    /* 给别的模块用的单条渲染：把一段文字渲染进元素。
       special.js 的读数/角色名走这里，和界面其它字模是同一条链路。
       opts: { size, tight, perLine } */
    window.__uiTextRender = function (el, text, opts) {
      if (!el) return;
      opts = opts || {};
      var fs = opts.size || parseFloat(getComputedStyle(el).fontSize) || 12;
      makeImg(text, fs, function (img) {
        if (!img || !el.isConnected) return;
        el.textContent = "";
        img.setAttribute("data-uitxt", text);
        el.appendChild(img);
        el.style.lineHeight = "1";
      }, opts.perLine || "", opts.tight ? "1" : "");
    };

    // 输入框占位符：走 /api/text（有磁盘缓存），角色名改了会跟着换
    var inp = document.getElementById("input");
    if (inp && inp.parentElement) {
      var bar = inp.parentElement;
      var ph = document.createElement("img");
      var fs2 = parseFloat(getComputedStyle(inp).fontSize) || 12;
      ph.style.cssText =
        "position:absolute;left:26px;top:50%;transform:translateY(-50%);" +
        "height:" + fs2.toFixed(1) + "px;pointer-events:none;opacity:.85;display:none";
      bar.style.position = bar.style.position || "relative";
      bar.appendChild(ph);
      // 关键: 清掉原生占位符, 否则会有两份文字叠在一起
      inp.setAttribute("placeholder", "");
      var lastHint = "";
      function drawHint() {
        var name = (window.__charName || "和纱");
        var text = "对" + name + "说点什么…（Enter 发送）";
        if (text === lastHint) return;
        lastHint = text;
        ph.src = "/api/text?text=" + encodeURIComponent(text) +
                 "&plain=1&chars_per_line=40&v=" + VER;
      }
      drawHint();
      document.addEventListener("wa2:charname", drawHint);
      function sync() {
        var hide = !!(inp.value && inp.value.length) || document.activeElement === inp;
        ph.style.display = hide ? "none" : "block";
      }
      inp.addEventListener("input", sync);
      inp.addEventListener("focus", sync);
      inp.addEventListener("blur", sync);
      sync();
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
