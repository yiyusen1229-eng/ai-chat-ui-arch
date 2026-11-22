# -*- coding: utf-8 -*-
"""模型调用的记账口：谁花了多少 token、多少钱。

为什么单开一个模块：记账的真身在 main._record_usage（它手里有累计值和价格表），
但 actor / director_gen / translate 要是直接 `import main`，自测里会把整个程序
再拉一遍（起目录、读配置、摸角色），既慢又容易出副作用。所以让 main 启动时
把函数 bind 进来，其它模块只调 record()。

没绑定时 record() 什么也不做、返回空 dict —— 统计坏了不能影响说话。
"""
import threading

_LOCK = threading.Lock()
_IMPL = {"fn": None}


def bind(fn):
    """main 启动时调一次：把 _record_usage 挂上来（传 None 可以解绑，自测用）"""
    with _LOCK:
        _IMPL["fn"] = fn


def bound():
    with _LOCK:
        return bool(_IMPL["fn"])


def record(model, usage):
    """记一次调用；返回这次的花费信息（没有记账口时返回空 dict）"""
    with _LOCK:
        fn = _IMPL["fn"]
    if not fn:
        return {}
    try:
        return fn(model or "", usage or {}) or {}
    except Exception:
        return {}
