# files_manager — Script Reference

**Status:** Active
**Location:** `~/Wrk-pjtcs/files_manager`
**What this project is:** A file-transfer helper for moving files and folders between
computers — locally, over SMB (Windows/NAS shares), FTP, or SFTP (SSH). It comes as both
a command-line tool and a point-and-click desktop (Tkinter) app, with progress bars,
resumable transfers, checksum verification, and a folder-watcher that can auto-upload new
files as they appear.

## Scripts

### `file_transfer.py`
This is the engine underneath everything — all the actual copying/uploading logic lives
here, and both the command-line tool and the GUI import from it. Run it directly with
`python file_transfer.py` for an interactive, prompt-driven session: it checks you're
online, asks for source and destination IPs (for logging/auditing purposes), asks for
optional credentials, lets you type source paths (files, folders, or wildcard patterns
like `*.jpg`), copies everything to a destination folder with a live progress bar, then
verifies every copied file's checksum (SHA-256) matches the original before declaring
success. Beyond the interactive mode, it also provides the building blocks the GUI uses:
functions to upload files/folders over SMB, FTP, and SFTP; a resumable-copy function that
can pick up a large file transfer where it left off; and a parallel-copy function using
multiple worker threads. Reads/writes: local files, a `log.txt` activity log next to the
script, and (for the resume feature) a small state file tracking which files already
finished. Talks to the network only when you choose SMB/FTP/SFTP mode. Gotcha: passwords
you type are held only in memory for that run and are never written to `log.txt`.

**Why it's written this way:** The transfer functions are plain module-level functions
rather than classes because each backend (local filesystem, SMB via `pysmb`, FTP via
`ftplib`, SFTP via `paramiko`) has such a different native API (callbacks vs. generators
vs. file-like objects) that a shared class hierarchy would mostly exist to paper over
those differences — a lightweight shared convention (a `progress` dict plus an optional
`progress_cb`) lets both the CLI and the GUI report progress uniformly without forcing
every backend through one abstraction. `TransferState` persists completed filenames as a
flat newline-delimited text file rather than a small database, which is enough
bookkeeping for a single operator running one transfer at a time and avoids pulling in a
dependency just to track a set of strings. Passwords are deliberately kept in local
variables only (never written to `log.txt`) because this is a manual, interactive tool
run by one person at a time — there's no multi-user session or credential-vault
requirement to justify anything heavier.

### `file_transfer_gui.py`
This is the point-and-click desktop version — run `python file_transfer_gui.py` to open
a window (requires a display; needs `paramiko` installed, which it checks for on
startup). You pick a transfer mode (Local copy, SMB, FTP, or SFTP) from a menu or radio
buttons, add source files/folders/patterns to a list, pick or browse to a destination,
and click "Start Transfer" to run the copy in a background thread so the window stays
responsive, with a live progress bar and status messages. It also has a "Remote Browser"
tab for browsing folders on a remote SFTP/FTP/SMB server and pulling files down, a
"Check Connection"/"Connect"/"Validate Credentials" set of buttons with red/green status
lights, NAS presets for common brands (Synology, QNAP, WD My Cloud), and an optional
"watch this folder and auto-transfer new files" mode built on the `watchdog` library.
Under the hood it calls the same copy/upload functions defined in `file_transfer.py`.
Reads/writes: the same `log.txt` file, plus whatever local or remote paths you point it
at. Gotcha: closing the window while a transfer is running asks for confirmation first.

**Why it's written this way:** Tkinter was chosen over a heavier GUI toolkit because it
ships with the Python standard library, so this small internal tool needs nothing extra
installed just to get a window on screen. The transfer itself runs on a background
`threading.Thread` that only ever pushes tuples onto a `Queue`, which the main thread
polls every 100ms via `root.after` — Tkinter widgets aren't safe to update from a
non-main thread, so this queue-and-poll pattern is the standard way to keep a long-running
network transfer from freezing the window while still getting live progress into it. The
GUI imports its copy/upload logic straight from `file_transfer.py` rather than
reimplementing any of it, keeping the CLI and GUI as two front ends over one shared
engine. NAS presets (Synology, QNAP, WD My Cloud) are hardcoded as a small fixed list
rather than loaded from a config file, which is reasonable given there are only a
handful of them and they rarely change.

### `test_file_transfer.py`
Automated test suite for `file_transfer.py`, run with `pytest test_file_transfer.py` (or
just `pytest` from the project folder). It checks that checksum computation and
comparison work correctly (including for files that don't match or don't exist), that
byte-size totals for single files and whole directories are calculated correctly, that a
basic file copy actually copies the right content and tracks progress, that copying a
whole directory preserves its structure at the destination, that a copy-then-verify round
trip succeeds, that wildcard source patterns correctly find existing files and correctly
raise an error for patterns that match nothing, and that the network connectivity check
correctly reports online/offline. These tests create and clean up their own temporary
files — they don't touch any real project data. Gotcha: `test_is_online` makes a real
network connection attempt (to 8.8.8.8), so it will fail if run with no internet access.

**Why it's written this way:** Tests use real temporary files and directories
(`tempfile.NamedTemporaryFile`, `tempfile.TemporaryDirectory`) instead of mocking the
filesystem, which is a reasonable trade for a script whose whole job is moving real bytes
between real paths — mocking `open()`/`shutil` calls would test that the mocks were
called correctly rather than that files actually get copied and verified byte-for-byte.
Letting `test_is_online` hit the real internet (8.8.8.8) instead of mocking `socket` is a
deliberate, if fragile, simplification: it exercises the actual connectivity check the
tool relies on before every transfer, at the cost of the test suite depending on network
access being available wherever it runs.
