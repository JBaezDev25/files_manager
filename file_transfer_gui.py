#!/usr/bin/env python3
"""Simple Tkinter GUI for the file transfer helper."""

import threading
from pathlib import Path
from queue import Empty, Queue
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from file_transfer import (
    copy_path,
    expand_source_spec,
    is_online,
    log,
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

        self.progress_var = tk.IntVar()
        self.status_var = tk.StringVar(value="Idle")

        self.queue: Queue = Queue()
        self.worker: threading.Thread | None = None

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

        dest = self.dest_var.get().strip()
        if not dest:
            messagebox.showerror("Transfer", "Destination directory is required.")
            return

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
        )

        self.worker = threading.Thread(
            target=self._run_transfer, args=args, daemon=True
        )
        self.worker.start()

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
    ) -> None:
        try:
            self.queue.put(("status", "Checking network..."))
            if not is_online():
                raise RuntimeError("Network check failed. Connect and retry.")

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
                f"{', '.join(map(str, sources))}; Destination: {dest}; "
                f"SrcIP={src_ip}; DstIP={dst_ip}; "
                f"SrcUser={src_user}; DstUser={dst_user}"
            )

            for src in sources:
                copy_path(
                    src,
                    dest,
                    progress,
                    progress_cb,
                    include_hidden=include_hidden,
                )

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
                self.queue.task_done()
        except Empty:
            pass
        self._schedule_queue_check()


def main() -> None:
    root = tk.Tk()
    TransferGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
