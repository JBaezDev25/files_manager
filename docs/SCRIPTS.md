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
