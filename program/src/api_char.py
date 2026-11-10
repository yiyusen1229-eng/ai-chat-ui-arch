# -*- coding: utf-8 -*-
"CharApi 的 mixin：从 main.py 拆出，方法体未改动。"

import json, os
import characters
import persona_stage
import special_cfg


class CharApi:
    """见 main.Handler；从 main 注入运行时全局。"""

    def _character_get(self):
        from main import (ASSETS_DIR)
        """GET /api/character/…：角色列表 / 立绘索引。

        立绘图片本身不走这里（返回 None，交给 translate_path 的静态分支）——
        这样 Range / 缓存 / Content-Type 都复用现成的那套。
        """
        if self.path.startswith("/api/character/sprite/index.json"):
            # 多角色：?char=雪菜 时返回她自己的立绘索引
            _cid = ""
            try:
                from main import qs
                from urllib.parse import urlparse, parse_qs
                _q = parse_qs(urlparse(self.path).query)
                _cid = qs(_q, ("char", "c")).strip()
            except Exception:
                _cid = ""
            return self._json(characters.sprite_index(os.path.join(ASSETS_DIR, "sprites"), _cid or None))
        if self.path == "/api/character/opening":
            try:
                p = os.path.join(characters.active_dir(), "opening.json")
                if os.path.isfile(p):
                    with open(p, encoding="utf-8") as f:
                        d = json.load(f)
                    if isinstance(d, dict):
                        return self._json({"ok": True, "zh": d.get("zh", ""),
                                           "ja": d.get("ja", ""),
                                           "emotion": d.get("emotion", "calm")})
            except Exception:
                pass
            return self._json({"ok": False})
        if self.path.startswith("/api/character/list"):
            return self._json({"ok": True,
                               "active": characters.active_id(),
                               "dir": characters.root(),
                               "characters": characters.list_characters()})
        return None

    def _after_switch(self, res):
        from main import (_set_character_sessions)
        """切换 / 新建角色之后：关系阶段跟着换人重置，并把新名字带回去"""
        if isinstance(res, dict) and res.get("ok"):
            try:
                persona_stage.reset()
            except Exception:
                pass
            try:
                _set_character_sessions()
            except Exception:
                pass
            try:
                res["name"] = special_cfg.get_name()
                res["dir"] = characters.active_dir()
            except Exception:
                pass
        return res
