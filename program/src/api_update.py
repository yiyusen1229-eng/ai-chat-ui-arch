# -*- coding: utf-8 -*-
"""更新检查 / 一键更新 / 打开链接（从 main.py 拆出的 mixin）。

- GET  /api/update         查 GitHub 上的 version.json，判断有没有比本机新的版本
- GET  /api/update?force=1 「检查更新」菜单用：绕过「提示过就不再提示」的策略
- POST /api/update/apply   一键更新：git 仓库走 fetch+reset，zip 安装走整份快照下载
                           打包版（exe）直接拒绝——代码冻在 exe 里，只能整包替换
- POST /api/update/ack     用户点过提示（打开了下载页）：这个版本别再提醒
- GET  /api/update/status  一键更新的进度（前端轮询）
- GET  /api/open_url?url=  用系统浏览器打开链接（只允许 http/https）

提醒策略（2026-11-22 加）：`main.py` 的 `webview.start(private_mode=True)` 每次启动
都是全新的 WebView2 数据目录，localStorage 存不住 —— 所以「看过没有」只能记后端。
同一个版本：点过（ack）就再也不提；只看到没点 → 每 REMIND_DAYS 天再提一次。
以前没有这层记忆，加上打包版更新根本不生效，就变成「每次重开都弹同一条」。
"""

import io
import json
import os
import time
import urllib.request
import webbrowser

import datadir
import version as _ver
import updater

REMIND_DAYS = 3          # 同一版本「看到过但没点」时隔几天再提醒（改这个 = 改行为）
SEEN_FILE = "update_seen.json"


# ---------------------------------------------------------------- 提示记录
def _seen_path():
    return os.path.join(datadir.root(), SEEN_FILE)


def _read_seen():
    try:
        with io.open(_seen_path(), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _write_seen(d):
    try:
        p = _seen_path()
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        with io.open(p, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
    except Exception:
        pass            # 记不住不影响用，别把更新检查搞崩


class UpdateApi:
    """见 main.Handler。"""

    def _update_check(self):
        """查远端版本；拿不到就当“无更新”，绝不影响启动。"""
        info = {
            "ok": True,
            "current": _ver.VERSION,
            "latest": _ver.VERSION,
            "newer": False,
            "should_show": False,
            "auto": False,
            "mode": "",
            "portable": False,
            "hint": "",
            "url": _ver.PATCH_URL,
            "notes": "",
        }
        try:
            st = updater.detect()
            info["auto"] = bool(st.get("can_auto"))
            info["mode"] = st.get("mode") or ""
            info["portable"] = info["mode"] == "portable"
            info["hint"] = st.get("git_detail") or ""
        except Exception as e:
            info["auto_error"] = str(e)[:120]
        url = os.environ.get("WA2_UPDATE_URL") or _ver.RAW_VERSION_URL
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "WA2-Kazusa-Bot"})
            with urllib.request.urlopen(req, timeout=6) as r:
                # utf-8-sig：version.json 被记事本存过就会带 BOM（实测踩过）
                d = json.loads(r.read().decode("utf-8-sig"))
            latest = str(d.get("version") or "").strip()
            if latest:
                info["latest"] = latest
                info["newer"] = _ver.ver_key(latest) > _ver.ver_key(_ver.VERSION)
                # 打包版优先用 portable_url（下载新压缩包的地方），源码安装用补丁页
                info["url"] = ((d.get("portable_url") if info["portable"] else "")
                               or d.get("url") or _ver.PATCH_URL)
                info["notes"] = str(d.get("notes") or "")[:200]
                if info["newer"]:
                    info["should_show"] = self._decide_show(latest)
        except Exception as e:
            info["ok"] = False
            info["error"] = "%s: %s" % (type(e).__name__, e)
        return self._json(info)

    def _decide_show(self, latest):
        """这条提示该不该弹（并把「已经提示过」记下来）。

        新版本第一次出现 → 弹；同一版本点过 ack → 不弹；
        同一版本只看到没点 → 隔 REMIND_DAYS 天再弹一次。
        `?force=1`（菜单「检查更新」）绕过策略，永远弹。
        """
        force = "force=1" in (self.path or "")
        seen = _read_seen()
        same = str(seen.get("version") or "") == latest
        acked = bool(seen.get("ack")) and same
        try:
            last = int(seen.get("notified_at") or 0)
        except Exception:
            last = 0
        now = int(time.time())
        due = (not same) or (now - last >= REMIND_DAYS * 86400)
        show = force or (due and not acked)
        if show:
            rec = {"version": latest, "notified_at": now, "ack": False}
            if force and acked:
                rec["ack"] = True     # 手动「检查更新」看一眼不算又想要提醒
            _write_seen(rec)
        return bool(show)

    def _update_ack(self):
        """用户点过提示（打开了下载页）：这一版别再提醒。"""
        try:
            body = self._body() or {}
        except Exception:
            body = {}
        v = str(body.get("version") or "").strip()
        seen = _read_seen()
        cur = str(seen.get("version") or "")
        if v and cur and v != cur:
            return self._json({"ok": True, "ignored": True})   # 别把新版本的记录盖掉
        seen.update({"version": v or cur, "ack": True, "acked_at": int(time.time())})
        _write_seen(seen)
        return self._json({"ok": True, "version": seen.get("version") or ""})

    def _update_apply(self):
        """一键更新：后台跑，前端用 /api/update/status 轮询。"""
        try:
            st = updater.detect()
            if not st.get("can_auto"):
                detail = st.get("git_detail") or "无法自动更新"
                if st.get("mode") == "portable":
                    detail += "：请下载新的压缩包，解压后替换整个文件夹"
                return self._json({"ok": False,
                                   "portable": st.get("mode") == "portable",
                                   "error": detail})
            p = updater.apply()
            return self._json({"ok": True, "started": True,
                               "mode": st.get("mode"), "progress": p})
        except Exception as e:
            return self._json({"ok": False, "error": "%s: %s" % (type(e).__name__, e)})

    def _update_status(self):
        return self._json({"ok": True, "progress": updater.progress()})

    def _open_url(self):
        """用系统默认浏览器打开（只放行 http/https，避免被当成执行器）。"""
        from urllib.parse import urlparse, parse_qs
        import main

        q = parse_qs(urlparse(self.path).query)
        url = (main.qs(q, ("url",)) or "").strip()
        if not (url.startswith("http://") or url.startswith("https://")):
            return self._json({"ok": False, "error": "只允许 http/https"})
        try:
            webbrowser.open(url)
            return self._json({"ok": True, "url": url})
        except Exception as e:
            return self._json({"ok": False, "error": str(e)})
