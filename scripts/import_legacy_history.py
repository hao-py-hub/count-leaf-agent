"""将旧版历史 SQLite 数据导入当前项目，不触发任何图像分析。"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path


导入字段 = (
    "run_id, source_label, source_path, source_kind, status, created_at, updated_at, "
    "completed_at, error_message, original_result_json, current_result_json, steps_json, revision"
)


def main() -> int:
    """备份目标库后，将旧记录以默认项目范围幂等导入。"""
    parser = argparse.ArgumentParser(description="导入旧版历史记录，不重跑图片")
    parser.add_argument("--source", required=True, type=Path, help="旧版 history SQLite 文件")
    parser.add_argument("--target", required=True, type=Path, help="当前 history SQLite 文件")
    parser.add_argument("--project-id", default="default", help="导入记录所属项目")
    args = parser.parse_args()

    if not args.source.is_file():
        raise FileNotFoundError(f"找不到源数据库：{args.source}")
    if not args.target.is_file():
        raise FileNotFoundError(f"找不到目标数据库：{args.target}")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = args.target.with_name(f"{args.target.stem}.before-legacy-import-{stamp}{args.target.suffix}")
    shutil.copy2(args.target, backup)

    source = sqlite3.connect(args.source)
    target = sqlite3.connect(args.target)
    try:
        rows = source.execute(f"SELECT {导入字段} FROM analysis_history").fetchall()
        target.execute("BEGIN IMMEDIATE")
        before = target.execute("SELECT COUNT(*) FROM analysis_history").fetchone()[0]
        target.executemany(
            f"""
            INSERT OR IGNORE INTO analysis_history ({导入字段}, project_id)
            VALUES ({','.join('?' for _ in range(13))}, ?)
            """,
            [(*row, args.project_id) for row in rows],
        )
        after = target.execute("SELECT COUNT(*) FROM analysis_history").fetchone()[0]
        target.commit()
    except Exception:
        target.rollback()
        raise
    finally:
        source.close()
        target.close()

    print(f"导入完成：源记录 {len(rows)} 条，新增 {after - before} 条，目标现有 {after} 条。")
    print(f"目标备份：{backup}")
    print("下一步：可在历史页查看记录；本次未触发图像重跑或 VLM 调用。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
