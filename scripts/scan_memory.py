#!/usr/bin/env python3
"""扫描所有记忆文件：统计行数、识别历史快照、标记过期日志、检查引用死链。

只读脚本——不做任何修改、删除或写入。

用法：
    python scan_memory.py                      # 自动扫描全部已知位置
    python scan_memory.py --workspace <工作区根>   # 指定工作区根目录
    python scan_memory.py --json               # 输出 JSON
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

STALE_DAYS = 30
HOME = Path(os.environ.get("USERPROFILE") or Path.home())
WORKBUDDY = HOME / ".workbuddy"
MAX_DEPTH = 4
DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})\.md$")
# 记忆文件中引用 Windows 绝对路径：优先匹配反引号包裹（含空格），其次裸路径
PATH_CODE_RE = re.compile(r"`([A-Za-z]:\\[^`]+)`")
PATH_RE = re.compile(r"[A-Za-z]:\\[^\s`\"'()\[\]，。；：,;]+")


def find_memory_files(workspace_root: Path) -> list[dict]:
    """枚举用户级与项目级记忆文件。"""
    found: list[dict] = []

    # 1) 用户级（新位置 personal 目录 + 旧位置根目录）
    for p in sorted(WORKBUDDY.glob("user-*/MEMORY.md")):
        found.append({"layer": "用户级", "path": p})
    for p in sorted(WORKBUDDY.glob("MEMORY*.md*")):
        found.append({"layer": "用户级(旧位置/快照)", "path": p})

    # 2) 项目级 + 每日日志（遍历工作区根下各项目）
    if workspace_root.is_dir():
        for proj in sorted(workspace_root.iterdir()):
            mem_dir = proj / ".workbuddy" / "memory"
            if not mem_dir.is_dir():
                continue
            for p in sorted(mem_dir.iterdir()):
                if p.suffix == ".md":
                    layer = "项目级" if p.name == "MEMORY.md" else "每日日志"
                    found.append({"layer": layer, "project": proj.name, "path": p})
    return found


def stat_file(entry: dict) -> dict:
    path: Path = entry["path"]
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        entry["error"] = str(exc)
        return entry

    lines = text.splitlines()
    entry["lines"] = len(lines)
    entry["bytes"] = path.stat().st_size
    entry["mtime"] = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")

    m = DATE_RE.match(path.name)
    if m:
        d = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        age = (datetime.now() - d).days
        entry["age_days"] = age
        entry["stale"] = age > STALE_DAYS and entry["layer"] == "每日日志"

    if len(path.name) > len("MEMORY.md"):  # 快照文件
        entry["is_snapshot"] = path.name.startswith("MEMORY.md.")

    # 统计内部结构（用于诊断辅助，不替代人工判断）
    entry["headings"] = sum(1 for ln in lines if ln.startswith("#"))
    entry["bullets"] = sum(1 for ln in lines if ln.lstrip().startswith("- "))

    # 死链检查：抽取 Windows 绝对路径并验证存在性
    refs: list[str] = []
    seen = set()
    cands = PATH_CODE_RE.findall(text) + PATH_RE.findall(text)
    for raw in cands:
        cand = raw.rstrip("\\/,.")
        if cand in seen or len(cand) < 5:
            continue
        seen.add(cand)
        refs.append(cand)
    # 只把「像文件」的引用算作死链候选（含扩展名或以 .md/.py/.html 等结尾），
    # 目录级引用（如已清理的工作副本）单独归类，避免噪声
    file_like = [r for r in refs if re.search(r"\.[A-Za-z0-9]{1,6}$", r)]
    missing = [r for r in file_like if not Path(r).exists()]
    gone_dir = [r for r in refs if r not in file_like and not Path(r).exists()]
    entry["ref_count"] = len(refs)
    entry["dead_refs"] = missing
    entry["gone_dirs"] = gone_dir
    return entry


def main() -> int:
    ap = argparse.ArgumentParser(description="扫描记忆文件（只读）")
    ap.add_argument("--workspace", default=".", help="工作区根目录（默认当前目录）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    entries = [stat_file(e) for e in find_memory_files(Path(args.workspace))]
    entries.sort(key=lambda e: (e.get("layer", ""), e.get("project", ""), str(e["path"])))

    if args.json:
        print(json.dumps(entries, ensure_ascii=False, indent=2, default=str))
        return 0

    total_lines = 0
    print(f"扫描时间：{datetime.now():%Y-%m-%d %H:%M}")
    print(f"工作区根：{args.workspace}   （超 {STALE_DAYS} 天的日志标记为过期）\n")
    for e in entries:
        tag = ""
        if e.get("stale"):
            tag += f"  [过期 {e.get('age_days')} 天]"
        if e.get("is_snapshot"):
            tag += "  [历史快照]"
        if e.get("dead_refs"):
            tag += f"  [死链 {len(e['dead_refs'])}]"
        proj = f"{e.get('project', '')}/" if e.get("project") else ""
        print(f"[{e['layer']}] {proj}{e['path'].name}  {e.get('lines', '?')} 行  "
              f"{e.get('mtime', '?')}{tag}")
        total_lines += e.get("lines", 0)
        for d in e.get("dead_refs", [])[:5]:
            print(f"      ✗ 死链（文件不存在）：{d}")
        for d in e.get("gone_dirs", [])[:5]:
            print(f"      · 目录已不在（若为已清理的工作副本属正常留档）：{d}")
    print(f"\n合计 {len(entries)} 个文件、{total_lines} 行。")
    print("下一步：按 SKILL.md 第 2 步逐份诊断（案例/规律/元规则/死链），"
          "第 3 步输出待删清单后停下等用户确认。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
