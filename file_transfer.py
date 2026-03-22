#!/usr/bin/env python3
"""Guided file transfer helper with connectivity check, prompts, progress bar, and logging."""

import fnmatch
import getpass
import hashlib
import os
import shutil
import socket
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from concurrent.futures import ThreadPoolExecutor, as_completed
from ftplib import FTP
from smb.SMBConnection import SMBConnection
import paramiko


def compute_checksum(path: Path, algorithm: str = "sha256") -> str:
    hash_func = hashlib.new(algorithm)
    with path.open("rb") as f:
        while chunk := f.read(CHUNK_SIZE):
            hash_func.update(chunk)
    return hash_func.hexdigest()


def verify_checksum(src: Path, dst: Path, algorithm: str = "sha256") -> bool:
    if not dst.exists():
        return False
    src_hash = compute_checksum(src, algorithm)
    dst_hash = compute_checksum(dst, algorithm)
    return src_hash == dst_hash


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
    try:
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
        try:
            shutil.copystat(src, dst, follow_symlinks=False)
        except OSError:
            pass
    except IOError as e:
        raise IOError(f"Failed to copy {src}: {e}")


class TransferState:
    def __init__(self, state_file: Path):
        self.state_file = state_file
        self.completed_files: set[str] = set()
        self.load()

    def load(self) -> None:
        if self.state_file.exists():
            with self.state_file.open() as f:
                self.completed_files = set(line.strip() for line in f if line.strip())

    def save(self) -> None:
        with self.state_file.open("w") as f:
            for name in self.completed_files:
                f.write(f"{name}\n")

    def is_completed(self, src_name: str) -> bool:
        return src_name in self.completed_files

    def mark_completed(self, src_name: str) -> None:
        self.completed_files.add(src_name)
        self.save()


def copy_file_resume(
    src: Path,
    dst: Path,
    progress: dict[str, int],
    state: TransferState | None = None,
    progress_cb: Callable[[int, int, str], None] | None = None,
) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)

    existing_size = dst.stat().st_size if dst.exists() else 0
    src_size = src.stat().st_size

    if existing_size >= src_size:
        progress["done"] += src_size
        if progress_cb:
            progress_cb(progress["done"], progress["total"], src.name)
        return

    try:
        with src.open("rb") as fsrc, dst.open("ab") as fdst:
            fsrc.seek(existing_size)
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

        try:
            shutil.copystat(src, dst, follow_symlinks=False)
        except OSError:
            pass
    except IOError as e:
        raise IOError(f"Failed to copy {src}: {e}")


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


def verify_copy(src: Path, dest_root: Path, include_hidden: bool = True) -> bool:
    if src.is_file():
        target = dest_root / src.name
        return verify_checksum(src, target)
    else:
        for root, dirs, files in os.walk(src):
            if not include_hidden:
                dirs[:] = [name for name in dirs if not name.startswith(".")]
                files = [name for name in files if not name.startswith(".")]
            for name in files:
                item = Path(root) / name
                rel = item.relative_to(src)
                target = dest_root / src.name / rel
                if not verify_checksum(item, target):
                    return False
    return True


def _copy_single_file(args: tuple) -> None:
    src, target, progress = args
    target.parent.mkdir(parents=True, exist_ok=True)
    with src.open("rb") as fsrc, target.open("wb") as fdst:
        while True:
            chunk = fsrc.read(CHUNK_SIZE)
            if not chunk:
                break
            fdst.write(chunk)
            progress["done"] += len(chunk)


def parallel_copy(
    src: Path,
    dest_root: Path,
    progress: dict[str, int],
    max_workers: int = 4,
) -> None:
    files_to_copy = []

    if src.is_file():
        target = dest_root / src.name
        files_to_copy.append((src, target, progress))
    else:
        for root, dirs, files in os.walk(src):
            for name in files:
                item = Path(root) / name
                rel = item.relative_to(src)
                target = dest_root / src.name / rel
                files_to_copy.append((item, target, progress))

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(_copy_single_file, args) for args in files_to_copy]
        for future in as_completed(futures):
            future.result()


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


