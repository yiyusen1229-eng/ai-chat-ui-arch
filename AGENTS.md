# AGENTS.md · 给 AI 代理的接手须知

**先读 `other/docs/交接速览.md`（1 页）。** 需要细节再看 `other/docs/新对话交接说明.md`（32 KB，别整份读：
用 `python tools/digest.py --outline <file>` 看目录，再按节读）。

## 项目是什么

桌面聊天程序：Python 本地 HTTP 服务 + WebView2 无边框窗口，类galgame 界面，
对话走云端 LLM，语音走本地 GPT-SoVITS。角色 = `characters/<角色>/` 一个文件夹（自包含）。

## 架构地图（别一次读整个项目）

`other/docs/架构/` 共 8 份，每份 1.5～3 KB，**按任务只读对应的那一份**：

| 改什么 | 读哪份 |
|---|---|
| 加/改 HTTP 接口 | `01_后端与接口.md` |
| system 拼装、聊天流程 | `02_对话管线.md` |
| 记忆书 / 世界书 / 人设 / 检索 | `03_记忆与世界书.md` |
| 角色文件夹 / 立绘 / 语音资源 | `04_角色与资源.md` |
| 界面、按钮、演出 | `05_前端界面.md` |
| 工具、自测、发布 | `06_工具自测与发布.md` |
| 存档与各种 json 格式 | `07_文件与格式.md` |

入口：[`other/docs/架构/00_总览.md`](other/docs/架构/00_总览.md)。

## 常用命令

```powershell
program\启动程序.bat                                  # 启动（同步 src → build_env 再开窗）
python program\tools\run_smoke.py                     # 一键自测（约 5 秒，含在线冒烟）
python program\tools\run_dev.py --sync-only           # 只同步代码到 build_env
cd program\build_env; $env:WA2_ASSETS_DIR="..\res\assets"
.\venv\Scripts\python.exe main.py --serve-only --port 8791   # 只起后端（自测/探针用）
python program\tools\ui_probe.py --url http://127.0.0.1:8791/game/index.html `
    --js setup.js --check check.js --wait 1500        # 界面探针（--js/--check 收文件路径）
python program\tools\check_assets.py                  # 素材自检
```

## 省钱规则（每次任务都适用）

1. **不要输出截图/图片**（用户 2026-11-04 明确交代）。界面问题看探针返回的 JSON 数字
   （节点数、高度、能否滚动、`naturalWidth>0`、报错列表）；只有用户明确要求“给我看图”才 `--shot`。
2. **不要整份读文件**：先 `tools/digest.py --outline`，再只读需要的那几节。
3. **diff 先 `--stat`**，确认涉及哪些文件，再看单个文件的 diff。
4. **按架构地图定位，只读一层**：`other/docs/架构/00_总览.md` → 找到任务对应的叶子文档 → 只读那一份。
5. **一任务一会话**：新会话只喂 `交接速览.md` + 本次任务，不要把历史对话贴回来。
6. **机械活交给便宜模型**：改名、批量替换、跑测试、跑探针。

## 代码落位规矩（硬性）

1. **目录即层级**：新代码放进 `core/`（稳定原语）、`chat/`（对话管线）、`api/`（HTTP 分组）、
   `character/`（角色与资源）、`ui/`（前端按功能）——不要往 `main.py` 里继续加。
2. **单文件上限 400 行**：超过就要拆；超过 800 行的（`main.py`、`game.js`、`opt.js`）已在待拆清单。
3. **新文件必须登记**到 `program/tools/arch_map.py` 的 `LEVELS` / `UI_LEVELS`，否则映射表显示「未登记」。
4. **改完代码跑** `python program/tools/arch_map.py --write`；提交前 `--check` 必须通过。
5. **动过前端模块就跑** `python program/tools/check_split.py`：同名全局覆盖、丢语句、
   未定义引用、重复语句、漏搬函数，五类坑一次扫出来。
6. **只读一层**：先看 `other/docs/架构/映射表.md` 的「层级 / 被谁调用」两列，再决定读哪个文件。

## 文件命名（硬性）

- 新增的日志 / 补丁 / 快照：`名称_YYYY-MM-DD_HH-MM-SS.后缀`（例如 `更新日志_2026-11-04_01-55-30.md`）
- 已发布过的文件不改名（避免断链）
- 补丁放在 `other/docs/patches/`，日志放在 `other/docs/`
- **每次发布都要 bump 版本号**：`version.json`（仓库根）与 `program/src/version.py` 的
  `VERSION` 保持一致（格式 `YYYY-MM-DD.N`），否则客户端查不到更新

## 不许动的东西

- `characters/`、`res/assets/`、`persona/`、`worldbook/`、`sessions/`、`release/` 是本地数据，**不入库、不上传**。
- 世界书结构不改；人设/记忆书内容只留本机，文档里不抄设定细节。
- 发布流程见 `新对话交接说明.md` 第11、12、13 条（先整合旧补丁再发新补丁，旧补丁保留）。
- **改动前先看 `other/docs/不要动.md`**：15 条「碰不得 + 动了会怎样 + 正确改法」，每条都是踩过的坑
  （模块身份 `__main__`、像素系数 0.625、`window.__history` 同一个数组、hotreload 跳过清单、
  选择条不能实时叠 plus-lighter、`WA2.prefs` 不存在、改完 src 要 run_dev --sync-only …）。
- 想知道「一次对话经过哪些模块、缓存落在哪」→ `other/docs/架构/00b_数据流.md`。
- 想做/改**角色包**（导出、导入、给别人发角色）→ `other/docs/角色包规范.md`
  （格式、脱敏规则、大小上限、`tools/char_pack_migrate.py --check/--fix/--from-dir`，还有一个 2.2 KB 的示例包）。
