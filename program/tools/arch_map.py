# -*- coding: utf-8 -*-
"""扫描源码，生成「架构映射表」——层级 / 文件 / 职责 / 导出 / 依赖 / 被谁调用。

规则：**这张表由工具生成，不要手改**；改完代码跑一次，表就跟着更新。

    python tools/arch_map.py                 # 打印
    python tools/arch_map.py --write         # 写入 other/docs/架构/映射表.md
    python tools/arch_map.py --check         # 与已提交的表对比，不一致就退出码 1（给 CI/自测用）

层级定义见 LEVELS：路径/文件名 → 一级 / 二级 / 三级。
"""
import argparse
import ast
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # program/
SRC = os.path.join(ROOT, "src")
OUT = os.path.join(ROOT, "..", "other", "docs", "架构", "映射表.md")

# 一级/二级归属（按文件名）。加新文件一定要在这里登记，否则表里显示「未登记」。
LEVELS = {
    "main.py": ("一级 · 后端服务", "主入口（HTTP 服务 + 窗口 + 启动）"),
    "text_parse.py": ("二级 · 核心", "回复标签解析、日文清理（纯函数）"),
    "applog.py": ("二级 · 核心", "日志文件"),
    "diag.py": ("二级 · 核心", "调试面板的只读状态账本（/api/debug/state）"),
    "logpack.py": ("二级 · 核心", "日志按天滚动 + 诊断包导出（脱敏）"),
    "perf.py": ("二级 · 核心", "性能采样：logs/perf-<日期>.csv（内存/句柄/缓存）"),
    "perfcfg.py": ("二级 · 核心", "每角色演出 / 切片规则（performance.json / persona_slices.json）"),
    "hotreload.py": ("二级 · 核心", "素材热重载：盯目录 → 失效缓存 → 前端重拉（可关）"),
    "params.py": ("二级 · 核心", "关键参数登记表（生成 other/docs/参数与指标.md）"),
    "secret.py": ("二级 · 核心", "密钥来源（环境变量优先）与日志脱敏"),
    "winutil.py": ("二级 · 核心", "Windows 窗口 API"),
    "pricing.py": ("二级 · 核心", "价格与用量估算"),
    "memory.py": ("二级 · 记忆", "对话记忆书：阈值/打包/条目"),
    "standing.py": ("二级 · 记忆", "常驻记忆书两段化 + 随阶段低频刷新"),
    "persona_ctx.py": ("二级 · 记忆", "人设正文、世界书命中、时期"),
    "persona_stage.py": ("二级 · 记忆", "关系阶段分类"),
    "story_rag.py": ("二级 · 记忆", "原作检索（BM25）"),
    "sessions.py": ("二级 · 存档", "存读档读写"),
    "char_cards.py": ("二级 · 角色", "角色卡 / 主角卡读写与校验（导演卡缺失不许进多角色）"),
    "cast.py": ("二级 · 角色", "本场阵容：谁在场、谁是主角、龙套；连接状态汇总"),
    "director.py": ("二级 · 剧情", "导演骨架：幕/拍计划、导演提示词、占位拍（暂不接模型）"),
    "playqueue.py": ("二级 · 剧情", "播放队列（后端内存）：入队/取一条/清空/快照"),
    "actor.py": ("二级 · 剧情", "角色开口：导演给意图，角色自己的模型写一句日语台词"),
    "characters.py": ("二级 · 角色", "角色文件夹/切换/立绘索引"),
    "char_res.py": ("二级 · 角色", "角色资源定位与状态"),
    "char_pack.py": ("二级 · 角色", "角色包导出/导入（zip）"),
    "special_files.py": ("二级 · 角色", "特别模式导入/打开/立绘落盘"),
    "special_cfg.py": ("二级 · 角色", "角色名读写"),
    "tts_client.py": ("二级 · 角色", "语音合成与参考音频"),
    "audio_gain.py": ("二级 · 音频", "响度归一化（BS.1770 近似，记增益不改原文件）"),
    "scene_cg.py": ("二级 · 场景", "场景 CG 标签与匹配"),
    "sub_render.py": ("二级 · 字模", "中文→字体图集渲染"),
    "api_window.py": ("二级 · 接口/窗口", "窗口控制 /api/window"),
    "api_char.py": ("二级 · 接口/角色", "角色查询与切换后处理"),
    "api_modelcfg.py": ("二级 · 接口/模型", "/api/modelcfg"),
    "api_tts.py": ("二级 · 接口/语音", "/api/tts 语音合成"),
    "api_text.py": ("二级 · 接口/字模", "/api/text 字模渲染"),
    "api_chat.py": ("二级 · 接口/对话", "/api/chat 对话入口"),
    "api_charpack.py": ("二级 · 接口/角色", "角色包导出/导入 /api/charpack/*"),
    "api_memory.py": ("二级 · 接口/记忆", "记忆书查看与删除 /api/memory*"),
    "api_update.py": ("二级 · 接口/更新", "版本检查 /api/update、打开链接 /api/open_url"),
    "api_log.py": ("二级 · 接口/日志", "诊断包导出 /api/log/bundle"),
    "api_perfcfg.py": ("二级 · 接口/配置", "/api/performance（每角色演出 / 切片规则）"),
    "api_hotreload.py": ("二级 · 接口/素材", "/api/hotreload（素材热重载状态与开关）"),
    "api_wizard.py": ("二级 · 接口/素材", "/api/assets/wizard（素材导入向导数据）"),
    "version.py": ("二级 · 核心", "版本号与 GitHub 仓库地址"),
    "wt-update.js": ("二级 · 前端/更新", "启动查新版本 + 标题栏通知（WA2.update）"),
    "gdi_text.py": ("二级 · 字模", "GDI 兜底渲染"),
}

