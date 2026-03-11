#!/usr/bin/env python3
"""Simple Tkinter GUI for the file transfer helper."""

import socket
import stat
import threading
from pathlib import Path
from queue import Empty, Queue
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Callable, cast

try:
    import paramiko
except ImportError as exc:  # pragma: no cover - gui helper
    raise SystemExit(
        "Missing dependency: paramiko. Install with 'pip install paramiko'."
    ) from exc

from smb.SMBConnection import SMBConnection

from file_transfer import (
    copy_path,
    expand_source_spec,
    is_online,
    log,
    smb_copy_path,
    total_bytes,
)


class TransferGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("File Transfer Helper")

        self.sources: list[Path] = []
        self.dest_var = tk.StringVar()
        self.src_ip_var = tk.StringVar()
        self.dst_ip_var = tk.StringVar()
        self.src_user_var = tk.StringVar()
        self.src_pwd_var = tk.StringVar()
        self.dst_user_var = tk.StringVar()
        self.dst_pwd_var = tk.StringVar()
        self.pattern_var = tk.StringVar()
        self.include_hidden_var = tk.BooleanVar(value=True)
        self.mode_var = tk.StringVar(value="local")  # local copy vs smb upload
        self.share_var = tk.StringVar()
        self.remote_path_var = tk.StringVar(value="/")

        self.progress_var = tk.IntVar()
        self.status_var = tk.StringVar(value="Idle")
        self.connection_state = tk.StringVar(value="disconnected")
        self.cred_state = tk.StringVar(value="unvalidated")

        self.queue: Queue = Queue()
        self.worker: threading.Thread | None = None
        self.session_busy = False

        self._build_ui()
        self._schedule_queue_check()

    def _build_ui(self) -> None:
        pad = 6

        top = ttk.Frame(self.root, padding=pad)
        top.pack(fill=tk.BOTH, expand=True)

        net_frame = ttk.LabelFrame(top, text="Endpoints", padding=pad)
        net_frame.pack(fill=tk.X, pady=(0, pad))
        ttk.Label(net_frame, text="Source IP").grid(
            row=0, column=0, sticky=tk.W, padx=(0, pad)
        )
        ttk.Entry(net_frame, textvariable=self.src_ip_var, width=18).grid(
            row=0, column=1, sticky=tk.W
        )
        ttk.Label(net_frame, text="Dest IP").grid(
            row=0, column=2, sticky=tk.W, padx=(pad, pad)
        )
        ttk.Entry(net_frame, textvariable=self.dst_ip_var, width=18).grid(
            row=0, column=3, sticky=tk.W
        )

        mode_frame = ttk.LabelFrame(top, text="Transfer Mode", padding=pad)
        mode_frame.pack(fill=tk.X, pady=(0, pad))
        ttk.Radiobutton(
            mode_frame,
            text="Local copy",
            variable=self.mode_var,
            value="local",
        ).pack(side=tk.LEFT)
        ttk.Radiobutton(
            mode_frame,
            text="SMB upload",
            variable=self.mode_var,
            value="smb",
        ).pack(side=tk.LEFT, padx=(pad, 0))

        cred_frame = ttk.LabelFrame(top, text="Credentials (optional)", padding=pad)
        cred_frame.pack(fill=tk.X, pady=(0, pad))
        ttk.Label(cred_frame, text="Source user").grid(
            row=0, column=0, sticky=tk.W, padx=(0, pad)
        )
        ttk.Entry(cred_frame, textvariable=self.src_user_var, width=18).grid(
            row=0, column=1, sticky=tk.W
        )
        ttk.Label(cred_frame, text="Source password").grid(
            row=0, column=2, sticky=tk.W, padx=(pad, pad)
        )
        ttk.Entry(cred_frame, textvariable=self.src_pwd_var, show="*", width=18).grid(
            row=0, column=3, sticky=tk.W
        )
        ttk.Label(cred_frame, text="Dest user").grid(
            row=1, column=0, sticky=tk.W, padx=(0, pad), pady=(pad, 0)
        )
        ttk.Entry(cred_frame, textvariable=self.dst_user_var, width=18).grid(
            row=1, column=1, sticky=tk.W, pady=(pad, 0)
        )
        ttk.Label(cred_frame, text="Dest password").grid(
            row=1, column=2, sticky=tk.W, padx=(pad, pad), pady=(pad, 0)
        )
        ttk.Entry(cred_frame, textvariable=self.dst_pwd_var, show="*", width=18).grid(
            row=1, column=3, sticky=tk.W, pady=(pad, 0)
        )

        src_frame = ttk.LabelFrame(top, text="Sources", padding=pad)
        src_frame.pack(fill=tk.BOTH, expand=True, pady=(0, pad))

        pattern_row = ttk.Frame(src_frame)
        pattern_row.pack(fill=tk.X, pady=(0, pad))
        ttk.Entry(pattern_row, textvariable=self.pattern_var).pack(
            side=tk.LEFT, fill=tk.X, expand=True
        )
        ttk.Button(pattern_row, text="Add Pattern", command=self._add_pattern).pack(
            side=tk.LEFT, padx=(pad, 0)
        )
        ttk.Button(pattern_row, text="Add Files", command=self._add_files_dialog).pack(
            side=tk.LEFT, padx=(pad, 0)
        )
        ttk.Button(
            pattern_row, text="Add Directory", command=self._add_directory_dialog
        ).pack(side=tk.LEFT, padx=(pad, 0))
        ttk.Checkbutton(
            pattern_row,
            text="Include hidden",
            variable=self.include_hidden_var,
        ).pack(side=tk.LEFT, padx=(pad, 0))

        list_frame = ttk.Frame(src_frame)
        list_frame.pack(fill=tk.BOTH, expand=True)
        self.sources_list = tk.Listbox(list_frame, selectmode=tk.EXTENDED, height=8)
        self.sources_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar = ttk.Scrollbar(
            list_frame, orient=tk.VERTICAL, command=self.sources_list.yview
        )
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.sources_list.config(yscrollcommand=scrollbar.set)
        ttk.Button(
            src_frame, text="Remove Selected", command=self._remove_selected
        ).pack(anchor=tk.E, pady=(pad, 0))

        dest_frame = ttk.LabelFrame(top, text="Destination", padding=pad)
        dest_frame.pack(fill=tk.X, pady=(0, pad))
        dest_row = ttk.Frame(dest_frame)
        dest_row.pack(fill=tk.X)
        ttk.Entry(dest_row, textvariable=self.dest_var).pack(
            side=tk.LEFT, fill=tk.X, expand=True
        )
        ttk.Button(dest_row, text="Browse", command=self._choose_dest).pack(
            side=tk.LEFT, padx=(pad, 0)
        )

        smb_row = ttk.Frame(dest_frame)
        smb_row.pack(fill=tk.X, pady=(pad / 2, 0))
        ttk.Label(smb_row, text="SMB share").pack(side=tk.LEFT)
        ttk.Entry(smb_row, textvariable=self.share_var, width=18).pack(
            side=tk.LEFT, padx=(pad / 2, pad)
        )
        ttk.Label(smb_row, text="Remote path").pack(side=tk.LEFT)
        ttk.Entry(smb_row, textvariable=self.remote_path_var, width=24).pack(
            side=tk.LEFT, padx=(pad / 2, 0)
        )

        progress_frame = ttk.Frame(top)
        progress_frame.pack(fill=tk.X)
        self.progress_bar = ttk.Progressbar(
            progress_frame, variable=self.progress_var, maximum=100
        )
        self.progress_bar.pack(fill=tk.X)
        ttk.Label(progress_frame, textvariable=self.status_var).pack(
            anchor=tk.W, pady=(pad / 2, 0)
        )

        ttk.Button(top, text="Start Transfer", command=self._start_transfer).pack(
            anchor=tk.E, pady=(pad, 0)
        )

        conn_frame = ttk.Frame(top)
        conn_frame.pack(fill=tk.X, pady=(pad / 2, 0))
        self.conn_canvas = tk.Canvas(
            conn_frame, width=18, height=18, highlightthickness=0
        )
        self.conn_canvas.pack(side=tk.LEFT, padx=(0, pad))
        self._set_connection_indicator("red")
        self.check_btn = ttk.Button(
            conn_frame, text="Check Connection", command=self._start_check_connection
        )
        self.check_btn.pack(side=tk.LEFT, padx=(0, pad))
        self.validate_btn = ttk.Button(
            conn_frame,
            text="Validate Credentials",
            command=self._start_validate_credentials,
        )
        self.validate_btn.pack(side=tk.LEFT)
        self.session_status = tk.StringVar(value="Idle")
        ttk.Label(conn_frame, textvariable=self.session_status).pack(
            side=tk.LEFT, padx=(pad, 0)
        )

        browse_frame = ttk.LabelFrame(top, text="Remote directories", padding=pad)
        browse_frame.pack(fill=tk.BOTH, expand=True, pady=(pad / 2, 0))

        src_remote_frame = ttk.Frame(browse_frame)
        src_remote_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, pad))
        ttk.Label(src_remote_frame, text="Source root entries").pack(anchor=tk.W)
        self.src_remote_list = tk.Listbox(src_remote_frame, height=8)
        self.src_remote_list.pack(fill=tk.BOTH, expand=True)
        ttk.Button(
            src_remote_frame,
            text="Add as source",
            command=self._add_selected_remote_source,
        ).pack(anchor=tk.E, pady=(pad / 2, 0))

        dst_remote_frame = ttk.Frame(browse_frame)
        dst_remote_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ttk.Label(dst_remote_frame, text="Destination root entries").pack(anchor=tk.W)
        self.dst_remote_list = tk.Listbox(dst_remote_frame, height=8)
        self.dst_remote_list.pack(fill=tk.BOTH, expand=True)
        ttk.Button(
            dst_remote_frame,
            text="Use as destination",
            command=self._use_selected_remote_destination,
        ).pack(anchor=tk.E, pady=(pad / 2, 0))

    def _add_sources(self, new_paths: list[Path]) -> None:
        for p in new_paths:
            if p not in self.sources:
                self.sources.append(p)
        self._refresh_sources_list()

    def _add_pattern(self) -> None:
        spec = self.pattern_var.get().strip()
        if not spec:
            return
        try:
            matches = expand_source_spec(spec)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Add Pattern", str(e))
            return
        self._add_sources(matches)
        self.pattern_var.set("")

    def _add_files_dialog(self) -> None:
        paths = filedialog.askopenfilenames(title="Select source files")
        if paths:
            self._add_sources([Path(p) for p in paths])

    def _add_directory_dialog(self) -> None:
        path = filedialog.askdirectory(title="Select source directory")
        if path:
            self._add_sources([Path(path)])

    def _remove_selected(self) -> None:
        selection = list(self.sources_list.curselection())
        for index in reversed(selection):
            self.sources.pop(index)
        self._refresh_sources_list()

    def _choose_dest(self) -> None:
        path = filedialog.askdirectory(title="Select destination directory")
        if path:
            self.dest_var.set(path)

    def _refresh_sources_list(self) -> None:
        self.sources_list.delete(0, tk.END)
        for p in self.sources:
            self.sources_list.insert(tk.END, str(p))

    def _start_transfer(self) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Transfer", "A transfer is already running.")
            return

        if not self.src_ip_var.get().strip() or not self.dst_ip_var.get().strip():
            messagebox.showerror("Transfer", "Source and destination IPs are required.")
            return

        if not self.sources:
            messagebox.showerror("Transfer", "Add at least one source path.")
            return

        mode = self.mode_var.get()
        dest = self.dest_var.get().strip()
        share = self.share_var.get().strip()
        remote_path = self.remote_path_var.get().strip()
        if mode == "local":
            if not dest:
                messagebox.showerror("Transfer", "Destination directory is required.")
                return
        elif mode == "smb":
            if not share:
                messagebox.showerror("Transfer", "SMB share name is required.")
                return
            if not remote_path:
                remote_path = "/"

        self.progress_var.set(0)
        self.progress_bar.config(mode="determinate")
        self.status_var.set("Starting...")

        args = (
            self.src_ip_var.get().strip(),
            self.dst_ip_var.get().strip(),
            self.src_user_var.get().strip(),
            self.src_pwd_var.get(),
            self.dst_user_var.get().strip(),
            self.dst_pwd_var.get(),
            self.sources.copy(),
            Path(dest).expanduser(),
            self.include_hidden_var.get(),
            mode,
            share,
            remote_path,
        )

        self.worker = threading.Thread(
            target=self._run_transfer, args=args, daemon=True
        )
        self.worker.start()

    def _probe_ip(self, ip: str, ports: tuple[int, ...] = (22, 80, 443)) -> bool:
        for port in ports:
            try:
                with socket.create_connection((ip, port), timeout=2):
                    return True
            except OSError:
                continue
        return False

    def _set_connection_indicator(self, color: str) -> None:
        self.conn_canvas.delete("all")
        self.conn_canvas.create_oval(3, 3, 15, 15, fill=color, outline=color)
        self.connection_state.set("connected" if color == "green" else "disconnected")

    def _start_check_connection(self) -> None:
        src_ip = self.src_ip_var.get().strip()
        dst_ip = self.dst_ip_var.get().strip()
        if not src_ip or not dst_ip:
            messagebox.showerror(
                "Connection", "Enter source and destination IPs first."
            )
            return
        self._set_session_busy(True, "Checking connection...")
        self._run_session_task(lambda: self._check_connection_task(src_ip, dst_ip))

    def _check_connection_task(self, src_ip: str, dst_ip: str) -> dict[str, object]:
        src_ok = self._probe_ip(src_ip)
        dst_ok = self._probe_ip(dst_ip)
        if not (src_ok and dst_ok):
            problems = []
            if not src_ok:
                problems.append(f"Source {src_ip} not reachable")
            if not dst_ok:
                problems.append(f"Destination {dst_ip} not reachable")
            raise RuntimeError("; ".join(problems))
        return {"type": "check", "msg": "Connection OK"}

    def _open_sftp(
        self, host: str, username: str, password: str, timeout: int = 6
    ) -> paramiko.SFTPClient:
        try:
            sock = socket.create_connection((host, 22), timeout=timeout)
            sock.settimeout(timeout)
        except socket.timeout as e:
            raise TimeoutError(f"SSH connect to {host}:22 timed out") from e
        transport = paramiko.Transport(sock)
        transport.banner_timeout = timeout
        transport.auth_timeout = timeout
        try:
            transport.connect(username=username, password=password)
        except socket.timeout as e:
            raise TimeoutError(f"SSH auth to {host}:22 timed out") from e
        sftp = cast(paramiko.SFTPClient, paramiko.SFTPClient.from_transport(transport))
        try:
            chan = sftp.get_channel()
            if chan:
                chan.settimeout(timeout)
        except Exception:
            pass
        return sftp

    def _list_remote_root(
        self, host: str, username: str, password: str, timeout: int = 6
    ) -> list[str]:
        entries: list[str] = []
        sftp = self._open_sftp(host, username, password, timeout=timeout)
        try:
            for item in sftp.listdir_attr("/"):
                mode = item.st_mode or 0
                if stat.S_ISDIR(mode):
                    name = item.filename
                    if not self.include_hidden_var.get() and name.startswith("."):
                        continue
                    entries.append(name)
        finally:
            sftp.close()
        return sorted(entries)

    def _start_validate_credentials(self) -> None:
        src_ip = self.src_ip_var.get().strip()
        dst_ip = self.dst_ip_var.get().strip()
        src_user = self.src_user_var.get().strip()
        dst_user = self.dst_user_var.get().strip()
        src_pwd = self.src_pwd_var.get()
        dst_pwd = self.dst_pwd_var.get()

        if not src_ip or not dst_ip or not src_user or not dst_user:
            messagebox.showerror(
                "Credentials",
                "Enter source/destination IPs and usernames before validating.",
            )
            return

        self._set_session_busy(True, "Validating credentials...")
        self._run_session_task(
            lambda: self._validate_credentials_task(
                src_ip, dst_ip, src_user, src_pwd, dst_user, dst_pwd
            )
        )

    def _validate_credentials_task(
        self,
        src_ip: str,
        dst_ip: str,
        src_user: str,
        src_pwd: str,
        dst_user: str,
        dst_pwd: str,
    ) -> dict[str, object]:
        src_dirs = self._list_remote_root(src_ip, src_user, src_pwd)
        dst_dirs = self._list_remote_root(dst_ip, dst_user, dst_pwd)
        return {
            "type": "validate",
            "src_dirs": src_dirs,
            "dst_dirs": dst_dirs,
            "msg": "Credentials validated and directories fetched.",
        }

    def _run_session_task(self, worker: Callable[[], dict[str, object]]) -> None:
        def runner() -> None:
            try:
                payload = worker()
                self.queue.put(("session_ok", payload))
            except Exception as e:  # noqa: BLE001
                self.queue.put(("session_error", str(e)))

        threading.Thread(target=runner, daemon=True).start()

    def _set_session_busy(self, busy: bool, message: str | None = None) -> None:
        self.session_busy = busy
        state = tk.DISABLED if busy else tk.NORMAL
        self.check_btn.config(state=state)
        self.validate_btn.config(state=state)
        if message:
            self.status_var.set(message)
            self.session_status.set(message)
        if busy:
            self._set_connection_indicator("orange")
        else:
            self.session_status.set("Idle")

    def _populate_remote_lists(
        self, src_entries: list[str], dst_entries: list[str]
    ) -> None:
        self.src_remote_list.delete(0, tk.END)
        for name in src_entries:
            self.src_remote_list.insert(tk.END, f"/{name}")
        self.dst_remote_list.delete(0, tk.END)
        for name in dst_entries:
            self.dst_remote_list.insert(tk.END, f"/{name}")

    def _add_selected_remote_source(self) -> None:
        selection = self.src_remote_list.curselection()
        if not selection:
            return
        for idx in selection:
            path = Path(self.src_remote_list.get(idx))
            if path not in self.sources:
                self.sources.append(path)
        self._refresh_sources_list()

    def _use_selected_remote_destination(self) -> None:
        selection = self.dst_remote_list.curselection()
        if not selection:
            return
        dest = self.dst_remote_list.get(selection[0])
        self.dest_var.set(dest)

    def _run_transfer(
        self,
        src_ip: str,
        dst_ip: str,
        src_user: str,
        src_pwd: str,
        dst_user: str,
        dst_pwd: str,
        sources: list[Path],
        dest: Path,
        include_hidden: bool,
        mode: str,
        share: str,
        remote_path: str,
    ) -> None:
        try:
            self.queue.put(("status", "Checking network..."))
            if not is_online():
                raise RuntimeError("Network check failed. Connect and retry.")

            if mode == "local":
                if not dest.exists():
                    dest.mkdir(parents=True, exist_ok=True)
                if not dest.is_dir():
                    raise NotADirectoryError(f"Destination is not a directory: {dest}")

            total = total_bytes(sources, include_hidden=include_hidden)
            if total == 0:
                raise RuntimeError("Nothing to copy (total size is zero).")

            self.queue.put(("status", "Copying..."))

            progress = {"done": 0, "total": total}

            def progress_cb(done: int, total_bytes_val: int, current: str) -> None:
                self.queue.put(("progress", done, total_bytes_val, current))

            log(
                "Transfer started. Sources: "
                f"{', '.join(map(str, sources))}; "
                f"Dest={'local ' + str(dest) if mode == 'local' else f'SMB {share}:{remote_path}'}; "
                f"Mode={mode}; SrcIP={src_ip}; DstIP={dst_ip}; "
                f"SrcUser={src_user}; DstUser={dst_user}"
            )

            if mode == "local":
                for src in sources:
                    copy_path(
                        src,
                        dest,
                        progress,
                        progress_cb,
                        include_hidden=include_hidden,
                    )
            else:
                self.queue.put(("status", "Connecting to SMB..."))
                conn = SMBConnection(
                    dst_user,
                    dst_pwd,
                    socket.gethostname(),
                    dst_ip,
                    use_ntlm_v2=True,
                    is_direct_tcp=True,
                )
                if not conn.connect(dst_ip, 445, timeout=6):
                    raise RuntimeError("SMB connection failed")
                try:
                    for src in sources:
                        smb_copy_path(
                            src,
                            conn,
                            share,
                            remote_path,
                            progress,
                            progress_cb,
                            include_hidden=include_hidden,
                        )
                finally:
                    try:
                        conn.close()
                    except Exception:
                        pass

            log(f"Transfer completed. Bytes: {total}")
            self.queue.put(("done", "File Transfer Completed"))
        except Exception as e:  # noqa: BLE001
            log(f"Transfer failed: {e}")
            self.queue.put(("error", str(e)))

    def _schedule_queue_check(self) -> None:
        self.root.after(100, self._process_queue)

    def _process_queue(self) -> None:
        try:
            while True:
                item = self.queue.get_nowait()
                kind = item[0]
                if kind == "status":
                    self.status_var.set(item[1])
                elif kind == "progress":
                    _, done, total, current = item
                    percent = 0 if total == 0 else int(done * 100 / total)
                    self.progress_var.set(percent)
                    self.status_var.set(f"{percent}% - {current}")
                elif kind == "done":
                    self.progress_var.set(100)
                    self.status_var.set(item[1])
                    messagebox.showinfo("Transfer", item[1])
                elif kind == "error":
                    self.progress_bar.config(mode="determinate")
                    self.status_var.set(f"Error: {item[1]}")
                    messagebox.showerror("Transfer", item[1])
                elif kind == "session_ok":
                    self._handle_session_ok(item[1])
                elif kind == "session_error":
                    self._handle_session_error(item[1])
                self.queue.task_done()
        except Empty:
            pass
        self._schedule_queue_check()

    def _handle_session_ok(self, payload: dict[str, object]) -> None:
        self._set_session_busy(False)
        msg = str(payload.get("msg", ""))
        ptype = payload.get("type")
        if ptype == "check":
            self._set_connection_indicator("green")
            self.status_var.set(msg)
            messagebox.showinfo("Connection", msg)
        elif ptype == "validate":
            src_dirs = payload.get("src_dirs", [])
            dst_dirs = payload.get("dst_dirs", [])
            self._set_connection_indicator("green")
            self.cred_state.set("valid")
            self.status_var.set(msg)
            self._populate_remote_lists(
                cast(list[str], src_dirs), cast(list[str], dst_dirs)
            )
            messagebox.showinfo("Credentials", msg)

    def _handle_session_error(self, error: str) -> None:
        self._set_session_busy(False)
        self._set_connection_indicator("red")
        self.cred_state.set("invalid")
        self.status_var.set(f"Error: {error}")
        messagebox.showerror("Session", error)


def main() -> None:
    root = tk.Tk()
    TransferGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
