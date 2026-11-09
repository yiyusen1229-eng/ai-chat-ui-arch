# -*- coding: utf-8 -*-
"""角色包导出 / 导入（zip）

一个角色 = `characters/<角色>/` 一个文件夹。角色包只带走「换台机器也能直接演」的东西：

    name.txt          显示名
    prompt/           提示词（base + stage1~4 + persona_*）
    worldbook/        世界书
    memory/           常驻记忆书
    portrait/         立绘（含 index.json）
    voice/            参考音频（TTS 音色）

**不带**（都是有意的）：

    sessions/         存档 —— 对话记录不该跟着包到处跑
    story.json        剧情索引开关（索引文件是本机生成的，别人拿了也用不了）
    tts.json          权重路径（绝对路径，换机必失效）
    opening.json / pronunciation.json   可选小配置，不属于「角色本体」
    任何缓存

zip 里多放一份 `manifest.json`：格式名、版本、角色名、导出时间、文件数、字节数。
导入时优先用它拿角色名；没有 manifest 的裸包也认（用压缩包文件名当角色名）。

导入是「先解到临时目录 → 校验 → 改名落盘」：中途失败不会在 characters/ 里留下半个角色。
防路径穿越 / 绝对路径 / 盘符 / 超大文件 / 条目爆炸；只认上面那几个目录，其它条目跳过并报告。
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

import characters

FORMAT = "wa2-char-pack"
FORMAT_VERSION = 1

# 包里的「角色本体」：5 个资源目录 + 显示名
PACK_DIRS = ("prompt", "worldbook", "memory", "portrait", "voice", "voice_model", "card")
PACK_FILES = ("name.txt",)
MANIFEST = "manifest.json"

# 上限（防解压炸弹；正常角色包 ~100MB，立绘 279 张 + 参考音频 71 个）
MAX_TOTAL = 2 * 1024 ** 3          # 解压后总量 2GB
MAX_ENTRY = 512 * 1024 ** 2        # 单个文件 512MB
MAX_ENTRIES = 20000

# 已经压过的媒体用 STORED：省掉白花的 CPU（实测 87MB 立绘 + 16MB 参考音频）
_STORE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif",
              ".wav", ".mp3", ".ogg", ".flac", ".m4a")

# 记住上次导出到哪儿（下次选目录直接落在那儿）；和 prefs.json 一个规矩，放用户目录
CFG_NAME = "char_pack.json"


def _log(msg):
    try:
        import applog
        applog.log(msg, "PACK")
    except Exception:
        pass


# ------------------------------------------------------------------ 小工具
def _safe_rel(rel):
    """压缩包条目 → 安全的相对路径；不安全返回空串"""
    rel = (rel or "").replace("\\", "/").strip()
    if not rel or rel.endswith("/"):
        return ""
    if rel.startswith("/") or re.match(r"^[A-Za-z]:", rel):
        return ""
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        return ""
    return "/".join(parts)


def _top_dir(rel):
    return rel.split("/")[0] if "/" in rel else ""


def _wrapper_dir(names):
    """用户常常把整个角色文件夹压进去（条目是 `和纱/prompt/...`）。

    所有带目录的条目都在同一个顶层目录下、且那个名字不是 prompt/worldbook/...
    就把它当包装层剥掉。返回要剥掉的目录名，没有就是空串。
    """
    tops = set()
    for n in names:
        t = _top_dir(n)
        if t:
            tops.add(t)
    if len(tops) == 1:
        t = next(iter(tops))
        if t not in PACK_DIRS:
            return t
    return ""


def _cfg_path():
    return os.path.join(characters.user_dir(), CFG_NAME)


def _last_dir():
    try:
        with open(_cfg_path(), encoding="utf-8") as f:
            d = json.load(f)
        p = (d or {}).get("last_export_dir") or ""
        return p if p and os.path.isdir(p) else ""
    except Exception:
        return ""


def _save_last_dir(path):
    try:
        tmp = _cfg_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"last_export_dir": path}, f, ensure_ascii=False, indent=1)
        os.replace(tmp, _cfg_path())
    except Exception:
        pass


# ------------------------------------------------------------------ 系统对话框
# 和 characters.pick_folder() 同一套：PowerShell 独立进程 + 1x1 置顶 Owner，
# 路径经 UTF-8 临时文件传回来（走 stdout 会被 GBK 转坏中文路径）。
_OWNER = (
    "$ErrorActionPreference='Stop';"
    "Add-Type -AssemblyName System.Windows.Forms;"
    "Add-Type -AssemblyName System.Drawing;"
    "$f=New-Object System.Windows.Forms.Form;"
    "$f.TopMost=$true;$f.ShowInTaskbar=$false;$f.FormBorderStyle='None';"
    "$f.StartPosition='Manual';$f.Size=New-Object System.Drawing.Size(1,1);"
    "$f.Location=New-Object System.Drawing.Point(-3000,-3000);$f.Show();"
)


def _q(s):
    return (s or "").replace("'", "''")


def _run_ps(ps, tmp_out):
    """跑 PowerShell 对话框，返回它写进临时文件的路径（没选就是空串）

    打包版是窗口程序，不带控制台：加 CREATE_NO_WINDOW 免得多闪一个黑框
    （对话框本身是 WinForms 窗口，不受影响）。
    """
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", ps],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=600, creationflags=flags)
    except subprocess.TimeoutExpired:
        return ""
    except Exception as e:
        _log("角色包：选择窗口打不开 %s" % e)
        return ""
    try:
        with open(tmp_out, encoding="utf-8-sig", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return ""


def _tmp_out():
    fd, p = tempfile.mkstemp(prefix="wa2_pack_", suffix=".txt")
    os.close(fd)
    try:
        os.remove(p)                  # 让脚本自己创建；没选就还是不存在
    except OSError:
        pass
    return p


def pick_export_dir(title="选择角色包导出到哪个文件夹", default=""):
    """系统「选择文件夹」窗口。返回 {ok, dir} 或 {ok:False, cancelled:True}"""
    if os.name != "nt":
        return {"ok": False, "error": "只有 Windows 支持文件夹选择窗口"}
    tmp = _tmp_out()
    ps = (
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;" + _OWNER +
        "$d=New-Object System.Windows.Forms.FolderBrowserDialog;"
        "$d.Description='%s';"
        "$d.SelectedPath='%s';"
        "$d.ShowNewFolderButton=$true;"
        "$ok=($d.ShowDialog($f) -eq [System.Windows.Forms.DialogResult]::OK);"
        "$f.Close();"
        "if($ok){[System.IO.File]::WriteAllText('%s', $d.SelectedPath, "
        "(New-Object System.Text.UTF8Encoding $false))}"
    ) % (_q(title), _q(default or characters.user_dir()), _q(tmp))
    picked = _run_ps(ps, tmp)
    try:
        os.remove(tmp)
    except OSError:
        pass
    if not picked:
        return {"ok": False, "cancelled": True}
    picked = os.path.normpath(picked)
    if not os.path.isdir(picked):
        return {"ok": False, "error": "选的不是文件夹：%s" % picked}
    return {"ok": True, "dir": picked}


def pick_pack_file(title="选择角色包（.zip）", default=""):
    """系统「选择文件」窗口，只列 zip。返回 {ok, path} 或 cancelled"""
    if os.name != "nt":
        return {"ok": False, "error": "只有 Windows 支持文件选择窗口"}
    tmp = _tmp_out()
    ps = (
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;" + _OWNER +
        "$d=New-Object System.Windows.Forms.OpenFileDialog;"
        "$d.Title='%s';"
        "$d.Filter='角色包 (*.zip)|*.zip|所有文件 (*.*)|*.*';"
        "$d.CheckFileExists=$true;$d.Multiselect=$false;"
        "%s"
        "$ok=($d.ShowDialog($f) -eq [System.Windows.Forms.DialogResult]::OK);"
        "$f.Close();"
        "if($ok){[System.IO.File]::WriteAllText('%s', $d.FileName, "
        "(New-Object System.Text.UTF8Encoding $false))}"
    ) % (_q(title),
         ("$d.InitialDirectory='%s';" % _q(default)) if default and os.path.isdir(default) else "",
         _q(tmp))
    picked = _run_ps(ps, tmp)
    try:
        os.remove(tmp)
    except OSError:
        pass
    if not picked:
        return {"ok": False, "cancelled": True}
    picked = os.path.normpath(picked)
    if not os.path.isfile(picked):
        return {"ok": False, "error": "选的文件不存在：%s" % picked}
    return {"ok": True, "path": picked}


# ------------------------------------------------------------------ 导出
# 不该进包的垃圾：编辑/备份留下的副本（.bak、.bak-20261105-2015、~$xxx、Thumbs.db…）
# 不光是体积问题 —— 旧版提示词里可能有已经被改掉的私人内容，不该跟着包发给别人
_JUNK_RE = re.compile(
    r"(\.bak(\.|-|$)|\.tmp$|\.swp$|^~\$|^\.DS_Store$|^Thumbs\.db$|^desktop\.ini$)",
    re.IGNORECASE)


def _is_junk(name):
    return bool(_JUNK_RE.search(name or ""))


def _members(cid):
    """要打包的文件 [(绝对路径, 包内相对路径)]"""
    root = os.path.join(characters.root(), cid)
    out = []
    for fn in PACK_FILES:
        p = os.path.join(root, fn)
        if os.path.isfile(p):
            out.append((p, fn))
    for d in PACK_DIRS:
        base = os.path.join(root, d)
        if not os.path.isdir(base):
            continue
        for dirpath, _dirs, files in os.walk(base):
            for fn in sorted(files):
                if _is_junk(fn):
                    continue
                p = os.path.join(dirpath, fn)
                if not os.path.isfile(p):
                    continue
                rel = os.path.relpath(p, root).replace("\\", "/")
                out.append((p, rel))
    return out


def _unique_zip(out_dir, base):
    name = characters._sanitize(base) or "角色"
    p = os.path.join(out_dir, name + ".zip")
    i = 2
    while os.path.exists(p):
        p = os.path.join(out_dir, "%s_%d.zip" % (name, i))
        i += 1
    return p


def _sanitize_standing(path):
    """把常驻记忆书里的**关系摘要**抹掉，只留固定设定。

    `memory/standing.json` = {fixed（角色固定设定）, relation_summary（从聊天里总结的关系）,
    stage, source_msgs, updated_at, version}。后面四项都是「你和这个角色处到哪了」，
    属于私人进度：导出角色包是为了给别人一个角色，不是把自己的进度一起发出去。
    （2026-11-06：实测导出的包里 relation_summary 有 210 字、stage=4、source_msgs=81。）
    读不出来就原样返回（宁可少脱敏也别把包弄坏）。
    """
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        if not isinstance(d, dict):
            return open(path, "rb").read()
    except Exception:
        try:
            return open(path, "rb").read()
        except OSError:
            return b"{}"
    d["relation_summary"] = ""
    d["stage"] = 1
    d["source_msgs"] = 0
    d["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    return json.dumps(d, ensure_ascii=False, indent=1).encode("utf-8")


def export_pack(cid, out_dir):
    """把 characters/<cid>/ 打包成 <out_dir>/<角色名>.zip"""
    cid = characters._sanitize(cid or "")
    if not cid:
        return {"ok": False, "error": "角色名不对"}
    src = os.path.join(characters.root(), cid)
    if not os.path.isdir(src):
        return {"ok": False, "error": "没有这个角色文件夹：%s" % cid}
    if not out_dir or not os.path.isdir(out_dir):
        return {"ok": False, "error": "导出目录不存在：%s" % out_dir}

    members = _members(cid)
    if not members:
        return {"ok": False, "error": "这个角色文件夹是空的（没有提示词 / 立绘 / 语音可导出）"}

    parts = {}
    total = 0
    for p, rel in members:
        top = _top_dir(rel) or rel
        parts[top] = parts.get(top, 0) + 1
        try:
            total += os.path.getsize(p)
        except OSError:
            pass

    target = _unique_zip(out_dir, characters.display_name(cid) or cid)
    manifest = {
        "format": FORMAT,
        "version": FORMAT_VERSION,
        "id": cid,
        "name": characters.display_name(cid),
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "app": "WA2-Kazusa-Bot",
        "parts": parts,
        "files": len(members),
        "bytes": total,
    }
    tmp = target + ".tmp"
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as z:
            z.writestr(MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=1))
            for p, rel in members:
                # standing.json 要脱敏后再进包：关系摘要/阶段是从真实聊天里长出来的，
                # 不该跟着角色包发给别人（2026-11-06 审出来的）
                if rel.replace("\\", "/").endswith("memory/standing.json"):
                    z.writestr(rel, _sanitize_standing(p))
                    continue
                ext = os.path.splitext(rel)[1].lower()
                kind = zipfile.ZIP_STORED if ext in _STORE_EXT else zipfile.ZIP_DEFLATED
                z.write(p, rel, compress_type=kind)
        os.replace(tmp, target)          # 原子替换：写一半断电不会留半个包
    except Exception as e:
        try:
            os.remove(tmp)
        except OSError:
            pass
        _log("角色包：导出失败 %s" % e)
        return {"ok": False, "error": "打包失败：%s" % e}

    _save_last_dir(out_dir)
    _log("角色包：导出 %s → %s（%d 个文件）" % (cid, target, len(members)))
    return {"ok": True, "path": target, "file": os.path.basename(target),
            "dir": out_dir, "files": len(members), "bytes": total, "parts": parts}


def export_with_dialog(cid):
    """导出按钮：先选目录再打包（目录窗口取消就什么都不做）"""
    cid = characters._sanitize(cid or "")
    if not cid:
        return {"ok": False, "error": "角色名不对"}
    if not os.path.isdir(os.path.join(characters.root(), cid)):
        return {"ok": False, "error": "没有这个角色文件夹：%s" % cid}
    d = pick_export_dir(default=_last_dir() or os.path.dirname(characters.root()))
    if not d.get("ok"):
        return d
    return export_pack(cid, d["dir"])


# ------------------------------------------------------------------ 导入
def _read_manifest(z, prefix):
    """读 manifest.json（可能在根，也可能在包装目录里）；坏了就当没有"""
    for name in ((prefix + "/" + MANIFEST) if prefix else "", MANIFEST):
        if not name:
            continue
        try:
            with z.open(name) as f:
                d = json.loads(f.read().decode("utf-8"))
            if isinstance(d, dict):
                return d
        except Exception:
            continue
    return {}


def _plan_entries(infos, prefix):
    """把条目分成「要的」和「跳过的」，顺便做安全与体积校验"""
    plan, skipped = [], []
    for i in infos:
        if i.is_dir():
            continue
        rel = _safe_rel(i.filename)
        if not rel:
            skipped.append(i.filename)
            continue
        if prefix:
            if not rel.startswith(prefix + "/"):
                # 包装层外的散文件（manifest.json 之类）单独处理
                if rel != MANIFEST:
                    skipped.append(rel)
                continue
            rel = rel[len(prefix) + 1:]
        if rel == MANIFEST:
            continue
        if rel in PACK_FILES or _top_dir(rel) in PACK_DIRS:
            plan.append((i, rel))
        else:
            skipped.append(rel)
    return plan, skipped


def _check_limits(infos):
    if len(infos) > MAX_ENTRIES:
        return "压缩包条目太多（%d 个，上限 %d）" % (len(infos), MAX_ENTRIES)
    total = 0
    for i in infos:
        if i.file_size > MAX_ENTRY:
            return "包里有超大文件：%s（%d MB）" % (i.filename, i.file_size // (1024 * 1024))
        total += i.file_size
        if total > MAX_TOTAL:
            return "解压后太大（超过 2GB）"
    return ""


def import_pack(zip_path):
    """把一个角色包解成 characters/ 下的新角色（不自动切换）"""
    if not zip_path or not os.path.isfile(zip_path):
        return {"ok": False, "error": "找不到文件：%s" % zip_path}
    if not zipfile.is_zipfile(zip_path):
        return {"ok": False, "error": "这不是有效的 zip 文件"}

    tmp_root = ""
    try:
        with zipfile.ZipFile(zip_path) as z:
            infos = z.infolist()
            bad = _check_limits(infos)
            if bad:
                return {"ok": False, "error": bad}

            names = [_safe_rel(i.filename) for i in infos if not i.is_dir()]
            names = [n for n in names if n]
            prefix = _wrapper_dir(names)
            manifest = _read_manifest(z, prefix)
            plan, skipped = _plan_entries(infos, prefix)
            if not plan:
                return {"ok": False,
                        "error": "包里没有角色内容（要 prompt/ worldbook/ memory/ portrait/ voice/）"}

            # 先解到 characters/ 下的临时目录：同一个盘，最后一步是原子改名
            tmp_root = os.path.join(characters.root(),
                                    ".importing_%d" % int(time.time() * 1000))
            os.makedirs(tmp_root, exist_ok=True)
            total = 0
            for i, rel in plan:
                dst = os.path.join(tmp_root, *rel.split("/"))
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                with z.open(i) as srcf, open(dst, "wb") as outf:
                    shutil.copyfileobj(srcf, outf, 1024 * 1024)
                total += i.file_size

        # 角色 id：manifest 里的 > 包里的 name.txt > 压缩包文件名；重名自动加序号
        nm_text = ""
        try:
            with open(os.path.join(tmp_root, "name.txt"), encoding="utf-8") as f:
                nm_text = f.read().strip()
        except Exception:
            pass
        raw = (manifest.get("id") or manifest.get("name") or nm_text
               or os.path.splitext(os.path.basename(zip_path))[0])
        base = characters._sanitize(raw) or "导入角色"
        cid = characters._unique(base)
        final = os.path.join(characters.root(), cid)
        os.replace(tmp_root, final)          # 目录改名，失败会抛异常
        tmp_root = ""

        characters._make_dirs(final)
        if not os.path.isfile(os.path.join(final, "name.txt")):
            nm = (manifest.get("name") or base or cid).strip()[:12] or cid
            characters._write(os.path.join(final, "name.txt"), nm)
        # 提示词是空的（裸包/手工包）→ 用自带模板铺一份，免得导入后是个空角色
        if not characters._has_prompt(os.path.join(final, "prompt")):
            characters.seed_character(cid)

        name = characters.display_name(cid)
        _log("角色包：导入 %s → %s（%d 个文件）" % (zip_path, cid, len(plan)))
        return {"ok": True, "id": cid, "name": name, "files": len(plan),
                "bytes": total, "skipped": skipped[:20],
                "skipped_count": len(skipped),
                "manifest": {k: manifest.get(k) for k in ("format", "version", "name", "created_at")}}
    except zipfile.BadZipFile:
        return {"ok": False, "error": "压缩包损坏，读不出来"}
    except Exception as e:
        _log("角色包：导入失败 %s" % e)
        return {"ok": False, "error": "导入失败：%s" % e}
    finally:
        if tmp_root and os.path.isdir(tmp_root):
            shutil.rmtree(tmp_root, ignore_errors=True)


def import_with_dialog():
    """导入按钮：弹选文件窗口 → 导入（取消就什么都不做）"""
    f = pick_pack_file(default=_last_dir() or os.path.dirname(characters.root()))
    if not f.get("ok"):
        return f
    return import_pack(f["path"])
