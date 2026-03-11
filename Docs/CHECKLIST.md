# Transfer Script Checklist

- Verify network connectivity before any transfer begins.
- Validate all source paths exist and are readable; flag and skip missing items.
- Confirm the destination is writable and has sufficient free space for the transfer.
- Prompt for source and destination IPs and optional credentials for audit; fail fast on SSH/SFTP timeouts.
- Collect source files/folders via patterns, file/folder pickers, and allow wildcards that include hidden items when enabled.
- Ask for destination directory (GUI browse), allow creating it if missing, and ensure it is a directory.
- Support selectable transfer modes: local copy or SMB upload; require SMB share name/remote path when chosen.
- Copy all selected files and folders into the destination (preserve structure and metadata for local copies; preserve timestamps where possible for SMB).
- Show a live progress indicator in the GUI based on total bytes copied.
- Emit per-item checksums after copy and log verification results.
- Write transfer activity to `log.txt` with timestamps and context.
- Display success banner `File Transfer Completed` when done.
- Display failure banner `!!!Something went Wrong!!` on errors and include the exit code and failed paths.
