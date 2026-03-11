#!/usr/bin/env python3
"""Guided file transfer helper with connectivity check, prompts, progress bar, and logging."""

import fnmatch
import getpass
import os
import shutil
import socket
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from smb.SMBConnection import SMBConnection


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


def prompt_ip(label: str) -> str:
    ip = input(f"Enter {label} IP: ").strip()
    if not ip:
        raise ValueError(f"{label.title()} IP is required.")
    return ip


def _anchor_root(pattern: str) -> Path:
    parts = Path(pattern).expanduser().parts
    anchor_parts: list[str] = []
    for part in parts:
        if any(ch in part for ch in "*?["):
            break
        anchor_parts.append(part)
    if not anchor_parts:
        return Path("/" if pattern.startswith(os.sep) else os.getcwd())
    return Path(*anchor_parts)


def expand_source_spec(spec: str) -> list[Path]:
    expanded_spec = os.path.abspath(os.path.expanduser(spec))

    if not any(ch in expanded_spec for ch in "*?["):
        path = Path(expanded_spec)
        if not path.exists():
            raise FileNotFoundError(f"Source not found: {path}")
        return [path.resolve()]

    anchor = _anchor_root(expanded_spec)
    if not anchor.exists():
        raise FileNotFoundError(f"Base path for pattern does not exist: {anchor}")

    matches: set[Path] = set()
    for root, dirs, files in os.walk(anchor):
        for name in dirs + files:
            candidate = os.path.join(root, name)
            if fnmatch.fnmatch(candidate, expanded_spec):
                matches.add(Path(candidate).resolve())

    if not matches:
        raise FileNotFoundError(f"No matches for source pattern: {spec}")

    return sorted(matches)


def collect_sources() -> list[Path]:
    raw = input(
        "Enter source file/folder paths (comma-separated, '*' allowed): "
    ).strip()
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if not parts:
        raise ValueError("No source paths provided.")

    paths: list[Path] = []
    for spec in parts:
        paths.extend(expand_source_spec(spec))

    unique_paths = []
    seen = set()
    for p in paths:
        if p not in seen:
            unique_paths.append(p)
            seen.add(p)
    return unique_paths


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


def total_bytes(paths: list[Path], include_hidden: bool = True) -> int:
    total = 0
    for p in paths:
        if p.is_file():
            total += p.stat().st_size
        else:
            for root, dirs, files in os.walk(p):
                if not include_hidden:
                    dirs[:] = [name for name in dirs if not name.startswith(".")]
                    files = [name for name in files if not name.startswith(".")]
                for name in files:
                    file = Path(root) / name
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


def copy_file(
    src: Path,
    dst: Path,
    progress: dict[str, int],
    progress_cb: Callable[[int, int, str], None] | None = None,
) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    with src.open("rb") as fsrc, dst.open("wb") as fdst:
        while True:
            chunk = fsrc.read(CHUNK_SIZE)
            if not chunk:
                break
            fdst.write(chunk)
            progress["done"] += len(chunk)
            if progress_cb:
                progress_cb(progress["done"], progress["total"], src.name)
            else:
                print_progress(progress["done"], progress["total"], src.name)
    shutil.copystat(src, dst, follow_symlinks=True)


class _ProgressReader:
    def __init__(
        self,
        wrapped,
        progress: dict[str, int],
        progress_cb: Callable[[int, int, str], None] | None,
        name: str,
    ):
        self._wrapped = wrapped
        self._progress = progress
        self._cb = progress_cb
        self._name = name

    def read(self, size: int = -1) -> bytes:
        data = self._wrapped.read(size)
        self._progress["done"] += len(data)
        if data and self._cb:
            self._cb(self._progress["done"], self._progress["total"], self._name)
        return data

    def seek(self, *args, **kwargs):  # passthrough for compatibility
        return self._wrapped.seek(*args, **kwargs)

    def tell(self, *args, **kwargs):
        return self._wrapped.tell(*args, **kwargs)


def copy_path(
    src: Path,
    dest_root: Path,
    progress: dict[str, int],
    progress_cb: Callable[[int, int, str], None] | None = None,
    include_hidden: bool = True,
) -> None:
    if src.is_file():
        target = dest_root / src.name
        copy_file(src, target, progress, progress_cb)
    else:
        for root, dirs, files in os.walk(src):
            if not include_hidden:
                dirs[:] = [name for name in dirs if not name.startswith(".")]
                files = [name for name in files if not name.startswith(".")]
            for name in files:
                item = Path(root) / name
                rel = item.relative_to(src)
                target = dest_root / src.name / rel
                copy_file(item, target, progress, progress_cb)


def _ensure_remote_dir(
    conn: SMBConnection, share: str, remote_dir: str, base: str = ""
) -> None:
    # remote_dir like "folder/sub"
    parts = [p for p in Path(remote_dir).as_posix().split("/") if p]
    current = base.strip("/")
    for part in parts:
        current = f"{current}/{part}" if current else part
        try:
            conn.createDirectory(share, current)
        except Exception:
            # ignore if exists
            continue


def smb_copy_path(
    src: Path,
    conn: SMBConnection,
    share: str,
    remote_base: str,
    progress: dict[str, int],
    progress_cb: Callable[[int, int, str], None] | None = None,
    include_hidden: bool = True,
) -> None:
    base = remote_base.strip("/")
    if src.is_file():
        rel_path = src.name
        remote_path = f"{base}/{rel_path}" if base else rel_path
        _upload_file(src, conn, share, remote_path, progress, progress_cb)
    else:
        for root, dirs, files in os.walk(src):
            if not include_hidden:
                dirs[:] = [name for name in dirs if not name.startswith(".")]
                files = [name for name in files if not name.startswith(".")]
            for name in files:
                item = Path(root) / name
                rel = item.relative_to(src)
                rel_path = (Path(src.name) / rel).as_posix()
                remote_path = f"{base}/{rel_path}" if base else rel_path
                _upload_file(item, conn, share, remote_path, progress, progress_cb)


def _upload_file(
    src: Path,
    conn: SMBConnection,
    share: str,
    remote_path: str,
    progress: dict[str, int],
    progress_cb: Callable[[int, int, str], None] | None,
) -> None:
    remote_dir = str(Path(remote_path).parent)
    if remote_dir and remote_dir != ".":
        _ensure_remote_dir(conn, share, remote_dir)
    with src.open("rb") as f:
        reader = _ProgressReader(f, progress, progress_cb, src.name)
        conn.storeFile(share, remote_path, reader)
    try:
        mtime = src.stat().st_mtime
        conn.setAttributes(share, remote_path, attrTime=mtime)
    except Exception:
        pass


def main() -> None:
    try:
        print("Checking network connectivity...")
        if not is_online():
            msg = "Network check failed. Please connect and retry."
            print(msg)
            log(msg)
            print("!!!Something went Wrong!!")
            return

        src_ip = prompt_ip("source")
        dst_ip = prompt_ip("destination")

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
            f"SrcIP={src_ip}; DstIP={dst_ip}; "
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
        print("!!!Something went Wrong!!")


if __name__ == "__main__":
    main()
