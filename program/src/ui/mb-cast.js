/* mb-cast.js —— 标题栏「多角色」菜单（控制台）
 *
 * 玩家看不到这些；这是给开发者/调参用的窗口：
 *   · 连接状态：每个角色 聊天模型 / 语音 是否连上
 *   · 阵容：本场有谁、谁是主角、谁是龙套
 *   · 队列：后台待播条数、清空
 *   · 开关：多角色（自动模式仍用右下角那个按钮）
 *   · 长度：小 / 中 / 大
 *   · 测试：往队列里塞两条占位台词（不接模型，验证链路）
 *
 * 数据来源：/api/cast/status、/api/direct/status
 */
(function () {
  var WA2 = window.WA2 = window.WA2 || {};
  var bar = null;
  var item = null;
  var menu = null;
  var last = { cast: null, director: null };

  function el(tag, cls, text) {
    var d = document.createElement(tag);
    if (cls) d.className = cls;
    if (text != null) d.textContent = text;
    return d;
  }

  function toast(msg) {
    try { if (window.__toast) window.__toast(msg, 2200); } catch (e) {}
  }

  function api(path, body) {
    var opt = body ? {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    } : { cache: "no-store" };
    return fetch(path, opt).then(function (r) { return r.json(); });
  }

  function row(label, onclick, cls) {
    var r = el("div", "mi-row" + (cls ? " " + cls : ""), label);
    if (onclick) {
      r.addEventListener("click", function (e) {
        e.stopPropagation();
        onclick(e);
      });
    }
    return r;
  }

  function subRow(label, onclick) {
    var r = el("div", "mi-row", label);
    if (onclick) {
      r.addEventListener("click", function (e) {
        e.stopPropagation();
        onclick(e);
      });
    }
    return r;
  }

  function sub(label, rows) {
    var wrap = el("div", "mi-row has-sub", label);
    var box = el("div", "mbSub");
    rows.forEach(function (r) { box.appendChild(r); });
    wrap.appendChild(box);
    /* 子菜单靠 .open 才展开（和 menubar 的 has-sub 一致） */
    wrap.addEventListener("mouseenter", function () { wrap.classList.add("open"); });
    wrap.addEventListener("mouseleave", function () { wrap.classList.remove("open"); });
    return wrap;
  }

  function yn(v) {
    return v ? "已连接" : "未连接";
  }

  function closeAllMenus() {
    var all = document.querySelectorAll("#menuBar .mbItem");
    for (var i = 0; i < all.length; i++) all[i].classList.remove("open");
    document.body.classList.remove("mbOpen");
  }

  function toggleMenu() {
    var wasOpen = item.classList.contains("open");
    closeAllMenus();
    if (!wasOpen) {
      item.classList.add("open");
      document.body.classList.add("mbOpen");
    }
  }

  function render() {
    if (!menu) return;
    menu.innerHTML = "";
    var c = last.cast, d = last.director;

    /* 连接状态 */
    var connRows = [];
    if (c && c.members && c.members.length) {
      c.members.forEach(function (m) {
        var s = m.name + "（" + m.label + "）  聊天 " + yn(m.chat) + " · 语音 " + yn(m.voice);
        connRows.push(subRow(s));
      });
    } else {
      connRows.push(subRow("（阵容为空）"));
    }
    menu.appendChild(sub("连接状态", connRows));

    /* 导演模型 */
    var dm = (d && d.model) || {};
    menu.appendChild(subRow("导演模型：" + (dm.model || "未配置") + (dm.connected ? "（已连接）" : "（未连接）"), function () {
      closeAllMenus();
      if (window.WA2 && WA2.spModel && WA2.spModel.open) WA2.spModel.open("director");
    }));

    /* 阵容 */
    var castRows = [subRow("清空阵容", function () {
      api("/api/cast/set", { members: [] }).then(refresh);
    })];
    if (c && c.can_join) {
      var _inCast = {};
      (c.members || []).forEach(function (m) { if (m.id) _inCast[m.id] = true; });
      c.can_join.forEach(function (x) {
        if (_inCast[x.id]) {
          /* 已经在场：给一个「移出」 */
          castRows.push(subRow("移出：" + x.name, function () {
            var members = ((c.members || []).filter(function (m) { return m.role !== "extra"; })
              .filter(function (m) { return m.id !== x.id; })
              .map(function (m) { return { id: m.id, role: m.role }; }));
            api("/api/cast/set", { members: members }).then(refresh);
          }));
          return;
        }
        castRows.push(subRow((x.ok ? "加入：" : "缺导演卡：") + x.name, function () {
          if (!x.ok) return;
          var members = ((c.members || []).filter(function (m) { return m.role !== "extra"; })
            .map(function (m) { return { id: m.id, role: m.role }; }));
          if (members.some(function (m) { return m.id === x.id; })) return;   /* 已加入就不重复 */
          members.push({ id: x.id, role: "actor" });
          api("/api/cast/set", { members: members }).then(refresh);
        }));
      });
    }
    castRows.unshift(subRow("加入玩家（你）", function () {
      var members = ((c.members || []).filter(function (m) { return m.role !== "player"; })
        .map(function (m) { return { id: m.id, role: m.role, name: m.name }; }));
      members.push({ role: "player", name: "你" });
      api("/api/cast/set", { members: members }).then(refresh);
    }));
    menu.appendChild(sub("阵容", castRows));

    /* 导演模型 */
    menu.appendChild(row("导演模型…", function () {
      /* 开对话框时把菜单收起来，别挡住弹窗 */
      closeAllMenus();
      if (window.WA2 && WA2.spModel && WA2.spModel.open) WA2.spModel.open("director");
    }));

    /* 队列 */
    var q = (d && d.queue) || {};
    menu.appendChild(sub("队列", [
      subRow("待播 " + (q.pending || 0) + " 条"),
      subRow("清空队列", function () { api("/api/play/clear", {}).then(refresh); })
    ]));

    /* 开关 */
    menu.appendChild(sub("开关", [
      subRow("多角色：" + (d && d.enabled ? "开" : "关"), function () {
        var _next = !(d && d.enabled);
        api("/api/direct/options", { enabled: _next }).then(function () {
        /* 切回单角色：把还没播的「拍」清掉，否则它们会在自动模式里冒出来 */
        if (!_next) { try { fetch("/api/play/clear", { method: "POST" }).catch(function () {}); } catch (e) {} }
        /* 切换后立刻让播放器重新读一次开关，别等 5 秒轮询 */
        try { if (WA2.play && WA2.play.refresh) WA2.play.refresh(); } catch (e) {}
        refresh();
      });
      }),
    ]));

    /* 长度 */
    menu.appendChild(sub("长度", [
      subRow("小（7 拍 / 约 350 字）", function () { api("/api/direct/options", { length: "small" }).then(refresh); }),
      subRow("中（12 拍 / 约 700 字）", function () { api("/api/direct/options", { length: "medium" }).then(refresh); }),
      subRow("大（25 拍 / 约 1500 字）", function () { api("/api/direct/options", { length: "large" }).then(refresh); })
    ]));

    menu.appendChild(el("div", "mi-sep"));

    /* 测试 */
    menu.appendChild(row("写一拍（用模型）", function () {
      toast("导演正在写…");
      api("/api/direct/gen", { n: 2 }).then(function (r) {
        if (!r || !r.ok) toast((r && r.error) || "导演没写出来");
        else toast("收到 " + (r.pushed || 0) + " 条");
        refresh();
      });
    }));
    menu.appendChild(row("塞两条占位台词", function () {
      api("/api/direct/stub", { n: 2 }).then(refresh);
    }));
    menu.appendChild(row("刷新状态", function () { refresh(); }));
  }

  function refresh() {
    place();
    return Promise.all([api("/api/cast/status"), api("/api/direct/status")])
      .then(function (rs) {
        last.cast = (rs[0] && rs[0].cast) || null;
        last.director = (rs[1] && rs[1].director) || null;
        last.director.queue = last.director.queue || ((rs[1] || {}).queue || {});
        render();
        return last;
      })
      .catch(function () { render(); });
  }

  function build() {
    bar = document.getElementById("menuBar");
    if (!bar || document.getElementById("mbCast")) return;

    item = el("div", "mbItem", "多角色");
    item.id = "mbCast";
    menu = el("div", "mbMenu");
    item.appendChild(menu);

    bar.appendChild(item);
    place();

    item.addEventListener("mouseenter", function () { place(); refresh(); });
    /* 和 menubar.makeMenu 一样的开合：点一下开/关，点空白关 */
    menu.addEventListener("click", function (e) { e.stopPropagation(); });
    item.addEventListener("click", function (ev) {
      ev.stopPropagation();
      toggleMenu();
    });
    document.addEventListener("click", closeAllMenus);
    [400, 1200, 3000].forEach(function (ms) { setTimeout(place, ms); });
    render();
    refresh();
  }

  /* 始终待在「系统」前面；菜单栏重建/后建都能自己归位 */
  function place() {
    var b = document.getElementById("menuBar");
    if (!b || !item || item.parentNode !== b) return;
    var sys = null;
    for (var i = 0; i < b.children.length; i++) {
      var ch = b.children[i];
      if (ch !== item && ch.className && ch.className.indexOf("mbItem") >= 0 &&
          ch.textContent.replace(/\s+/g, "").indexOf("系统") === 0) {
        sys = ch;
        break;
      }
    }
    if (sys && sys.previousElementSibling !== item) b.insertBefore(item, sys);
  }

  function boot() {
    /* 菜单栏由 menubar.js 异步建，可能比这个模块晚；等它出现再挂 */
    var tries = 0;
    (function waitBar() {
      if (document.getElementById("menuBar")) {
        build();
        return;
      }
      if (tries++ > 60) return;
      setTimeout(waitBar, 200);
    })();
  }

  var apiOut = { refresh: refresh, render: render };
  WA2.mbCast = apiOut;
  WA2.register && WA2.register("mbCast", apiOut);

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