UI_LEVELS = {
    "wt-core.js": ("二级 · 前端/核心", "window.WA2 命名空间、公共小函数、功能登记"),
    "wt-audio.js": ("二级 · 前端/音频", "BGM / SE / 语音音量（WA2.audio）"),
    "wt-fade.js": ("二级 · 前端/演出", "双缓冲交叉淡化：背景/立绘（WA2.fade）"),
    "wt-perfcfg.js": ("二级 · 前端/演出", "每角色演出配置（/api/performance，WA2.perfCfg）"),
    "wt-hotreload.js": ("二级 · 前端/演出", "热重载轮询：素材变了 → 版本号 + 重画（WA2.hotReload）"),
    "asset-wizard.js": ("二级 · 前端/界面", "素材检查 / 导入向导（菜单「环境设置 → 素材检查」）"),
    "wt-sprite.js": ("二级 · 前端/立绘", "立绘/服装/表情（WA2.sprite）"),
    "wt-voice.js": ("二级 · 前端/语音", "TTS 播放与参考音频（WA2.voice）"),
    "wt-text.js": ("二级 · 前端/文字", "分页/打字机/翻页图标（WA2.text）"),
    "wt-backlog.js": ("二级 · 前端/回想", "对话历史与回想（WA2.backlog）"),
    "wt-choices.js": ("二级 · 前端/选项", "选择分支选项条：/api/choice → 字模 + 贴图五层（WA2.choices）"),
    "wt-chat.js": ("二级 · 前端/对话", "发送与分析回复（WA2.chat）"),
"wt-auto.js": ("前端", "自动续说"),
    "wt-session.js": ("二级 · 前端/存档", "存读档与缩略图（WA2.session）"),
    "wt-cost.js": ("二级 · 前端/费用", "token/费用显示（WA2.cost）"),
    "wt-mood.js": ("二级 · 前端/情绪", "情绪判断与 BGM 情绪（WA2.mood）"),
    "wt-gdi.js": ("二级 · 前端/渲染", "字模渲染兜底（WA2.gdi）"),
    "wt-nav.js": ("二级 · 前端/导航", "标题/退出/思考条（WA2.nav）"),
    "opt-widgets.js": ("二级 · 前端/设置控件", "设置页页签内容（WA2.optWidgets）"),
    "opt-model.js": ("二级 · 前端/设置模型", "模型/连接配置（WA2.optModel）"),
    "sp-sliders.js": ("二级 · 前端/特别参数", "滑条与配置读写（WA2.spSliders）"),
    "sp-char.js": ("二级 · 前端/特别角色", "角色切换/新建（WA2.spChar）"),
    "sp-pack.js": ("二级 · 前端/特别角色", "角色包导出/导入按钮（WA2.spPack）"),
    "mem-screen.js": ("二级 · 前端/记忆书", "记忆书界面：查看 + 删除（WA2.memoryScreen）"),
    "sp-panels.js": ("二级 · 前端/特别面板", "角色面板/立绘/语音（WA2.spPanels）"),
    "game.js": ("一级 · 前端/对话", "对话演出：请求/解析/打字机/立绘/回想/音量"),    "mb-cast.js": ("一级 · 前端/标题栏", "多角色控制台：连接状态/阵容/队列/开关/长度"),

    "menubar.js": ("一级 · 前端/菜单", "顶部菜单：场景/服装/时期/窗口"),
    "special.js": ("一级 · 前端/特别模式", "参数滑条/角色/语音/世界书"),
    "opt.js": ("一级 · 前端/设置", "模型/连接/选项"),
    "screens.js": ("二级 · 前端/壳", "界面开关、右键/ESC、通用退出"),
    "savepages.js": ("二级 · 前端/存档", "存档网格/缩略图/页签"),
    "savetabs4.js": ("二级 · 前端/存档", "存档页签资源"),
    "uitext.js": ("二级 · 前端/字模", "中文换字模图"),
    "sound.js": ("二级 · 前端/音频", "SE/BGM 播放"),
    "zoom.js": ("二级 · 前端/窗口", "缩放"),
    "scale.js": ("二级 · 前端/窗口", "整数倍缩放"),
    "debug.js": ("二级 · 前端/调试", "调试面板"),
    "config.js": ("二级 · 前端/配置", "前端配置常量"),
}


