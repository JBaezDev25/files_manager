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
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileCreatedEvent

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
    connect_ftp,
    ftp_copy_path,
    parallel_copy,
    copy_file_resume,
    TransferState,
    connect_sftp,
    sftp_copy_path,
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
        self.mode_var = tk.StringVar(value="local")  # local, smb, or ftp
        self.share_var = tk.StringVar()
        self.remote_path_var = tk.StringVar(value="/")
        self.sftp_port_var = tk.IntVar(value=22)

        self.watch_enabled_var = tk.BooleanVar(value=False)
        self.watch_folder_var = tk.StringVar()
        self.watch_auto_transfer_var = tk.BooleanVar(value=True)
        self.parallel_workers_var = tk.IntVar(value=1)

        self.external_host_mode_var = tk.BooleanVar(value=False)

        self.observer: Observer | None = None

        self.progress_var = tk.IntVar()
        self.status_var = tk.StringVar(value="Idle")
        self.connection_state = tk.StringVar(value="disconnected")
        self.cred_state = tk.StringVar(value="unvalidated")
        self.remote_current_path = "/"

        self.queue: Queue = Queue()
        self.worker: threading.Thread | None = None
        self.session_busy = False

        self._build_ui()
        self._on_src_type_changed()
        self._on_dst_type_changed()
        self._schedule_queue_check()

    def _build_ui(self) -> None:
        pad = 4

        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)

        conn_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Connection", menu=conn_menu)
        conn_menu.add_radiobutton(
            label="Local Copy",
            variable=self.mode_var,
            value="local",
            command=self._on_mode_changed,
        )
        conn_menu.add_radiobutton(
            label="SMB Upload",
            variable=self.mode_var,
            value="smb",
            command=self._on_mode_changed,
        )
        conn_menu.add_radiobutton(
            label="FTP Upload",
            variable=self.mode_var,
            value="ftp",
            command=self._on_mode_changed,
        )
        conn_menu.add_radiobutton(
            label="SFTP Upload",
            variable=self.mode_var,
            value="sftp",
            command=self._on_mode_changed,
        )
        conn_menu.add_separator()
        conn_menu.add_command(label="Exit", command=self._on_exit)

        top = ttk.Frame(self.root, padding=pad)
        top.pack(fill=tk.BOTH, expand=True)

        self.notebook = ttk.Notebook(top)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        self.transfer_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.transfer_tab, text="Transfer")

        self.browser_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.browser_tab, text="Remote Browser")

        self.net_frame = ttk.LabelFrame(
            self.transfer_tab, text="Endpoints", padding=pad
        )
        self.net_frame.pack(fill=tk.X, pady=(0, pad))

        ttk.Label(self.net_frame, text="Source:").grid(
            row=0, column=0, sticky=tk.W, padx=(0, pad)
        )
        self.src_type_var = tk.StringVar(value="Local Host")
        src_type_combo = ttk.Combobox(
            self.net_frame,
            textvariable=self.src_type_var,
            values=["Local Host", "Remote Host"],
            width=14,
            state="readonly",
        )
        src_type_combo.grid(row=0, column=1, sticky=tk.W, padx=(0, pad))
        src_type_combo.bind("<<ComboboxSelected>>", self._on_src_type_changed)

        self.src_ip_entry = ttk.Entry(
            self.net_frame, textvariable=self.src_ip_var, width=18
        )
        self.src_ip_entry.grid(row=0, column=2, sticky=tk.W, padx=(pad, 0))
        self.src_ip_entry.grid_remove()

        ttk.Label(self.net_frame, text="Destination:").grid(
            row=1, column=0, sticky=tk.W, padx=(0, pad), pady=(pad / 2, 0)
        )
        self.dst_type_var = tk.StringVar(value="SFTP")
        self.dst_type_combo = ttk.Combobox(
            self.net_frame,
            textvariable=self.dst_type_var,
            values=["SMB", "FTP", "SFTP"],
            width=14,
            state="readonly",
        )
        self.dst_type_combo.grid(
            row=1, column=1, sticky=tk.W, padx=(0, pad), pady=(pad / 2, 0)
        )
        self.dst_type_combo.bind("<<ComboboxSelected>>", self._on_dst_type_changed)

        self.dst_ip_entry = ttk.Entry(
            self.net_frame, textvariable=self.dst_ip_var, width=18
        )
        self.dst_ip_entry.grid(
            row=1, column=2, sticky=tk.W, padx=(pad, 0), pady=(pad / 2, 0)
        )

        mode_frame = ttk.LabelFrame(
            self.transfer_tab, text="Transfer Mode", padding=pad
        )
        mode_frame.pack(fill=tk.X, pady=(0, pad))
        ttk.Radiobutton(
            mode_frame,
            text="Local copy",
            variable=self.mode_var,
            value="local",
            command=self._on_mode_changed,
        ).pack(side=tk.LEFT)
        ttk.Radiobutton(
            mode_frame,
            text="SMB upload",
            variable=self.mode_var,
            value="smb",
            command=self._on_mode_changed,
        ).pack(side=tk.LEFT, padx=(pad, 0))
        ttk.Radiobutton(
            mode_frame,
            text="FTP upload",
            variable=self.mode_var,
            value="ftp",
            command=self._on_mode_changed,
        ).pack(side=tk.LEFT, padx=(pad, 0))
        ttk.Radiobutton(
            mode_frame,
            text="SFTP upload",
            variable=self.mode_var,
            value="sftp",
            command=self._on_mode_changed,
        ).pack(side=tk.LEFT, padx=(pad, 0))

        external_frame = ttk.Frame(self.transfer_tab)
        external_frame.pack(fill=tk.X, pady=(0, pad))
        ttk.Checkbutton(
            external_frame,
            text="External Host Mode (connect two remote servers)",
            variable=self.external_host_mode_var,
            command=self._toggle_external_mode,
        ).pack(anchor=tk.W)

        cred_frame = ttk.LabelFrame(
            self.transfer_tab, text="Credentials (optional)", padding=pad
        )
        cred_frame.pack(fill=tk.X, pady=(0, pad))

        self.src_cred_frame = ttk.Frame(cred_frame)
        self.src_cred_frame.grid(
            row=0, column=0, columnspan=4, sticky=tk.W, pady=(0, pad)
        )

        ttk.Label(self.src_cred_frame, text="Source user").grid(
            row=0, column=0, sticky=tk.W, padx=(0, pad)
        )
        ttk.Entry(self.src_cred_frame, textvariable=self.src_user_var, width=18).grid(
            row=0, column=1, sticky=tk.W
        )
        ttk.Label(self.src_cred_frame, text="Source password").grid(
            row=0, column=2, sticky=tk.W, padx=(pad, pad)
        )
        ttk.Entry(
            self.src_cred_frame, textvariable=self.src_pwd_var, show="*", width=18
        ).grid(row=0, column=3, sticky=tk.W)

        ttk.Label(cred_frame, text="Dest user").grid(
            row=1, column=0, sticky=tk.W, padx=(0, pad)
        )
        ttk.Entry(cred_frame, textvariable=self.dst_user_var, width=18).grid(
            row=1, column=1, sticky=tk.W
        )
        ttk.Label(cred_frame, text="Dest password").grid(
            row=1, column=2, sticky=tk.W, padx=(pad, pad), pady=(pad, 0)
        )
        ttk.Entry(cred_frame, textvariable=self.dst_pwd_var, show="*", width=18).grid(
            row=1, column=3, sticky=tk.W, pady=(pad, 0)
        )

        src_frame = ttk.LabelFrame(self.transfer_tab, text="Sources", padding=pad)
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
        self.include_hidden_check = ttk.Checkbutton(
            pattern_row,
            text="Include hidden",
            variable=self.include_hidden_var,
        )
        self.include_hidden_check.pack(side=tk.LEFT, padx=(pad, 0))

        list_frame = ttk.Frame(src_frame)
        list_frame.pack(fill=tk.BOTH, expand=True)
        self.sources_list = tk.Listbox(list_frame, selectmode=tk.EXTENDED, height=4)
        self.sources_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar = ttk.Scrollbar(
            list_frame, orient=tk.VERTICAL, command=self.sources_list.yview
        )
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.sources_list.config(yscrollcommand=scrollbar.set)
        ttk.Button(
            src_frame, text="Remove Selected", command=self._remove_selected
        ).pack(anchor=tk.E, pady=(pad, 0))

        dest_frame = ttk.LabelFrame(self.transfer_tab, text="Destination", padding=pad)
        dest_frame.pack(fill=tk.X, pady=(0, pad))
        dest_row = ttk.Frame(dest_frame)
        dest_row.pack(fill=tk.X)
        ttk.Entry(dest_row, textvariable=self.dest_var).pack(
            side=tk.LEFT, fill=tk.X, expand=True
        )
        ttk.Button(dest_row, text="Browse", command=self._choose_dest).pack(
            side=tk.LEFT, padx=(pad, 0)
        )
        ttk.Label(dest_row, text="Port:").pack(side=tk.LEFT, padx=(pad, 0))
        ttk.Entry(dest_row, textvariable=self.sftp_port_var, width=6).pack(side=tk.LEFT)

        smb_row = ttk.Frame(dest_frame)
        smb_row.pack(fill=tk.X, pady=(pad / 2, 0))

        ttk.Label(smb_row, text="NAS Preset:").pack(side=tk.LEFT)
        nas_presets = ["Custom", "Synology", "QNAP", "WD My Cloud", "Generic NAS"]
        self.nas_preset_var = tk.StringVar(value="Custom")
        nas_combo = ttk.Combobox(
            smb_row,
            textvariable=self.nas_preset_var,
            values=nas_presets,
            width=12,
            state="readonly",
        )
        nas_combo.pack(side=tk.LEFT, padx=(pad / 2, pad))
        nas_combo.bind("<<ComboboxSelected>>", self._on_nas_preset_selected)

        ttk.Label(smb_row, text="Share:").pack(side=tk.LEFT)
        ttk.Entry(smb_row, textvariable=self.share_var, width=15).pack(
            side=tk.LEFT, padx=(pad / 2, pad)
        )
        ttk.Label(smb_row, text="Path:").pack(side=tk.LEFT)
        ttk.Entry(smb_row, textvariable=self.remote_path_var, width=20).pack(
            side=tk.LEFT, padx=(pad / 2, 0)
        )

        options_frame = ttk.LabelFrame(self.transfer_tab, text="Options", padding=pad)
        options_frame.pack(fill=tk.X, pady=(0, pad))

        watch_row = ttk.Frame(options_frame)
        watch_row.pack(fill=tk.X, pady=(0, pad / 2))
        ttk.Checkbutton(
            watch_row,
            text="Watch folder for new files",
            variable=self.watch_enabled_var,
            command=self._toggle_watch,
        ).pack(side=tk.LEFT)
        ttk.Entry(watch_row, textvariable=self.watch_folder_var, width=30).pack(
            side=tk.LEFT, padx=(pad, 0)
        )
        ttk.Button(watch_row, text="Browse", command=self._choose_watch_folder).pack(
            side=tk.LEFT, padx=(pad / 2, 0)
        )

        perf_row = ttk.Frame(options_frame)
        perf_row.pack(fill=tk.X)
        ttk.Label(perf_row, text="Parallel workers:").pack(side=tk.LEFT)
        ttk.Spinbox(
            perf_row, from_=1, to=8, textvariable=self.parallel_workers_var, width=5
        ).pack(side=tk.LEFT, padx=(pad / 2, 0))

        progress_frame = ttk.Frame(self.transfer_tab)
        progress_frame.pack(fill=tk.X)
        self.progress_bar = ttk.Progressbar(
            progress_frame, variable=self.progress_var, maximum=100
        )
        self.progress_bar.pack(fill=tk.X)
        ttk.Label(progress_frame, textvariable=self.status_var).pack(
            anchor=tk.W, pady=(pad / 2, 0)
        )

        btn_frame = ttk.Frame(self.transfer_tab)
        btn_frame.pack(fill=tk.X, pady=(pad, 0))
        ttk.Button(btn_frame, text="Start Transfer", command=self._start_transfer).pack(
            side=tk.LEFT
        )
        ttk.Button(
            btn_frame, text="Stop Transfer", command=self._on_stop_transfer
        ).pack(side=tk.LEFT, padx=(pad, 0))
        ttk.Button(btn_frame, text="Exit", command=self._on_exit).pack(side=tk.RIGHT)

        conn_frame = ttk.Frame(self.transfer_tab)
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
        self.connect_btn = ttk.Button(
            conn_frame, text="Connect", command=self._start_connect
        )
        self.connect_btn.pack(side=tk.LEFT, padx=(0, pad))
        self.validate_btn = ttk.Button(
            conn_frame,
            text="Validate Credentials",
            command=self._start_validate_credentials,
        )
        self.validate_btn.pack(side=tk.LEFT, padx=(pad, 0))

        self.cred_status_var = tk.StringVar(value="Not validated")
        self.cred_canvas = tk.Canvas(
            conn_frame, width=18, height=18, highlightthickness=0
        )
        self.cred_canvas.pack(side=tk.LEFT, padx=(0, pad))
        self._set_cred_indicator("red")
        ttk.Label(conn_frame, textvariable=self.cred_status_var).pack(
            side=tk.LEFT, padx=(pad, 0)
        )

        self.session_status = tk.StringVar(value="Idle")
        ttk.Label(conn_frame, textvariable=self.session_status).pack(
            side=tk.LEFT, padx=(pad, 0)
        )

        browse_frame = ttk.LabelFrame(
            self.browser_tab, text="Remote directories", padding=pad
        )
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

        nav_frame = ttk.Frame(dst_remote_frame)
        nav_frame.pack(fill=tk.X)
        ttk.Button(nav_frame, text="⬆ Go Back", command=self._remote_go_back).pack(
            side=tk.LEFT
        )
        self.remote_path_label = ttk.Label(nav_frame, text="/")
        self.remote_path_label.pack(side=tk.LEFT, padx=(10, 0))

        ttk.Label(
            dst_remote_frame, text="Destination entries (double-click folder to enter)"
        ).pack(anchor=tk.W)
        self.dst_remote_list = tk.Listbox(dst_remote_frame, height=8)
        self.dst_remote_list.pack(fill=tk.BOTH, expand=True)
        self.dst_remote_list.bind("<Double-Button-1>", self._on_remote_double_click)

        btn_row = ttk.Frame(dst_remote_frame)
        btn_row.pack(fill=tk.X, pady=(pad / 2, 0))
        ttk.Button(
            btn_row,
            text="Use as destination",
            command=self._use_selected_remote_destination,
        ).pack(side=tk.LEFT)
        ttk.Button(btn_row, text="Get from Remote", command=self._get_from_remote).pack(
            side=tk.LEFT, padx=(pad, 0)
        )

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

    def _choose_watch_folder(self) -> None:
        path = filedialog.askdirectory(title="Select folder to watch")
        if path:
            self.watch_folder_var.set(path)

    def _toggle_watch(self) -> None:
        if self.watch_enabled_var.get():
            watch_path = self.watch_folder_var.get().strip()
            if not watch_path:
                messagebox.showerror("Watch Folder", "Please select a folder to watch.")
                self.watch_enabled_var.set(False)
                return
            self._start_watching(watch_path)
        else:
            self._stop_watching()

    def _on_mode_changed(self) -> None:
        mode = self.mode_var.get()
        if mode == "local":
            self.status_var.set(
                "Local Copy Mode - Select source and destination folders"
            )
        elif mode == "smb":
            self.status_var.set("SMB Upload Mode - Enter NAS server details")
        elif mode == "ftp":
            self.status_var.set("FTP Upload Mode - Enter FTP server details")
        elif mode == "sftp":
            self.status_var.set("SFTP Upload Mode - Enter SFTP server details")

    def _on_exit(self) -> None:
        if self.worker and self.worker.is_alive():
            if messagebox.askyesno("Exit", "Transfer in progress. Stop and exit?"):
                self._on_stop_transfer()
                self.root.destroy()
        else:
            self.root.destroy()

    def _on_stop_transfer(self) -> None:
        if self.worker and self.worker.is_alive():
            self.status_var.set("Stopping transfer...")
            self._stop_watching()
            self.queue.put(("stopped", "Transfer stopped by user"))
            self.status_var.set("Transfer stopped")

    def _toggle_external_mode(self) -> None:
        if self.external_host_mode_var.get():
            self.status_var.set("External Host Mode: Connect to 2 remote servers")
        else:
            self.status_var.set("Local Mode: Local source to remote destination")

    def _on_src_type_changed(self, event=None) -> None:
        src_type = self.src_type_var.get()
        dst_type = self.dst_type_var.get()

        if src_type == "Local Host":
            self.src_ip_entry.grid_remove()
            self.mode_var.set("local")
            self._update_transfer_mode_display()

            self.src_cred_frame.grid_remove()
            self.include_hidden_check.grid_remove()
            self.sources_list.config(height=4)
            self.include_hidden_var.set(False)

            self.status_var.set("Source: Local Host selected")
        else:
            self.src_ip_entry.grid()
            self._update_transfer_mode_display()

            self.src_cred_frame.grid()
            self.include_hidden_check.grid()
            self.sources_list.config(height=8)
            self.include_hidden_var.set(True)

            self.status_var.set("Source: Remote Host - Enter server IP")

    def _on_dst_type_changed(self, event=None) -> None:
        dst_type = self.dst_type_var.get()
        self._update_transfer_mode_display()

        if dst_type == "Local Folder":
            self.dst_ip_entry.grid_remove()
            self.status_var.set("Destination: Local folder selected")
        else:
            self.dst_ip_entry.grid()
            self.status_var.set(f"Destination: {dst_type}")

    def _update_transfer_mode_display(self) -> None:
        src_type = self.src_type_var.get()
        dst_type = self.dst_type_var.get()

        if src_type == "Local Host":
            if dst_type == "SMB":
                self.mode_var.set("smb")
                self.status_var.set("Local Host → SMB Upload")
            elif dst_type == "FTP":
                self.mode_var.set("ftp")
                self.status_var.set("Local Host → FTP Upload")
            elif dst_type == "SFTP":
                self.mode_var.set("sftp")
                self.status_var.set("Local Host → SFTP Upload")
        else:
            if dst_type == "SMB":
                self.mode_var.set("smb")
                self.status_var.set("Remote Host → SMB Transfer")
            elif dst_type == "FTP":
                self.mode_var.set("ftp")
                self.status_var.set("Remote Host → FTP Transfer")
            elif dst_type == "SFTP":
                self.mode_var.set("sftp")
                self.status_var.set("Remote Host → SFTP Transfer")

    def _on_nas_preset_selected(self, event=None) -> None:
        preset = self.nas_preset_var.get()
        if preset == "Custom":
            self.status_var.set("Custom SMB/NAS configuration")
        elif preset == "Synology":
            self.share_var.set("volume1")
            self.remote_path_var.set("/")
            self.status_var.set("Synology NAS preset selected")
        elif preset == "QNAP":
            self.share_var.set("Public")
            self.remote_path_var.set("/")
            self.status_var.set("QNAP NAS preset selected")
        elif preset == "WD My Cloud":
            self.share_var.set("Public")
            self.remote_path_var.set("/")
            self.status_var.set("WD My Cloud preset selected")
        elif preset == "Generic NAS":
            self.share_var.set("")
            self.remote_path_var.set("/")
            self.status_var.set("Generic NAS - enter share name manually")

    def _start_watching(self, watch_path: str) -> None:
        gui = self

        class TransferHandler(FileSystemEventHandler):
            def on_created(self, event):
                if event.is_directory:
                    return
                file_path = Path(event.src_path)
                gui.queue.put(("new_file", file_path))

        try:
            self.observer = Observer()
            self.observer.schedule(TransferHandler(), watch_path, recursive=False)
            self.observer.start()
            self.status_var.set(f"Watching: {watch_path}")
        except Exception as e:
            self.status_var.set(f"Watch error: {e}")

    def _stop_watching(self) -> None:
        if self.observer:
            self.observer.stop()
            self.observer.join()
            self.observer = None
        self.status_var.set("Idle")

    def _refresh_sources_list(self) -> None:
        self.sources_list.delete(0, tk.END)
        for p in self.sources:
            self.sources_list.insert(tk.END, str(p))

    def _start_transfer(self) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Transfer", "A transfer is already running.")
            return

        src_type = self.src_type_var.get()
        dst_type = self.dst_type_var.get()

        if src_type == "Remote Host" and not self.src_ip_var.get().strip():
            messagebox.showerror("Transfer", "Source IP is required for Remote Host.")
            return

        if not self.dst_ip_var.get().strip():
            messagebox.showerror("Transfer", "Destination IP is required.")
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
        elif mode in ["ftp", "sftp"]:
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

    def _set_cred_indicator(self, color: str) -> None:
        self.cred_canvas.delete("all")
        self.cred_canvas.create_oval(3, 3, 15, 15, fill=color, outline=color)
        self.cred_status_var.set("Validated" if color == "green" else "Not validated")

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

    def _start_connect(self) -> None:
        src_type = self.src_type_var.get()
        dst_type = self.dst_type_var.get()
        src_ip = self.src_ip_var.get().strip()
        dst_ip = self.dst_ip_var.get().strip()
        dst_user = self.dst_user_var.get().strip()
        dst_pwd = self.dst_pwd_var.get()

        if not dst_ip:
            messagebox.showerror("Connect", "Enter destination IP first.")
            return

        self._set_session_busy(True, "Connecting...")

        def connect_task() -> dict[str, object]:
            try:
                connected_src = True
                connected_dst = True

                if src_type == "Remote Host":
                    self.queue.put(("status", f"Connecting to source {src_ip}..."))
                    try:
                        if dst_type == "SFTP":
                            import paramiko

                            ssh = paramiko.SSHClient()
                            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                            ssh.connect(
                                src_ip, username=dst_user, password=dst_pwd, timeout=10
                            )
                            ssh.close()
                    except Exception as e:
                        connected_src = False

                self.queue.put(("status", f"Connecting to {dst_type} {dst_ip}..."))
                dst_status = "FAILED"
                try:
                    if dst_type == "SFTP":
                        import paramiko

                        ssh = paramiko.SSHClient()
                        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                        ssh.connect(
                            dst_ip, username=dst_user, password=dst_pwd, timeout=10
                        )
                        ssh.close()
                        dst_status = "OK"
                    elif dst_type == "FTP":
                        from ftplib import FTP

                        ftp = FTP(dst_ip)
                        ftp.login(dst_user, dst_pwd)
                        ftp.quit()
                        dst_status = "OK"
                    else:
                        from smb.SMBConnection import SMBConnection

                        conn = SMBConnection(
                            dst_user,
                            dst_pwd,
                            socket.gethostname(),
                            dst_ip,
                            use_ntlm_v2=True,
                        )
                        conn.connect(dst_ip, 445, timeout=10)
                        conn.close()
                        dst_status = "OK"
                except Exception as e:
                    connected_dst = False
                    dst_status = f"FAILED: {str(e)[:40]}"

                if connected_src and connected_dst:
                    self._set_connection_indicator("green")
                    if src_type == "Local Host":
                        src_display = "Local Host"
                    else:
                        src_display = src_ip
                    self.session_status.set(f"{src_display} | {dst_type} | {dst_ip}")

                    dst_dirs = []
                    try:
                        if dst_type == "SFTP":
                            import paramiko

                            print("DEBUG: Connecting to SFTP for listing...")
                            ssh = paramiko.SSHClient()
                            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                            ssh.connect(
                                dst_ip, username=dst_user, password=dst_pwd, timeout=10
                            )
                            sftp = ssh.open_sftp()

                            items = sftp.listdir_attr()
                            folders = []
                            files = []
                            for item in items:
                                mode = item.st_mode
                                if mode and stat.S_ISDIR(mode):
                                    folders.append(item.filename + "/")
                                else:
                                    files.append(item.filename)
                            dst_dirs = sorted(folders) + sorted(files)
                            self.remote_current_path = "/"

                            print(f"DEBUG: SFTP dirs = {dst_dirs}")
                            sftp.close()
                            ssh.close()
                        elif dst_type == "FTP":
                            from ftplib import FTP

                            ftp = FTP(dst_ip)
                            ftp.login(dst_user, dst_pwd)
                            dst_dirs = ftp.nlst()
                            ftp.quit()
                        elif dst_type == "SMB":
                            from smb.SMBConnection import SMBConnection

                            conn = SMBConnection(
                                dst_user,
                                dst_pwd,
                                socket.gethostname(),
                                dst_ip,
                                use_ntlm_v2=True,
                            )
                            conn.connect(dst_ip, 445, timeout=10)
                            shares = conn.listShares()
                            dst_dirs = [s.name for s in shares if not s.isSpecial]
                            conn.close()
                    except Exception as e:
                        print(f"Error listing dirs: {type(e).__name__}: {e}")
                        import traceback

                        traceback.print_exc()
                        dst_dirs = []

                    self.queue.put(
                        (
                            "session_ok",
                            {
                                "msg": f"Connected! {dst_type} {dst_ip} ({dst_status})",
                                "type": "connect",
                                "dst_dirs": dst_dirs,
                            },
                        )
                    )
                    return {
                        "success": True,
                        "msg": f"Connected! {dst_type} {dst_ip} ({dst_status})",
                        "dst_dirs": dst_dirs,
                    }
                else:
                    self._set_connection_indicator("red")
                    if src_type == "Local Host":
                        src_display = "Local Host"
                    else:
                        src_display = src_ip
                    self.session_status.set(f"{src_display} | {dst_type} | FAILED")
                    self.queue.put(
                        ("session_error", f"Connection failed - {dst_status}")
                    )
                    return {"success": False, "error": dst_status}
            except Exception as e:
                self._set_connection_indicator("red")
                self.session_status.set("Connection failed")
                self.queue.put(("session_error", str(e)))
                return {"success": False, "error": str(e)}

        self._run_session_task(connect_task)

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

    def _get_from_remote(self) -> None:
        selection = self.dst_remote_list.curselection()
        if not selection:
            messagebox.showerror(
                "Get from Remote", "Select a file or folder from the remote list."
            )
            return

        remote_path = self.dst_remote_list.get(selection[0])
        dst_ip = self.dst_ip_var.get().strip()
        dst_user = self.dst_user_var.get().strip()
        dst_pwd = self.dst_pwd_var.get()

        if not dst_ip or not dst_user:
            messagebox.showerror("Get from Remote", "Connect to remote server first.")
            return

        local_dest = filedialog.askdirectory(title="Select local destination folder")
        if not local_dest:
            return

        self.progress_var.set(0)
        self.progress_bar.config(mode="determinate")
        self._set_session_busy(True, f"Downloading {remote_path}...")

        def download_task() -> dict[str, object]:
            try:
                import paramiko

                ssh = paramiko.SSHClient()
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                ssh.connect(dst_ip, username=dst_user, password=dst_pwd, timeout=10)
                sftp = ssh.open_sftp()

                remote_full_path = remote_path.rstrip("/")
                local_full_path = Path(local_dest) / remote_path.rstrip("/")

                file_size = sftp.stat(remote_full_path).st_size or 1

                def progress_callback(bytes_so_far):
                    if file_size > 0:
                        percent = int(bytes_so_far * 100 / file_size)
                        self.queue.put(
                            (
                                "progress",
                                (bytes_so_far, file_size, remote_path, percent),
                            )
                        )

                local_full_path.parent.mkdir(parents=True, exist_ok=True)
                sftp.get(
                    remote_full_path, str(local_full_path), callback=progress_callback
                )

                sftp.close()
                ssh.close()
                return {
                    "success": True,
                    "msg": f"Downloaded {remote_path} to {local_dest}",
                }
            except Exception as e:
                return {"success": False, "error": str(e)}

        self._run_session_task(download_task)

    def _on_remote_double_click(self, event=None):
        selection = self.dst_remote_list.curselection()
        if not selection:
            return
        item = self.dst_remote_list.get(selection[0])
        if item.endswith("/"):
            folder_name = item.rstrip("/")
            new_path = (
                self.remote_current_path + "/" + folder_name
                if self.remote_current_path != "/"
                else "/" + folder_name
            )
            self._navigate_remote(new_path)

    def _remote_go_back(self):
        if self.remote_current_path != "/":
            parts = self.remote_current_path.strip("/").rsplit("/", 1)
            if len(parts) > 1:
                new_path = "/" + parts[0]
            else:
                new_path = "/"
            self._navigate_remote(new_path)

    def _navigate_remote(self, path):
        dst_ip = self.dst_ip_var.get().strip()
        dst_user = self.dst_user_var.get().strip()
        dst_pwd = self.dst_pwd_var.get()
        dst_type = self.dst_type_var.get()

        if not dst_ip or not dst_user:
            return

        self._set_session_busy(True, f"Navigating to {path}...")

        def navigate_task() -> dict[str, object]:
            try:
                if dst_type == "SFTP":
                    import paramiko

                    ssh = paramiko.SSHClient()
                    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                    ssh.connect(dst_ip, username=dst_user, password=dst_pwd, timeout=10)
                    sftp = ssh.open_sftp()

                    items = sftp.listdir_attr(path)
                    folders = []
                    files = []
                    for item in items:
                        mode = item.st_mode
                        if mode and stat.S_ISDIR(mode):
                            folders.append(item.filename + "/")
                        else:
                            files.append(item.filename)
                    dst_dirs = sorted(folders) + sorted(files)

                    sftp.close()
                    ssh.close()
                    return {
                        "success": True,
                        "dirs": dst_dirs,
                        "path": path,
                        "type": "navigate",
                    }
                return {
                    "success": False,
                    "error": "Only SFTP supported for now",
                    "type": "navigate",
                }
            except Exception as e:
                return {"success": False, "error": str(e)}

        self._run_session_task(navigate_task)

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
                print(f"QUEUE: {kind} - {item}")
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
        elif ptype == "connect":
            dst_dirs = payload.get("dst_dirs", [])
            self.remote_current_path = "/"
            self.remote_path_label.config(text="/")
            print(f"DEBUG: dst_dirs = {dst_dirs}")
            self._set_connection_indicator("green")
            self._set_cred_indicator("green")
            self.cred_state.set("valid")
            self.status_var.set(msg)
            self.dst_remote_list.delete(0, tk.END)
            if dst_dirs:
                for d in dst_dirs:
                    self.dst_remote_list.insert(tk.END, str(d))
            else:
                self.dst_remote_list.insert(tk.END, "(No folders found)")
            messagebox.showinfo("Connection", msg)
        elif ptype == "navigate":
            dst_dirs = payload.get("dirs", [])
            new_path = payload.get("path", "/")
            self.remote_current_path = new_path
            self.remote_path_label.config(text=new_path)
            self.dst_remote_list.delete(0, tk.END)
            if dst_dirs:
                for d in dst_dirs:
                    self.dst_remote_list.insert(tk.END, str(d))
            else:
                self.dst_remote_list.insert(tk.END, "(Empty)")
        elif ptype == "validate":
            src_dirs = payload.get("src_dirs", [])
            dst_dirs = payload.get("dst_dirs", [])
            self._set_connection_indicator("green")
            self._set_cred_indicator("green")
            self.cred_state.set("valid")
            self.status_var.set(msg)
            self._populate_remote_lists(
                cast(list[str], src_dirs), cast(list[str], dst_dirs)
            )
            messagebox.showinfo("Credentials", msg)

    def _handle_session_error(self, error: str) -> None:
        self._set_session_busy(False)
        self._set_connection_indicator("red")
        self._set_cred_indicator("red")
        self.cred_state.set("invalid")
        self.status_var.set(f"Error: {error}")
        messagebox.showerror("Session", error)


def main() -> None:
    try:
        root = tk.Tk()
        TransferGUI(root)
        root.mainloop()
    except Exception as e:
        import traceback

        traceback.print_exc()
        input("Press Enter to exit...")


if __name__ == "__main__":
    main()
