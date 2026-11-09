# -*- coding: utf-8 -*-
"""开发运行：把 src/ 同步到 build_env/ 并直接用 python 启动（改完代码立刻生效）

program/  = 实时程序（源码 + 这个入口）
release/  = 打包好的分发版本
"""
import io
import os
import shutil
import subprocess
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(BASE, "src")
RES = os.path.join(BASE, "res")
ENV = os.path.join(BASE, "build_env")
PY = os.path.join(ENV, "venv", "Scripts", "python.exe")

# 根目录的单文件
DATA = ("story_index.json", "fewshot.json")
# 各自一个文件夹的数据（人设 / 世界书）
DATA_DIRS = ("persona", "worldbook")

# 1. 同步后端代码与数据
n = 0
for f in os.listdir(SRC):
    if f.endswith(".py") or f in DATA:
        shutil.copy2(os.path.join(SRC, f), os.path.join(ENV, f))
        n += 1
for d in DATA_DIRS:
    s = os.path.join(SRC, d)
    if os.path.isdir(s):
        shutil.copytree(s, os.path.join(ENV, d), dirs_exist_ok=True)
        n += 1
# 人设 / 世界书已经迁到子文件夹：删掉根目录的旧副本，免得两份同时存在
for legacy in ("persona.txt", "persona_core.txt", "worldbook.json"):
    p = os.path.join(ENV, legacy)
    if os.path.exists(p):
        try:
            os.remove(p)
            print("  清掉旧位置的 %s" % legacy)
        except OSError:
            pass
print("  同步 %d 个后端文件 → build_env/" % n)

# 2. 组装前端（src/ui + res/assets → build_env/ui2）
ui2 = os.path.join(ENV, "ui2")
shutil.rmtree(ui2, ignore_errors=True)
os.makedirs(ui2, exist_ok=True)
for f in os.listdir(os.path.join(SRC, "ui")):
    shutil.copy2(os.path.join(SRC, "ui", f), os.path.join(ui2, f))
print("  组装 ui2 完成（素材不再复制，运行时直接读 res/assets）")

# 2.5 换缓存版本号：不换的话 WebView2 会继续用旧脚本，改了代码看不到效果
try:
    import re
    import time
    _idx = os.path.join(ui2, "index.html")
    _stamp = time.strftime("%Y%m%d%H%M%S")
    _html = io.open(_idx, encoding="utf-8").read()
    _html, _n = re.subn(r"\?v=[0-9A-Za-z]+", "?v=" + _stamp, _html)
    io.open(_idx, "w", encoding="utf-8").write(_html)
    print("  缓存版本号 → ?v=%s（%d 处）" % (_stamp, _n))
except Exception as _e:
    print("  换版本号失败（不影响运行）:", _e)

# 只同步不启动（自测 / 打包前刷新用）
if "--sync-only" in sys.argv:
    print("  --sync-only：同步完成，不启动")
    sys.exit(0)

# 3. 启动
print("  启动程序…")
env = os.environ.copy()
env["WA2_ASSETS_DIR"] = os.path.join(RES, "assets")   # 界面素材直接读 res/
subprocess.Popen([PY, "main.py"], cwd=ENV, env=env)