def connect_ftp(host: str, user: str, password: str) -> FTP:
    ftp = FTP(host)
    if user and password:
        ftp.login(user, password)
    else:
        ftp.login()
    return ftp


def _ensure_ftp_dir(ftp: FTP, path: str) -> None:
    dirs = [p for p in Path(path).as_posix().split("/") if p]
    current = ""
    for d in dirs:
        current = f"{current}/{d}" if current else d
        try:
            ftp.mkd(current)
        except Exception:
            pass
        try:
            ftp.cwd(current)
        except Exception:
            pass
    ftp.cwd("/")


def ftp_upload_file(
    src: Path,
    ftp: FTP,
    remote_path: str,
    progress: dict[str, int],
    progress_cb: Callable[[int, int, str], None] | None = None,
) -> None:
    remote_dir = str(Path(remote_path).parent)
    if remote_dir and remote_dir != ".":
        _ensure_ftp_dir(ftp, remote_dir)

    with src.open("rb") as f:

        def callback(data: bytes) -> None:
            progress["done"] += len(data)
            if progress_cb:
                progress_cb(progress["done"], progress["total"], src.name)

        ftp.storbinary(f"STOR {remote_path}", f, callback=callback)


def ftp_copy_path(
    src: Path,
    ftp: FTP,
    remote_base: str,
    progress: dict[str, int],
    progress_cb: Callable[[int, int, str], None] | None = None,
    include_hidden: bool = True,
) -> None:
    base = remote_base.strip("/")
    if src.is_file():
        rel_path = src.name
        remote_path = f"{base}/{rel_path}" if base else rel_path
        ftp_upload_file(src, ftp, remote_path, progress, progress_cb)
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
                ftp_upload_file(item, ftp, remote_path, progress, progress_cb)


def connect_sftp(
    host: str, user: str, password: str, port: int = 22
) -> paramiko.SFTPClient:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(host, port=port, username=user, password=password)
    return ssh.open_sftp()


def _ensure_sftp_dir(sftp: paramiko.SFTPClient, path: str) -> None:
    dirs = [p for p in Path(path).as_posix().split("/") if p]
    current = ""
    for d in dirs:
        current = f"{current}/{d}" if current else d
        try:
            sftp.mkdir(current)
        except Exception:
            pass
        try:
            sftp.chdir(current)
        except Exception:
            pass
    sftp.chdir("/")


def sftp_upload_file(
    src: Path,
    sftp: paramiko.SFTPClient,
    remote_path: str,
    progress: dict[str, int],
    progress_cb: Callable[[int, int, str], None] | None = None,
) -> None:
    remote_dir = str(Path(remote_path).parent)
    if remote_dir and remote_dir != ".":
        _ensure_sftp_dir(sftp, remote_dir)

    file_size = src.stat().st_size
    with src.open("rb") as f:

        def callback(total: int, _sent: int) -> None:
            progress["done"] += total
            if progress_cb:
                progress_cb(progress["done"], progress["total"], src.name)

        sftp.putfo(f, remote_path, callback=callback)


def sftp_copy_path(
    src: Path,
    sftp: paramiko.SFTPClient,
    remote_base: str,
    progress: dict[str, int],
    progress_cb: Callable[[int, int, str], None] | None = None,
    include_hidden: bool = True,
) -> None:
    base = remote_base.strip("/")
    if src.is_file():
        rel_path = src.name
        remote_path = f"{base}/{rel_path}" if base else rel_path
        sftp_upload_file(src, sftp, remote_path, progress, progress_cb)
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
                sftp_upload_file(item, sftp, remote_path, progress, progress_cb)


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

        print("\nVerifying copied files...")
        all_verified = True
        for src in sources:
            if not verify_copy(src, dest):
                all_verified = False
                log(f"Verification FAILED for: {src}")

        if not all_verified:
            raise RuntimeError("Verification failed! Checksums don't match.")

        duration = time.time() - start
        print_progress(progress["done"], progress["total"], "done")
        print()  # newline after progress bar
        log(f"Transfer completed and verified in {duration:.2f}s. Bytes: {total}")
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
