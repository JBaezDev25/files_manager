# How I Built the Transfer Script

I built this script to make file moves feel guided and transparent. Here is the walkthrough of each step I took to put it together.

## 1) Connectivity guardrail
- Added a quick socket reachability check (to 8.8.8.8:53 with a short timeout) so we bail out early if the machine is offline. Failures log a clear message and show the failure banner.

## 2) Credential prompts (audit-only)
- Prompt for source and destination usernames/passwords using `input` and `getpass`. They are not used for authentication yet—just captured for audit context in the log so future integrations (e.g., SSH/SMB) have a spot ready.

## 3) Source selection and validation
- Ask for comma-separated paths. Expand user shortcuts (`~`), resolve to absolute paths, and validate existence. Reject missing paths immediately to avoid half-done transfers.

## 4) Destination handling
- Prompt for a destination directory, expand/resolve it, and confirm it is a directory. If it does not exist, ask permission to create it and build the directory tree when approved.

## 5) Sizing the work
- Walk every file in the selected sources to sum total bytes. If the total is zero, raise early—no reason to run a copy loop with nothing to move.

## 6) Copy engine
- Use streaming copy in 1 MB chunks so large files do not balloon memory. Preserve metadata with `shutil.copystat`. For folders, mirror the relative structure under the destination while iterating with `rglob`.

## 7) Progress bar
- Track bytes copied versus total. Render a fixed-width bar with percentage and MB counters, updating in place on stdout so users can see steady progress.

## 8) Logging
- Append to `log.txt` next to the script with timestamped entries. Log start context (sources, destination, provided usernames) and completion or failure details with durations and byte counts.

## 9) User-facing banners
- On success, print a clear ASCII banner: `File Transfer Completed`.
- On errors, log the exception and show `!!!Somethign went Wrong!!` so it is unmistakable.

## 10) Usage flow
- Run `python file_transfer.py`.
- Script checks connectivity, prompts for audit credentials, collects sources, confirms/creates destination, then copies while showing progress.
- When done, check `log.txt` for a record of the session.

If we expand this later, the hooks for real authentication (SSH/SMB), drive selection, and richer UI are already sketched into the flow above.
