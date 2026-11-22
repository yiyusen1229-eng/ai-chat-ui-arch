# -*- coding: utf-8 -*-
"""场景图导入 / 匹配的自测（不转图、不花 token，纯临时目录）。

钉住两条踩过的坑：
  1. **增量导入不能冲掉旧目录**：以前 build_index 只按 --src 生成，
     导第二批 63 张把原有 119 条的 index.json 写成只剩 63 条
     （2026-11-21 实际发生，用 --rebuild 从 webp 文件名重建恢复）。
  2. **素材里本来就有这个地点时不许被别名抢走**：新的「街道（白天）/（黑夜）」
     和老的「街上」并存，按相似度打分「街上」会先撞线，
     于是新整理的街道图全被老图盖掉。

跑法（在 program/ 下）：
    build_env/venv/Scripts/python.exe tools/test_import_cg.py
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "src"))
sys.path.insert(0, os.path.join(HERE, "tools"))

import import_cg  # noqa: E402
import scene_cg   # noqa: E402

bad = 0


def check(ok, msg):
    global bad
    if not ok:
        bad += 1
        print("  FAIL %s" % msg)
    else:
        print("  ok   %s" % msg)


def touch(d, stem):
    """build_index 只要求 webp 文件存在，内容不读"""
    with open(os.path.join(d, stem + ".webp"), "wb") as f:
        f.write(b"RIFF0000WEBP")


def main():
    tmp = tempfile.mkdtemp(prefix="wa2_cgtest_")
    try:
        dst = os.path.join(tmp, "cg")
        os.makedirs(dst)
        # 旧的一批
        touch(dst, "咖啡厅（白天）")
        touch(dst, "教室（夜晚）")
        # 新导入的一批（只有一张和源目录同名）
        touch(dst, "公司办公区（空无一人）（白天）")

        print("[1] 增量导入保留旧目录")
        index, items = import_cg.build_index(dst, ["公司办公区（空无一人）（白天）"], prune=False)
        ids = {it["id"] for it in items}
        check(len(items) == 3, "三条都在（%d）" % len(items))
        check("咖啡厅（白天）" in ids, "旧的 咖啡厅（白天） 没被冲掉")
        check("教室（夜晚）" in ids, "旧的 教室（夜晚） 没被冲掉")
        check(os.path.isfile(index), "index.json 写出来了")

        print("[2] --prune 时才只保留源目录")
        _, items_pruned = import_cg.build_index(dst, ["公司办公区（空无一人）（白天）"], prune=True)
        check(len(items_pruned) == 1, "prune 后只剩源目录里的那一条（%d）" % len(items_pruned))
        # 恢复完整目录，给第 3 项用
        import_cg.build_index(dst, [], prune=False)

        print("[3] 素材里已有的地点不被别名抢走")
        assets = os.path.join(tmp, "assets")
        os.makedirs(os.path.join(assets, "cg"))
        idx = {"version": 1, "generated": "", "items": [
            {"id": "街上（晚上）（人行道）", "file": "街上（晚上）（人行道）.webp",
             "place": "街上", "kind": "place", "time": "晚上", "weather": "", "state": ["人行道"]},
            {"id": "街道（白天）", "file": "街道（白天）.webp",
             "place": "街道", "kind": "place", "time": "白天", "weather": "", "state": []},
            {"id": "街道（黑夜）", "file": "街道（黑夜）.webp",
             "place": "街道", "kind": "place", "time": "黑夜", "weather": "", "state": []},
        ]}
        with open(os.path.join(assets, "cg", "index.json"), "w", encoding="utf-8") as f:
            json.dump(idx, f, ensure_ascii=False)
        scene_cg.set_assets_dir(assets)
        scene_cg._CACHE["data"] = None      # 别让上一步的缓存串进来
        m = scene_cg.match("街道（黑夜）") or {}
        check(m.get("id") == "街道（黑夜）", "街道（黑夜）→ %r" % m.get("id"))
        m2 = scene_cg.match("街上（晚上）") or {}
        check(m2.get("place") == "街上", "街上（晚上）→ %r" % m2.get("place"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n问题数: %d" % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
