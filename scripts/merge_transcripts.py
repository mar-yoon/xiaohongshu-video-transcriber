#!/usr/bin/env python3
"""
将 transcripts/ 目录下的所有文字稿合并为一个文件，方便后续蒸馏分析。
输出: transcripts/all_transcripts.md
"""

import os
from pathlib import Path
from datetime import datetime

SCRIPT_DIR = Path(__file__).parent
TRANSCRIPT_DIR = SCRIPT_DIR.parent / "transcripts"
OUTPUT_FILE = TRANSCRIPT_DIR / "all_transcripts.md"

def main():
    txt_files = sorted(TRANSCRIPT_DIR.glob("*.txt"))
    txt_files = [f for f in txt_files if f.name != "all_transcripts.md" and not f.name.endswith("_timestamped.txt")]

    if not txt_files:
        print("transcripts/ 目录下没有找到 .txt 文件")
        return

    print(f"找到 {len(txt_files)} 个文字稿，正在合并...")

    with open(OUTPUT_FILE, "w", encoding="utf-8") as out:
        out.write(f"# 小红书博主文字稿合集\n\n")
        out.write(f"- 合并时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
        out.write(f"- 文件数量: {len(txt_files)}\n")
        out.write(f"\n---\n\n")

        for i, f in enumerate(txt_files, 1):
            content = f.read_text(encoding="utf-8")
            out.write(f"## 文稿 {i}: {f.stem}\n\n")
            out.write(content)
            out.write(f"\n\n---\n\n")

    print(f"合并完成: {OUTPUT_FILE}")
    print(f"总大小: {OUTPUT_FILE.stat().st_size / 1024:.1f} KB")

if __name__ == "__main__":
    main()
