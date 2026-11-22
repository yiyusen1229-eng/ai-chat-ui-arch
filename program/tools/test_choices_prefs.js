/* wt-choices.js 的自测（纯 Node，不开界面）。
 *
 * 跑法：
 *   cd program
 *   node tools/test_choices_prefs.js
 *
 * 为什么专门测这个：2026-11-06 用户反馈「不管设置里改成几，出现的都是两个」。
 * 原因是 `prefsCount()` 去读 `WA2.prefs.getPrefs()` —— 而**根本没有 `WA2.prefs`**，
 * 设置模块的入口是 `window.App.getPrefs/setPref`。读不到就永远用默认 2，
 * 于是后端每次都被要求给 2 个。
 *
 * 2026-11-22 加：同一个对话点不重复生成 / 数量改大只补差额。
 * 用假 DOM + 假 fetch 把 open() 真跑一遍，数「到底发了几次 /api/choice、带没带 have」。
 */
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const code = fs.readFileSync(path.join(__dirname, "..", "src", "ui", "wt-choices.js"), "utf8");

const fails = [];
function check(name, cond, extra) {
  console.log((cond ? "  ok   " : "  FAIL ") + name + (cond ? "" : "  " + JSON.stringify(extra)));
  if (!cond) fails.push(name);
}

/* 极简 DOM：只够 wt-choices.js 建 .choiceRow 用；getElementById 记在登记表里 */
function makeDoc() {
  const reg = {};
  function El(tag) {
    this.tagName = tag;
    this.style = {};
    this.children = [];
    this._html = "";
    this.classList = { add: function () {}, remove: function () {}, toggle: function () {} };
    this.appendChild = function (c) { this.children.push(c); return c; };
    this.insertBefore = function (c) { this.children.push(c); return c; };
    this.addEventListener = function () {};
    this.setAttribute = function () {};
    this.querySelector = function () { return new El("span"); };
  }
  Object.defineProperty(El.prototype, "id", {
    get: function () { return this._id || ""; },
    set: function (v) { this._id = v; reg[v] = this; }
  });
  Object.defineProperty(El.prototype, "innerHTML", {
    get: function () { return this._html; },
    set: function (v) { this._html = v; }
  });
  const doc = {
    addEventListener: function () {},
    createElement: function (tag) { return new El(tag); },
    getElementById: function (id) { return reg[id] || null; },
    body: new El("body"),
    head: new El("head"),
    readyState: "complete"
  };
  return doc;
}

/* 假 Image：设 src 之后异步触发 onload，让 render() 的 Promise 能走完 */
function FakeImage() {}
Object.defineProperty(FakeImage.prototype, "src", {
  get: function () { return ""; },
  set: function () {
    const self = this;
    setTimeout(function () { try { self.onload && self.onload(); } catch (e) {} }, 0);
  }
});

function makeFetch(rec) {
  return function (url, opt) {
    const u = String(url || "");
    if (u.indexOf("/api/choice") === 0 && opt && String(opt.method || "").toUpperCase() === "POST") {
      const body = JSON.parse(opt.body || "{}");
      const have = body.have || [];
      const count = body.count || 2;
      const need = Math.max(0, count - have.length);
      const opts = have.slice();
      for (let i = 1; i <= need; i++) opts.push("auto" + opts.length);
      rec.push({ count: count, have: have.slice(), need: need });
      return Promise.resolve({
        ok: true,
        json: function () {
          return Promise.resolve({ ok: true, options: opts, count: opts.length,
                                   have: have.length, generated: need });
        }
      });
    }
    return Promise.resolve({ ok: true, json: function () { return Promise.resolve({}); } });
  };
}

/* 假环境：只给模块加载与 open() 需要的那点东西 */
function load(prefs, opts) {
  opts = opts || {};
  const doc = makeDoc();
  const requests = [];
  const fetchFn = makeFetch(requests);
  const win = {
    document: doc,
    WA2: { register: function () {}, perf: null },
    __history: [],
    toast: function () {},
    setTimeout: setTimeout,
    clearTimeout: clearTimeout,
    Promise: Promise,
    fetch: fetchFn,
    console: console
  };
  if (prefs !== null) {
    win.App = {
      getPrefs: function () { return prefs; },
      setPref: function (k, v) { prefs[k] = v; }
    };
  }
  if (opts.addWa2Prefs) {
    /* 老代码找的那个（永远不存在）也补上，确认我们没退回去读它 */
    win.WA2.prefs = { getPrefs: function () { return { choiceCount: 5 }; } };
  }
  const sandbox = {
    window: win, document: doc, console: console,
    setTimeout: setTimeout, clearTimeout: clearTimeout, Promise: Promise,
    fetch: fetchFn, Math: Math, parseInt: parseInt, String: String, JSON: JSON,
    Image: FakeImage, encodeURIComponent: encodeURIComponent,
    requestAnimationFrame: function (fn) { return setTimeout(fn, 0); }
  };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(code, sandbox);
  const choices = sandbox.window.WA2.choices;
  if (opts.collect) return { choices: choices, win: win, requests: requests };
  return choices;
}

