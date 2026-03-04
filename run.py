"""
ExchangeRateMonitor — 頂層入口點

用法：
    python run.py                   # 拉取最新程式碼後啟動
    python run.py --version v1.2.3  # checkout 指定版本後啟動
    python run.py --no-update       # 跳過 git 操作，直接啟動
"""

import argparse
import os
import subprocess
import sys

# 確保專案根目錄在 sys.path 中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _git_update(version: str | None) -> None:
    """
    啟動時同步程式碼。失敗時印出警告但不中止程式。

    - version=None：git pull --ff-only（拉最新）
    - version=<ref>：git fetch + git checkout <ref>（指定版本）
    """
    try:
        if version is None:
            result = subprocess.run(
                ["git", "pull", "--ff-only"],
                capture_output=True, text=True, timeout=30,
            )
            tag = "[git pull]"
        else:
            # 先 fetch 確保遠端的 tag/branch 都同步下來
            subprocess.run(
                ["git", "fetch", "--tags"],
                capture_output=True, text=True, timeout=30,
            )
            result = subprocess.run(
                ["git", "checkout", version],
                capture_output=True, text=True, timeout=30,
            )
            tag = f"[git checkout {version}]"

        output = (result.stdout + result.stderr).strip()
        if result.returncode == 0:
            print(f"{tag} {output or 'OK'}")
        else:
            print(f"{tag} 警告：{output}", file=sys.stderr)

    except FileNotFoundError:
        print("[git] 警告：找不到 git 指令，跳過自動更新。", file=sys.stderr)
    except subprocess.TimeoutExpired:
        print("[git] 警告：操作逾時，跳過自動更新。", file=sys.stderr)
    except Exception as exc:
        print(f"[git] 警告：{exc}", file=sys.stderr)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ExchangeRateMonitor")
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--version",
        metavar="REF",
        help="checkout 指定版本（tag / branch / commit hash）",
    )
    group.add_argument(
        "--no-update",
        action="store_true",
        help="跳過 git 操作，直接啟動",
    )
    args = parser.parse_args()

    if not args.no_update:
        _git_update(args.version)

    # 延遲 import：確保 import 到 git 更新後的最新程式碼
    from src.main import main
    main()
