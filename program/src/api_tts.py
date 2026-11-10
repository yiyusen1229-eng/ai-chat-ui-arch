# -*- coding: utf-8 -*-
"TtsApi 的 mixin：从 main.py 拆出，方法体未改动。"

import time
import applog
import audio_gain
import tts_client


class TtsApi:
    """见 main.Handler；从 main 注入运行时全局。"""

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
            # 重放旧存档句时的标记：当前确实在成人场面，就按 H 念
            # （旧存档里的 emotion 是生成时写死的，可能是 calm，重放会听着不对）
            _hs_raw = (qs(q, ("hscene", "hs")) or "").strip().lower()
            _hs_on = _hs_raw in ("1", "true", "yes", "on")
            if not _hs_raw:
                # 没带参数就用后端记住的"当前场面"（载入存档后的自动重播走这条）
                try:
                    import hscene as _hsc
                    _hs_on = _hsc.last_scene() is True
                except Exception:
                    _hs_on = False
            if _hs_on:
                if emotion != "H":
                    applog.log("重放：按当前场面用 H 参考（原标签 %r）" % (emotion,), "TTS")
                    emotion = "H"
            speak_ja = qs(q, ("speak_ja", "sp")).strip()
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
