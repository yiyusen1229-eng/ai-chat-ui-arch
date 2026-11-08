# -*- coding: utf-8 -*-
"""演出标签提示（system 末尾那段）的自测。

为什么单独测：2026-11-06 用户反馈「基本没有自动换过场景 CG、立绘和 BGM」，
根因是这段提示以前整个挂在 `if cc:`（有云端 key）下面 —— 本地模型根本不知道
要输出 <scene>/<outfit>/<emotion>，全是空转。这个测试盯着「两条路都要有」。

跑：
    cd program && .\\build_env\\venv\\Scripts\\python.exe tools\\test_chat_hints.py
"""
import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import api_chat  # noqa: E402

_fail = []


def check(name, ok, detail=""):
    print(("  ok   " if ok else "  FAIL ") + name + ("" if ok else "  " + str(detail)))
    if not ok:
        _fail.append(name)


def main():
    local = api_chat.performance_hints(False, "礼服、睡衣")
    cloud = api_chat.performance_hints(True, "礼服、睡衣")

    print("[1] 本地模型（没有云端 key）")
    check("带【场景】", "【场景】" in local, local[:60])
    check("带【服装】且列出了可选衣服", "【服装】" in local and "礼服" in local)
    check("带【情绪】", "【情绪】" in local, "")
    check("带【格式】（action 标签）", "【格式】" in local and "<action>" in local)
    check("不要求「只用日语」", "只用日语" not in local, "")
    check("不要求双语", "<ja>" not in local or "【双语】" not in local, "")

    print("[2] 云端")
    check("带【场景】", "【场景】" in cloud)
    check("带【语言】只用日语", "【语言】" in cloud and "只用日语" in cloud)
    check("带【双语】示例", "【双语】" in cloud and "<ja>" in cloud and "<zh>" in cloud)

    print("[3] 公共部分")
    check("没有空括号地名表", "（）" not in local and "()" not in local, "")
    check("云端比本地长", len(cloud) > len(local), "%d vs %d" % (len(cloud), len(local)))
    check("两句都提到 <scene>", "<scene>" in local and "<scene>" in cloud)
    check("两句都提到 <outfit>", "<outfit>" in local and "<outfit>" in cloud)
    check("两句都提到 <emotion>", "<emotion>" in local and "<emotion>" in cloud)

    check("默认参数（不给衣服）也不崩", "【服装】" in api_chat.performance_hints(False))


    print("[H 规则] 成人场面要正面要求标 H（不能只写「不在这种场面里不要用」）")
    hints = api_chat.performance_hints(True, "睡衣")
    check("提到「正在发生成人场面」", "正在发生成人场面" in hints)
    check("明确说了拟声也要标 H", "拟声" in hints and "H" in hints)
    check("警告了标 calm 会不搭", "calm" in hints and "不搭" in hints)


    print("[hscene] 场面判定标记（规则 + 示例都要有）")
    check("规则里有 <hscene> 说明", "<hscene>是</hscene>" in hints)
    check("示例里有 <hscene>否</hscene>", "<hscene>否</hscene>" in hints)
    check("说明了只用来挑语音参考", "语音参考" in hints)


    print("[续说] continue 模式要提示她接着说")
    _src = io.open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "src", "api_chat.py"), encoding="utf-8").read()
    check("后端有 continue 分支", "_auto_continue" in _src)
    check("提示里写了续说", "【续说】" in _src)
    check("明确要求不要等对方回答", "不要等对方回答" in _src)

    print()
    if _fail:
        print("失败 %d 项：%s" % (len(_fail), "、".join(_fail)))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
