/* wt-mood.js 的 BGM 槽位自测（纯 Node，不开界面、不连模型）。
 *
 *   cd program
 *   node tools/test_mood_bgm.js
 *
 * 为什么专门测这个：2026-11-06 用户反馈「BGM 只在寂静的冬夜和冰之刃之间切，
 * 而冰之刃应该在气氛沉重（分手/争执）时才出来」。
 * 实测（本机 8 个存档、476 句真实台词）根因两条：
 *   ① 用正则判情绪，把她的日常嘴硬（笨蛋/哼/少来）算成 angry → 冰之刃；
 *   ② 累积衰减 0.65 太黏，calm 占八成时 warm/sad 永远翻不了身（476 句只切 2 次）。
 * 现在改成：优先用模型给的 <emotion>（毒舌/问句算日常、沉重才算争执），
 * 没有标签才回退文本规则；衰减 0.5、领先 1.5 分且连续两轮才切。
 */
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const CODE = fs.readFileSync(path.join(__dirname, "..", "src", "ui", "wt-mood.js"), "utf8");
const fails = [];

function check(name, cond, extra) {
  console.log((cond ? "  ok   " : "  FAIL ") + name +
    (cond ? "" : "  " + JSON.stringify(extra)));
  if (!cond) fails.push(name);
}

/* 起一个模块实例：setBgm 只记录，不碰音频 */
function boot() {
  const played = [];
  const sandbox = {
    window: { WA2: { register: function () {} }, App: { getPrefs: () => ({}) } },
    console: console, Math: Math, parseInt: parseInt, setTimeout: setTimeout
  };
  sandbox.setBgm = function (name) { played.push(name); sandbox.window.WA2.audio.current = name; };
  vm.createContext(sandbox);
  vm.runInContext(CODE, sandbox);
  sandbox.window.WA2.audio = { current: "calm", enabled: true };
  return { mood: sandbox.window.WA2.mood, played: played,
           cur: function () { return sandbox.window.WA2.audio.current; } };
}

console.log("[1] 模型 <emotion> → BGM 槽位");
{
  const { mood } = boot();
  const cases = [
    ["沉重", "tense"], ["毒舌", "calm"], ["问句", "calm"], ["calm", "calm"],
    ["soft", "warm"], ["撒娇", "warm"], ["调侃吐槽", "comic"], ["哭腔", "sad"]
  ];
  for (const [emo, want] of cases) {
    check(emo + " → " + want, mood.bgmSlot("随便一句", emo) === want, mood.bgmSlot("随便一句", emo));
  }
}

console.log("[2] 没有标签时的回退规则（嘴硬不再算沉重）");
{
  const { mood } = boot();
  check("「我们分手吧」→ tense", mood.bgmSlot("我们分手吧") === "tense", mood.bgmSlot("我们分手吧"));
  check("「笨蛋，别管我」→ calm（日常嘴硬）",
        mood.bgmSlot("笨蛋，别管我") === "calm", mood.bgmSlot("笨蛋，别管我"));
  check("「哼，少来」→ calm", mood.bgmSlot("哼，少来这一套") === "calm", mood.bgmSlot("哼，少来这一套"));
  check("「对不起」→ sad", mood.bgmSlot("对不起……") === "sad", mood.bgmSlot("对不起……"));
  check("「早点睡」→ warm", mood.bgmSlot("早点睡吧，别熬夜") === "warm", mood.bgmSlot("早点睡吧，别熬夜"));
  check("空字符串 → calm", mood.bgmSlot("") === "calm", mood.bgmSlot(""));
}

console.log("[3] 切换：要连续两轮才换，不随单句抽风");
{
  const { mood, played } = boot();
  mood.reset();
  mood.accumulateMood("早点睡吧", "warm");
  check("单句温柔不切", played.length === 0, played);
  mood.accumulateMood("别勉强自己", "warm");
  check("连续两句温柔 → warm", played[played.length - 1] === "warm", played);
}

console.log("[4] 争执才会切到 tense（冰之刃）");
{
  const { mood, played } = boot();
  mood.reset();
  mood.accumulateMood("我们到此为止吧", "沉重");
  mood.accumulateMood("别再联系了", "沉重");
  check("连续两句沉重 → tense", played[played.length - 1] === "tense", played);
}

console.log("[5] 日常嘴硬不会把 BGM 拉去 tense");
{
  const { mood, played } = boot();
  mood.reset();
  for (let i = 0; i < 6; i++) {
    mood.accumulateMood("笨蛋，吵死了，别管我", "毒舌");
  }
  check("六句毒舌都不切到 tense", played.indexOf("tense") < 0, played);
}

console.log("[6] reset() 只清氛围分，不负责换歌");
{
  const { mood, played, cur } = boot();
  mood.reset();
  mood.accumulateMood("我们分手吧", "沉重");
  mood.accumulateMood("别再联系了", "沉重");
  const was = cur();
  const n = played.length;
  mood.reset();                       /* 分数清零，但歌继续放（换歌由开新游戏那边 setBgm） */
  check("reset 不主动换歌", cur() === was && played.length === n, { was, now: cur() });
  mood.accumulateMood("早点睡吧", "warm");
  check("reset 后单句仍不切（分数确实清零了）", played.length === n, played);
  mood.accumulateMood("别勉强自己", "warm");
  check("reset 后连续两句才切", played[played.length - 1] === "warm", played);
}

console.log();
if (fails.length) {
  console.log("失败 " + fails.length + " 项：" + fails.join("、"));
  process.exit(1);
}
console.log("全部通过");
