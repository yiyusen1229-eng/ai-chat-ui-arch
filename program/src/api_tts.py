# -*- coding: utf-8 -*-
"TtsApi 的 mixin：从 main.py 拆出，方法体未改动。"

import re
import time

import applog
import audio_gain
import tts_client


class TtsApi:
    """见 main.Handler；从 main 注入运行时全局。"""

    def _voice_state(self):
        """GET /api/voice/state：当前实际加载的权重与角色（排查串音用）"""
        try:
            import tts_client
            st = tts_client.state()
            try:
                import tts_prefetch
                st["prefetch"] = tts_prefetch.stats()   # 预合成最近做了什么
            except Exception:
                pass
            return self._json({"ok": True, **st})
        except Exception as e:
            return self._json({"ok": False, "error": str(e)}, 500)

    def _tts(self):
        from main import (NO_MODELS, log, qs, translate_ja)
        """合成语音，返回 wav

        参数（正式名 / 兼容旧名）：
            text      / t       要念的文字
            lang                 zh | ja
            emotion   / emo      情绪标签，影响语气
            speak_ja  / sp       日文原文；给了就直接念它（不再过翻译）
        """
        from urllib.parse import urlparse, parse_qs
        try:
            q = parse_qs(urlparse(self.path).query)
            text = qs(q, ("text", "t")).strip()
            lang = qs(q, ("lang",), "zh").strip() or "zh"
            emotion = qs(q, ("emotion", "emo"), "neutral").strip() or "neutral"
            # 成人场面：标签不是 H 就按 H 念。
            # 前端传了 hscene 就以它为准（重放旧句时带 1）；没传就用后端记住的当前场面
            # —— 多角色台词不会自带 H 标签，以前只有重放会强制，导致
            # 「实时平常语气、回想才是 H 语气」。
            _hs_raw = (qs(q, ("hscene", "hs")) or "").strip().lower()
            try:
                import hscene as _hsc
            except Exception:
                _hsc = None
            if _hs_raw:
                _hs_on = _hs_raw in ("1", "true", "yes", "on")
                if _hs_on and emotion != "H":
                    applog.log("按请求的场面标记用 H 参考（原标签 %r）" % (emotion,), "TTS")
                    emotion = "H"
            elif _hsc is not None:
                _eff = _hsc.effective_emotion(emotion)
                if _eff != emotion:
                    applog.log("按当前场面用 H 参考（原标签 %r）" % (emotion,), "TTS")
                    emotion = _eff
            speak_ja = qs(q, ("speak_ja", "sp")).strip()
            # 防御：speak_ja 里一个假名都没有，说明调用方把**中文字幕**当成日语原文传了
            # （多角色刚上线时历史里存的就是中文字幕）。当成"没给"，按中文念，
            # 否则下面会因为 speak_ja 强制 lang=ja，中文被日语模型念成呓语。
            if speak_ja and not re.search(r"[\u3040-\u30ff]", speak_ja):
                applog.log("TTS：speak_ja 里没有假名（多半是字幕当日语传了），按中文念", "WARN")
                speak_ja = ""
            # 多角色：这句是谁说的就用谁的权重/参考音频（不传 = 当前激活角色）
            speak_char = qs(q, ("char", "speaker", "who")).strip()
            if not text:
                return self._json({"error": "empty"}, 400)
            if NO_MODELS:
                return self._json({"error": "安全模式: 未加载语音模型"}, 503)
            if not tts_client.start():
                # 语音还在预热: 立刻返回, 前端只显示文字
                applog.log("语音未就绪: %s" % tts_client.status(), "WARN")
                return self._json({"error": "语音未就绪"}, 503)
            speak_text = text
            t_t0 = time.time()
            if speak_ja:
                speak_text = speak_ja
                lang = "ja"
            elif lang == "ja":
                ja = translate_ja(text)
                if ja:
                    speak_text = ja
                    log("日文配音: %s" % ja)
                else:
                    lang = "zh"          # 翻译失败就退回中文
            if lang == "ja":
                speak_text = tts_client.normalize_ja_names(speak_text)
            applog.log("TTS 准备: lang=%s emo=%s 角色=%s 文本=%s (翻译耗时 %.1fs)"
                       % (lang, emotion, speak_char or "(当前)", speak_text[:24], time.time() - t_t0), "TTS")
            t_s0 = time.time()
            data = tts_client.synthesize(speak_text, lang, emo=emotion, char=speak_char)
            applog.log("TTS 合成: %s 字节, 耗时 %.1fs"
                       % (len(data or b""), time.time() - t_s0), "TTS")
            if not data:
                return self._json({"error": tts_client.status()}, 500)
            # 响度归一化（#7）：量一次响度，把增益应用到**发出去的字节**上；
            # 缓存里的 wav 一个字节不改，增益另外记在 .gain.json 侧车里。
            gain = {}
            try:
                data, gain = audio_gain.apply(data)
            except Exception as e:
                applog.log("响度归一化失败（照原样播）：%s" % e, "WARN")
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Voice-Gain-DB", "%.2f" % (gain.get("gain_db") or 0.0))
            if gain.get("lufs") is not None:
                self.send_header("X-Voice-Lufs", "%.1f" % gain["lufs"])
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            return self._json({"error": str(e)}, 500)