def py_info(path):
    src = open(path, encoding="utf-8", errors="replace").read()
    info = {"lines": src.count("\n") + 1, "deps": set(), "exports": [], "doc": ""}
    try:
        t = ast.parse(src)
    except SyntaxError:
        return info
    doc = ast.get_docstring(t) or ""
    info["doc"] = doc.strip().splitlines()[0] if doc.strip() else ""
    for n in t.body:
        if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and not n.name.startswith("_"):
            info["exports"].append(n.name)
        elif isinstance(n, ast.Import):
            info["deps"].update(a.name.split(".")[0] for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module:
            info["deps"].add(n.module.split(".")[0])
    local = {os.path.splitext(f)[0] for f in os.listdir(SRC) if f.endswith(".py")}
    info["deps"] = sorted(d for d in info["deps"] if d in local)
    return info


def js_info(path):
    src = open(path, encoding="utf-8", errors="replace").read()
    info = {"lines": src.count("\n") + 1, "deps": [], "exports": [], "doc": ""}
    head = re.search(r"^\s*(?://|/\*)\s*(.+)$", src, re.M)
    info["doc"] = head.group(1).strip()[:60] if head else ""
    names = re.findall(r"window\.(__\w+)\s*=", src)[:4] + re.findall(r"^\s*function\s+(\w+)", src, re.M)[:4]
    info["exports"] = sorted(set(names))
    info["deps"] = ["<script 全局>"]
    return info


def build():
    rows = []
    for dirpath, _dn, fns in os.walk(SRC):
        for fn in sorted(fns):
            p = os.path.join(dirpath, fn)
            rel = os.path.relpath(p, ROOT).replace("\\", "/")
            if fn.endswith(".py") and dirpath == SRC:
                info = py_info(p)
                lvl, duty = LEVELS.get(fn, ("未登记", "—"))
                rows.append((lvl, rel, info, duty))
            elif fn.endswith(".js") and os.path.basename(dirpath) == "ui":
                info = js_info(p)
                lvl, duty = UI_LEVELS.get(fn, ("未登记", "—"))
                rows.append((lvl, rel, info, duty))
    # 被谁调用：反向依赖（仅 py 内部；js 是全局脚本，不建图）
    users = {}
    for _l, rel, info, _d in rows:
        mod = os.path.splitext(os.path.basename(rel))[0]
        for other in rows:
            om = os.path.splitext(os.path.basename(other[1]))[0]
            if mod in other[2]["deps"]:
                users.setdefault(mod, []).append(om)
    return rows, users


def render(rows, users):
    out = ["# 架构映射表（自动生成，不要手改）", "",
           "> 由 `program/tools/arch_map.py --write` 生成。改了代码就重跑，表会跟着变。",
           "> 代理用法：按「层级」找到要改的那一行 → 只读该文件；关注「被谁调用」列判断影响面。", ""]
    order = {}
    for lvl, _rel, _i, _d in rows:
        order.setdefault(lvl, 0)
        order[lvl] += 1
    for lvl in sorted(order, key=lambda x: (x.startswith("未"), x)):
        out += ["## " + lvl, "",
                "| 文件 | 行数 | 职责 | 导出 | 依赖 | 被谁调用 |", "|---|---|---|---|---|---|"]
        for l, rel, info, duty in rows:
            if l != lvl:
                continue
            mod = os.path.splitext(os.path.basename(rel))[0]
            exp = ", ".join(info["exports"][:6]) or "—"
            dep = ", ".join(info["deps"][:6]) or "—"
            use = ", ".join(sorted(set(users.get(mod, [])))[:6]) or "—"
            out.append("| `%s` | %d | %s | %s | %s | %s |" % (rel, info["lines"], duty, exp, dep, use))
        out.append("")
    big = sorted(rows, key=lambda r: -r[2]["lines"])[:8]
    out += ["## 待拆（行数最多的 8 个）", "", "| 文件 | 行数 | 建议 |", "|---|---|---|"]
    for _l, rel, info, _d in big:
        n = info["lines"]
        tip = "超过 800 行，必须拆" if n > 800 else ("超过 400 行，建议拆" if n > 400 else "可接受")
        out.append("| `%s` | %d | %s |" % (rel, n, tip))
    out.append("")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    rows, users = build()
    text = render(rows, users)
    if a.write:
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        open(OUT, "w", encoding="utf-8", newline="").write(text)
        print("已写入 %s（%d 个文件）" % (OUT, len(rows)))
    elif a.check:
        old = open(OUT, encoding="utf-8").read() if os.path.exists(OUT) else ""
        if old != text:
            print("映射表过期：跑 python tools/arch_map.py --write")
            return 1
        print("映射表是最新的")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
