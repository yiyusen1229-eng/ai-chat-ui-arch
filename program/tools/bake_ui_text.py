# -*- coding: utf-8 -*-
"""把界面里的**固定文案**预先渲染成 PNG，当 UI 素材用。

为什么不实时渲染：
    每次 /api/text 都要走一遍字模渲染，图片从空到有会闪一下；
    而这些文案永远不变，烤成静态图后前端直接 <img src> 加载，零延迟、无闪烁。

用法：
    python tools/bake_ui_text.py            # 全部重烤
    python tools/bake_ui_text.py --list     # 只看清单
"""
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(BASE, "src")
OUT = os.path.join(BASE, "res", "assets", "ui", "text")

sys.path.insert(0, SRC)

# 文案清单：key -> (文字, 字号, 是否描边, 染色)
# 说明：字号与界面里 uitext 实际用的值一致；描边只给"叠在画面上的字"开
ITEMS = {
    # ---- 模型管理 ----
    "mgr_chat":     ("聊天模型", 12, True, None),
    "mgr_voice":    ("语音模型", 12, True, None),
    "mgr_mt":       ("翻译模型", 12, True, None),
    "mgr_endpoint": ("端点", 11, True, None),
    "mgr_apikey":   ("API 密钥", 11, True, None),
    "mgr_model":    ("模型名称", 11, True, None),
    "mgr_switch":   ("切换模型", 11, True, None),
    "mgr_connect":  ("连接", 11, True, None),
    "mgr_ok":       ("已连接", 11, True, None),
    "mgr_fail":     ("连接失败", 11, True, None),

    # ---- 存档 / 读档提示 ----
    "save_done":    ("存档已保存", 12, True, None),
    "save_over":    ("覆盖这个存档吗？", 12, True, None),
    "save_yes":     ("是", 12, True, None),
    "save_no":      ("否", 12, True, None),
    "load_empty":   ("这个槽位没有存档", 12, True, None),
    "save_short":   ("对话不足两条不能存档", 12, True, None),

    # ---- 回想 / 输入框 ----
    "log_act_on":   ("动作：开", 11, True, None),
    "log_act_off":  ("动作：关", 11, True, None),
    "log_send":     ("发送", 11, True, None),

    # ---- 特别模式（都是无描边的界面字） ----
    # 数字/字母不在这里重烤：数字用 cost_d0~cost_d9，字母（token）用 cost_token，
    # ¥ 和 . 用 cost_yen / cost_dot；这里只补「%」「=」「≈」这三个符号。
    "sp_title_save":    ("存读档设置", 15, False, None),
    "sp_title_consume": ("每轮消耗参考", 15, False, None),
    "sp_title_char":    ("配置角色", 15, False, None),
    "sp_ctx":           ("发送到的模型上下文大小：", 12, False, None),
    "sp_trigger":       ("触发记忆书打包的阈值：", 12, False, None),
    "sp_recent":        ("读档时最多上下文保留长度：", 12, False, None),
    "sp_entries":       ("记忆条目上限：", 12, False, None),
    # 「每轮 token 预估：」= sp_turn_pre + cost_token + sp_turn_post
    "sp_turn_pre":      ("每轮", 12, False, None),
    "sp_turn_post":     ("预估：", 12, False, None),
    "sp_cost_label":    ("每轮费用预估：", 12, False, None),
    "sp_name_label":    ("角色名称：", 12, False, None),
    "sp_item_portrait": ("配置角色立绘", 12, False, None),
    "sp_item_prompt":   ("配置角色提示词", 12, False, None),
    "sp_item_world":    ("配置角色世界书", 12, False, None),
    "sp_item_memory":   ("配置角色记忆书", 12, False, None),
    "sp_item_voice":    ("配置语音模型参考音频", 12, False, None),
    "sp_item_model":    ("配置角色模型", 12, False, None),
    "sp_saved":         ("已保存", 11, False, None),
    "sp_save_btn":      ("保存", 9, False, None),
    # 配置项下拉出来的两个按钮（宽 106、高 20，字 10px 刚好）
    "sp_btn_import":    ("导入文件", 10, False, None),
    "sp_btn_open":      ("查看文件", 10, False, None),
    # 「配置角色」标题右边的两个按钮（切换角色 / 新建角色，底板用发送按钮的三态图）
    "sp_btn_switch":    ("切换角色", 9, False, None),
    "sp_btn_new":       ("新建角色", 9, False, None),
    # 配置角色模型：下拉面板两个按钮 + 对话框标题与三个连接按钮
    "sp_btn_chat":      ("聊天模型", 10, False, None),
    "sp_btn_voicem":    ("语音模型", 10, False, None),
    "mgr_dialog":       ("角色模型", 12, True, None),
    "mgr_conn_switch":  ("切换连接", 11, True, None),
    "mgr_conn_off":     ("断开连接", 11, True, None),
    "mgr_exit":         ("退出", 11, True, None),
    "mgr_director":     ("导演模型", 12, True, None),
    "spmd_ph_url":      ("api.deepseek.com", 10, False, None),
    "spmd_ph_key":      ("sk-...", 10, False, None),
    "sp_pct":           ("%", 12, False, None),
    "sp_eq":            ("=", 12, False, None),
    "sp_approx":        ("≈", 12, False, None),
    # 角色切换面板（spcp）的两个固定文案：标题 13px、取消 9px
    "sp_switch_title":  ("切换角色", 13, False, None),
    "sp_cancel":        ("取消", 9, False, None),
}


def main():
    if "--list" in sys.argv:
        for k, (t, fs, stroke, _c) in ITEMS.items():
            print("%-18s %s（%dpx）" % (k, t, fs))
        return

    # 只烤指定前缀的（例：python tools/bake_ui_text.py sp_），不给就全烤
    only = ""
    for a in sys.argv[1:]:
        if not a.startswith("--"):
            only = a
            break

    import sub_render
    os.makedirs(OUT, exist_ok=True)
    made = 0
    for key, (text, fs, stroke, color) in ITEMS.items():
        if only and not key.startswith(only):
            continue
        path = os.path.join(OUT, key + ".png")
        img = sub_render.render_block(text, maxw=2000, stroke=bool(stroke),
                                      margin=None if stroke else 0, color=color)
        img.save(path)
        made += 1
        print("%-18s %-24s %dx%d" % (key, text, img.width, img.height))
    print("\n共生成 %d 张 → %s" % (made, OUT))


if __name__ == "__main__":
    main()