console.log("[1] 读的是 window.App.getPrefs");
let c = load({ choiceCount: 4 });
check("设置 4 个 → prefsCount() = 4", c.prefsCount() === 4, c.prefsCount());

c = load({ choiceCount: 2 });
check("设置 2 个 → 2", c.prefsCount() === 2, c.prefsCount());

c = load({ choiceCount: "5" });
check("字符串 '5' 也认", c.prefsCount() === 5, c.prefsCount());

console.log("[2] 夹取与兜底");
c = load({ choiceCount: 9 });
check("超过 5 → 夹到 5", c.prefsCount() === 5, c.prefsCount());
c = load({ choiceCount: 1 });
check("小于 2 → 夹到 2", c.prefsCount() === 2, c.prefsCount());
c = load({ choiceCount: 0 });
check("0 当没设 → 2", c.prefsCount() === 2, c.prefsCount());
c = load({});
check("没这个键 → 2", c.prefsCount() === 2, c.prefsCount());
c = load(null);
check("App 还没就绪 → 2（不抛异常）", c.prefsCount() === 2, c.prefsCount());

console.log("[3] 不再依赖不存在的 WA2.prefs");
c = load({ choiceCount: 3 }, { addWa2Prefs: true });
check("即使有 WA2.prefs，也以 App.getPrefs 为准（3 而不是 5）", c.prefsCount() === 3, c.prefsCount());

/* ---- 4) 缓存：同一个对话点不重复生成、数量改大只补差额（2026-11-22） ---- */
(async function () {
  console.log("[4] 同一对话点不重复生成 / 数量改大只补差额");
  const prefs = { choiceCount: 2 };
  const env = load(prefs, { collect: true });
  const ch = env.choices;

  await ch.open();
  check("首次打开生成 2 条", ch.state.options.length === 2 && env.requests.length === 1,
        { n: ch.state.options.length, reqs: env.requests.length });
  check("首次请求不带 have", env.requests[0].have.length === 0, env.requests[0]);

  ch.hide();
  await ch.open();
  check("关掉没选、再打开 → 不再请求", env.requests.length === 1 && ch.state.cached === true,
        { reqs: env.requests.length, cached: ch.state.cached });
  check("关掉再打开还是原来那 2 条",
        ch.state.options.join("|") === "auto0|auto1", ch.state.options);

  prefs.choiceCount = 5;
  ch.hide();
  await ch.open();
  check("2→5：只发一次增量请求", env.requests.length === 2, env.requests.length);
  check("2→5：请求 count=5、带 2 条 have、只要 3 条",
        env.requests[1].count === 5 && env.requests[1].have.length === 2 &&
        env.requests[1].need === 3, env.requests[1]);
  check("2→5：5 条且前 2 条不变",
        ch.state.options.length === 5 && ch.state.options[0] === "auto0" &&
        ch.state.options[1] === "auto1", ch.state.options);

  prefs.choiceCount = 2;
  ch.hide();
  await ch.open();
  check("5→2：不请求，只摆前 2 条",
        env.requests.length === 2 && ch.state.options.length === 2, 
        { reqs: env.requests.length, n: ch.state.options.length });
  check("5→2：摆的仍是原来那两条",
        ch.state.options.join("|") === "auto0|auto1", ch.state.options);

  env.win.__history.push({ role: "user", content: "（推进一句）" });
  ch.hide();
  await ch.open();
  check("对话推进后重新生成（have 重新为空）",
        env.requests.length === 3 && env.requests[2].have.length === 0,
        { reqs: env.requests.length, last: env.requests[2] });

  console.log();
  if (fails.length) {
    console.log("失败 " + fails.length + " 项：" + fails.join("、"));
    process.exit(1);
  }
  console.log("全部通过");
})();
