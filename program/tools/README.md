# tools/

维护脚本。除 `run_dev.py`（`启动程序.bat` 调用）外，都是**手动运行**的，不参与程序启动。

| 脚本 | 作用 | 什么时候用 |
|---|---|---|
| `run_dev.py` | 同步 `src/` → `build_env/` 并启动程序；`--sync-only` 只同步，`--watch` 改完自动同步 + 重启 | 每次开发运行（被 bat 调用） |
| `ui_probe.py` | 用真实 WebView2 打开界面、跑 JS、截图 | 改完界面要验证「JS 真的跑了」 |
| `ui_snap.py` | 六个界面逐元素样式指纹 + 截图，`--diff` 对比两次 | 改 CSS 前后的回归检查 |
| `test_memory.py` | 记忆书单测（估算 / 裁剪 / 打包 / 合并） | 改记忆书逻辑 |
| `test_memory_flow.py` | 记忆书端到端（读档复制 → 缓存 → 存档快照 → 6 轮重打包） | 改记忆书流程 |
| `test_char_res.py` | 角色资源接线单测（提示词/世界书/记忆书/参考音频/立绘表情） | 改 `char_res` / `special_files` / `tts_client` |
| `test_char_wiring.py` | 资源接线端到端（假模型，断言 system 内容） | 改人设/世界书/记忆书注入 |
| `test_actor_ctx.py` | 上下文隔离：数据目录 / scenelog / speaker.ctx / 角色提示词里的记忆·原话·关系·红线 / 导演提示词 | 改 `actor` / `speaker` / `scenelog` / `datadir` / `director_gen` |
| `test_run_dev.py` | 开发入口：只看代码文件、改/增/删都认得出、窗口关了会退出、子进程收得干净 | 改 `run_dev.py` |
| `test_tts_prefetch.py` | 预合成：开关 / 只排下两句 / 参数与真播一致 / 去重与清空 / 失败不扩散 / 队列上限 | 改 `tts_prefetch` / `playqueue` |
| `test_persona_stage.py` | 关系阶段：云端分类 / 到顶封顶 / 失败兜底 | 改 `persona_stage.py` |
| `test_scene_cg.py` | 场景 CG 匹配单测 | 改 `scene_cg.py` |
| `import_cg.py` | 场景图 → `res/assets/cg/*.webp` + `index.json` | 换/加场景图 |
| `import_voice_refs.py` | 语音素材 → `<角色>/voice/<情绪>/*.wav` + prompt_text（剧本原文或 ASR） | 导入/整理参考音频 |
| `test_import_cg.py` | 导入/匹配回归（增量导入不冲旧目录、已有的地点不被别名抢） | 改 `import_cg.py` / `scene_cg.match` |
| `test_scene_judge.py` | 场景判定器单测（大致匹配/不变/胡答/关思考/完整场景名） | 改 `_judge_scene` 或场景提示词 |
| `build_story_index_v2.py` | 从 `other/dev/dataset/L2_scene.jsonl` 重建 `src/story_index.json.gz` | 换了剧本数据 |
| `bake_ui_text.py` | 固定文案 → `res/assets/ui/text/*.png` | 改界面文案 |
| `bake_cost_ui.py` | 花费栏字模（数字/字母单独排版） | 改花费栏 |
| `bake_page_cursor.py` | 对话框末尾的翻页图标（原版 page_02 的 120 帧 → 横向精灵图） | 换图标 / 重新烤素材 |
| `rename_sprites.py` | 立绘改名：编号目录 / kaz 文件名 → 中文衣服名 / 表情名，并重写 index.json | 整理立绘命名 |
| `make_special_ui.py` | 生成特别模式用图：滑条/恢复按钮/配置项底框与箭头 | 换素材 / 缺图 |
| `check_assets.py` | 素材自检（缺什么、该放哪） | 换机器 / 缺素材 |
| `preview_assets.py` | 预览 tga/bmp 素材与坐标 | 调界面坐标 |
| `setup_assets.py` | 下载可自动获取的模型依赖 | 首次部署 |
| `build_exe.py` | PyInstaller 打包（onefile） | 发版 |
| `restore_cloud_models.py` | 角色聊天模型在「本机 / 云端」之间一键切换（备份在 `characters/<角色>/model.json.cloud.bak`） | 换回云端 / 再切本地 |
| `build_portable.py` | 绿色版目录（onedir）+ zip | 发版 |
| `归档/` | 一次性分析脚本、旧工具（`gdi_proxy`、`llama_cpp`、占位素材生成器） | 一般不用，别删 |

## 自测：ui_probe.py

改前端必须验证**执行路径**，不是「文件里有这行代码」。这个探针开一个真的 WebView2
窗口（和正式程序同一个引擎），可以用 JS 点按钮、读状态，还能截图。

```powershell
# 终端 1：起后端（日志里会打印端口）
cd program\build_env; .\venv\Scripts\python.exe main.py --serve-only

# 终端 2：跑一段 JS 并截图
python tools\ui_probe.py --url http://127.0.0.1:8771/game/index.html `
    --js setup.js --check check.js --wait 1200 --shot out.png
```

- `--js` 打开后先执行（点按钮、改状态）
- `--wait` 等多久（给 fetch / 动画留时间）
- `--check` 等待后再执行一次（读结果）
- `--shot` 截图（Win32 PrintWindow，抓窗口真实画面）

两个 JS 文件都以 `return <可 JSON 化的值>` 结尾，结果会打印出来。
坑：同一段 JS 里**改完 class 立刻读 `getComputedStyle().opacity` 会拿到过渡前的值**，
要量结果就放到 `--check` 里，等过渡跑完再读。

## 自测：ui_snap.py（CSS 回归）

```powershell
python tools\ui_snap.py --url http://127.0.0.1:8771/game/index.html --out snap_before
# 改 CSS 后
python tools\ui_snap.py --url http://127.0.0.1:8771/game/index.html --out snap_after
python tools\ui_snap.py --diff snap_before snap_after
```

它会把标题/游戏/读档/回想/设置/特别模式六个界面逐元素 `getComputedStyle` 哈希，再截图；
对比时同时看样式和像素。为了可重复，它禁用了动画、隐藏了标题栏与飘雪、把鼠标挪到角落。

## 约定

- 脚本一律从 `program/` 为根做相对路径，不要写死绝对路径
- 会写文件的脚本，输出都落在 `res/` 或 `cache/` 下，不要污染 `src/`
