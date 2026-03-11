#!/usr/bin/env python3
"""Guided file transfer helper with connectivity check, prompts, progress bar, and logging."""

import getpass
import os
import shutil
import socket
import sys
import time
from datetime import datetime
from pathlib import Path


LOG_PATH = Path(__file__).with_name("log.txt")
BAR_WIDTH = 40
CHUNK_SIZE = 1024 * 1024  # 1 MB


def log(msg: str) -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] {msg}\n")


def is_online(host: str = "8.8.8.8", port: int = 53, timeout: int = 3) -> bool:
    try:
        socket.create_connection((host, port), timeout=timeout)
        return True
    except OSError:
        return False


def prompt_credentials(label: str) -> tuple[str, str]:
    print(f"Enter {label} credentials (stored only in memory for this run):")
    user = input("  Username (blank to skip): ").strip()
    if not user:
        return "", ""
    if sys.stdin.isatty():
        pwd = getpass.getpass("  Password (blank to skip): ")
    else:
        print("  Non-interactive mode detected; password input will be echoed.")
        pwd = input("  Password (blank to skip): ")
    return user, pwd


def collect_sources() -> list[Path]:
    raw = input("Enter source file/folder paths (comma-separated): ").strip()
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if not parts:
        raise ValueError("No source paths provided.")
    paths: list[Path] = []
    for p in parts:
        path = Path(p).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(f"Source not found: {path}")
        paths.append(path)
    return paths


def ensure_destination() -> Path:
    dest = Path(input("Enter destination directory: ").strip()).expanduser().resolve()
    if dest.exists() and not dest.is_dir():
        raise NotADirectoryError(f"Destination is not a directory: {dest}")
    if not dest.exists():
        create = (
            input(f"Destination {dest} does not exist. Create it? [y/N]: ")
            .strip()
            .lower()
        )
        if create != "y":
            raise FileNotFoundError("Destination directory not created.")
        dest.mkdir(parents=True, exist_ok=True)
    return dest


def total_bytes(paths: list[Path]) -> int:
    total = 0
    for p in paths:
        if p.is_file():
            total += p.stat().st_size
        else:
            for file in p.rglob("*"):
                if file.is_file():
                    total += file.stat().st_size
    return total


def print_progress(done: int, total: int, current_file: str) -> None:
    ratio = 0 if total == 0 else done / total
    filled = int(ratio * BAR_WIDTH)
    bar = "#" * filled + "-" * (BAR_WIDTH - filled)
    percent = int(ratio * 100)
    sys.stdout.write(
        f"\r[{bar}] {percent:3d}%  {done / 1e6:,.1f}MB/{total / 1e6:,.1f}MB  {current_file[:40]}"
    )
    sys.stdout.flush()


def copy_file(src: Path, dst: Path, progress: dict[str, int]) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    with src.open("rb") as fsrc, dst.open("wb") as fdst:
        while True:
            chunk = fsrc.read(CHUNK_SIZE)
            if not chunk:
                break
            fdst.write(chunk)
            progress["done"] += len(chunk)
            print_progress(progress["done"], progress["total"], src.name)
    shutil.copystat(src, dst, follow_symlinks=True)


def copy_path(src: Path, dest_root: Path, progress: dict[str, int]) -> None:
    if src.is_file():
        target = dest_root / src.name
        copy_file(src, target, progress)
    else:
        for item in src.rglob("*"):
            if item.is_dir():
                continue
            rel = item.relative_to(src)
            target = dest_root / src.name / rel
            copy_file(item, target, progress)


def main() -> None:
    try:
        print("Checking network connectivity...")
        if not is_online():
            msg = "Network check failed. Please connect and retry."
            print(msg)
            log(msg)
            print("!!!Somethign went Wrong!!")
            return

        src_user, src_pwd = prompt_credentials("source (for auditing only)")
        dst_user, dst_pwd = prompt_credentials("destination (for auditing only)")

        sources = collect_sources()
        dest = ensure_destination()

        total = total_bytes(sources)
        if total == 0:
            raise RuntimeError("Nothing to copy (total size is zero).")

        log(
            "Transfer started. Sources: "
            f"{', '.join(map(str, sources))}; Destination: {dest}; "
            f"SrcUser={src_user}; DstUser={dst_user}"
        )

        progress: dict[str, int] = {"done": 0, "total": total}
        start = time.time()
        for src in sources:
            copy_path(src, dest, progress)

        duration = time.time() - start
        print_progress(progress["done"], progress["total"], "done")
        print()  # newline after progress bar
        log(f"Transfer completed in {duration:.2f}s. Bytes: {total}")
        print(
            r"""
  _____ _ _        _______                            _           _ 
 |  ___(_) | ___  |__   __|__ _ _ __  ___  ___  _ __ | |__   ___ | |
 | |_  | | |/ _ \   | | / _ \ '_ \/ __|/ _ \| '_ \| '_ \ / _ \| |
 |  _| | | |  __/   | ||  __/ | | \__ \ (_) | |_) | | | | (_) | |
 |_|   |_|_|\___|   |_| \___|_| |_|___/\___/| .__/|_| |_|\___/|_|
                                           |_|                  
File Transfer Completed
"""
        )
    except Exception as e:
        log(f"Transfer failed: {e}")
        print(f"\nError: {e}")
        print("!!!Somethign went Wrong!!")


if __name__ == "__main__":
    main()
