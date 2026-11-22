/* wt-core.js —— 前端共享命名空间（拆分第 1 步，只加不改行为）
 *
 * 以后每个功能一个文件，都挂到 WA2 下面：
 *     WA2.util     公共小函数（$ / on / clamp…）
 *     WA2.state    跨文件共享的运行时状态（当前角色、当前衣服、音量…）
 *     WA2.chat     对话：请求 / 解析 / 打字机      （待拆）
 *     WA2.sprite   立绘 / 服装 / 场景              （待拆）
 *     WA2.backlog  回想 / 历史                     （待拆）
 *     WA2.audio    BGM / SE / 语音音量             （待拆）
 *
 * 规矩：
 *   1. 新文件只“往 WA2 上加属性”，不改别人的、不覆盖别人的；
 *   2. index.html 里 wt-core.js 必须最先加载；
 *   3. 旧的 IIFE 可以继续用自己的私有变量，等搬完再删；
 *   4. 每个功能文件用 WA2.register("名字", 对象) 登记自己，便于探针检查。
 */
(function () {
  var WA2 = window.WA2 = window.WA2 || {};

  WA2.version = "2026-11-04";

  /* 公共小函数：以后各文件别再各写一份 */
  WA2.util = WA2.util || {
    $: function (id) { return document.getElementById(id); },
    $$: function (sel, root) {
      return Array.prototype.slice.call((root || document).querySelectorAll(sel));
    },
    on: function (el, ev, fn, opt) {
      if (el) el.addEventListener(ev, fn, opt);
      return el;
    },
    clamp: function (v, a, b) { return v < a ? a : (v > b ? b : v); },
    pad2: function (n) { return (n < 10 ? "0" : "") + n; },
    isNum: function (v) { return typeof v === "number" && isFinite(v); },
    /* 这段文字里有没有假名。判断"是日语原文还是中文字幕"用：
       多角色路径把中文字幕当日语喂给语音模型，就是这么坏掉的。 */
    hasKana: function (s) {
      s = String(s || "");
      for (var i = 0; i < s.length; i++) {
        var c = s.charCodeAt(i);
        if (c >= 0x3040 && c <= 0x30ff) return true;
      }
      return false;
    }
  };

  /* 跨文件共享状态：只放“多个文件都要读”的东西 */
  WA2.state = WA2.state || {
    character: null,     // 当前角色 id（切换后由 special.js 更新）
    outfit: "",          // 当前衣服名
    emotion: "calm",     // 当前表情
    scene: "",           // 当前场景
    volume: { bgm: 100, voice: 100, se: 100 }
  };

  /* 功能模块登记处：探针靠它检查“拆出来的文件到底加载了没” */
  WA2.features = WA2.features || {};
  WA2.register = function (name, obj) {
    WA2.features[name] = obj || true;
    WA2[name] = obj || WA2[name];
  };

  /* DOM 就绪（各文件都可用，省得各自写一遍） */
  WA2.ready = function (fn) {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", fn);
    } else {
      fn();
    }
  };

  /* 给控制台/探针一个一眼能看的状态摘要 */
  WA2.summary = function () {
    return {
      version: WA2.version,
      features: Object.keys(WA2.features),
      state: WA2.state,
      ready: document.readyState
    };
  };
})();
