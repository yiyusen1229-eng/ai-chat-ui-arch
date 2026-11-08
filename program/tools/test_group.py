# -*- coding: utf-8 -*-
"""多角色同场（group.py）：提示块与分句解析。"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import group

FAILS = []
def check(name, cond, detail=""):
    if cond:
        print("  ok   " + name)
    else:
        FAILS.append(name)
        print("  FAIL " + name + "  " + str(detail))

def main():
    print("[1] 同场提示块")
    b = group.build_block([{"name": "和纱"}, {"name": "雪菜"}], "刚吵完架", "和纱")
    check("写了在场角色", "和纱" in b and "雪菜" in b)
    check("写了剧情方向", "刚吵完架" in b)
    check("要求一次一个角色说话", "一个" in b)
    check("给了台词格式", "名字：「台词」" in b)
    check("空的 cast 返回空串", group.build_block([]) == "")

    print("[2] 分句解析")
    cast = [{"name": "和纱"}, {"name": "雪菜"}]
    r = group.parse_line("和纱：「…なに」", cast, "和纱")
    check("她的台词是 her", r["line_type"] == "her" and r["text"] == "…なに", r)
    r = group.parse_line("雪菜：「あのね」", cast, "和纱")
    check("别人的台词是 other", r["line_type"] == "other" and r["speaker"] == "雪菜", r)
    r = group.parse_line("教室里安静下来。", cast, "和纱")
    check("无引号是旁白", r["line_type"] == "narr", r)
    r = group.parse_line("「…そう」", cast, "和纱")
    check("没写名字的台词算她的", r["line_type"] == "her" and r["text"] == "…そう", r)
    r = group.parse_line("", cast, "和纱")
    check("空行是旁白且文本为空", r["line_type"] == "narr" and r["text"] == "")

    print()
    if FAILS:
        print("失败 %d 项：%s" % (len(FAILS), "、".join(FAILS)))
        return 1
    print("全部通过")
    return 0

if __name__ == "__main__":
    sys.exit(main())
