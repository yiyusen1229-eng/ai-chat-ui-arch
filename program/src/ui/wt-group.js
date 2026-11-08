/* wt-group.js —— 多角色同场 + 剧情把控（右键菜单入口）
 *
 * 2026-11-08 用户要求提前引入（路线图第 14 条）：
 *   同场时对话像小说：旁白 + 若干角色的台词；由模型挑下一位说话的人。
 *
 * 这里只负责「设置」：选同场角色、写一句剧情方向，存到后端 group.json；
 * 请求时由 wt-chat 把 {cast, story} 一起带上（/api/group/prefs 持久化）。
 */
(function () {
  var WA2 = window.WA2 = window.WA2 || {};

  var state = { items: [], cast: [], story: "", ready: false };

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  function toast(msg, ms) {
    try { if (window.__toast) window.__toast(msg, ms || 2200); } catch (e) {}
  }

  function closeAll() {
    var items = document.querySelectorAll("#menuBar .mbItem");
    for (var i = 0; i < items.length; i++) items[i].classList.remove("open");
    document.body.classList.remove("mbOpen");
  }

  function save() {
    fetch("/api/group/prefs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cast: state.cast, story: state.story })
    }).catch(function () {});
  }

  function load() {
    return fetch("/api/group/cast", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d || !d.ok) return;
        state.items = d.items || [];
        var p = d.prefs || {};
        state.cast = p.cast || [];
        state.story = p.story || "";
        state.ready = true;
        paint();
      })
      .catch(function () {});
  }

  function paint() {
    var box = document.getElementById("groupCastBox");
    if (!box) return;
    box.innerHTML = "";
    if (!state.items.length) {
      box.appendChild(el("div", "mi-row", "（没有可用的角色）"));
      return;
    }
    state.items.forEach(function (it) {
      var on = state.cast.indexOf(it.id) >= 0;
      var row = el("div", "mi-row", (on ? "✔ " : "　 ") + it.name);
      row.addEventListener("click", function (ev) {
        ev.stopPropagation();
        var i = state.cast.indexOf(it.id);
        if (i >= 0) state.cast.splice(i, 1);
        else state.cast.push(it.id);
        paint();
        save();
      });
      box.appendChild(row);
    });
    box.appendChild(el("div", "mi-sep"));
    var srow = el("div", "mi-row", "剧情方向…" + (state.story ? "（已设）" : ""));
    srow.addEventListener("click", function (ev) {
      ev.stopPropagation();
      var v = window.prompt("这一场的剧情方向（留空=不限）", state.story || "");
      if (v === null) return;
      state.story = String(v).trim();
      save();
      paint();
      toast(state.story ? "剧情方向已设" : "已清除剧情方向");
    });
    box.appendChild(srow);
    var crow = el("div", "mi-row", "清除同场角色");
    crow.addEventListener("click", function (ev) {
      ev.stopPropagation();
      state.cast = [];
      state.story = "";
      save();
      paint();
      toast("已回到单人对话");
    });
    box.appendChild(crow);
    var hint = el("div", "mi-row", state.cast.length >= 2
      ? "同场：" + state.cast.join("、") : "同场：无（单人）");
    hint.style.opacity = ".65";
    box.appendChild(hint);
  }

  function buildMenu() {
    var bar = document.getElementById("menuBar");
    if (!bar || document.getElementById("mbGroup")) return;
    var item = el("div", "mbItem", "同场");
    item.id = "mbGroup";
    var menu = el("div", "mbMenu");
    menu.id = "groupCastBox";
    item.appendChild(menu);
    item.addEventListener("click", function (ev) {
      ev.stopPropagation();
      var was = item.classList.contains("open");
      closeAll();
      if (!was) { item.classList.add("open"); document.body.classList.add("mbOpen"); }
      if (!state.ready) load();
    });
    bar.appendChild(item);
    paint();
    load();
  }

  function payload() {
    if (!state.cast || state.cast.length < 2) return {};
    return { cast: state.cast.slice(), story: state.story || "" };
  }

  var api = { payload: payload, reload: load,
              state: function () { return { cast: state.cast.slice(), story: state.story }; } };
  WA2.group = api;
  WA2.register && WA2.register("group", api);

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", buildMenu);
  } else {
    buildMenu();
  }
})();
