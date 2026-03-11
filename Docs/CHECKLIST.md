# Transfer Script Checklist

- Verify network connectivity before any transfer begins.
- Validate all source paths exist and are readable; flag and skip missing items.
- Confirm the destination is writable and has sufficient free space for the transfer.
- Prompt for optional source and destination login credentials for audit.
- Collect user-selected source files and folders (comma-separated input).
- Ask for destination directory, allow creating it if missing, and ensure it is a directory.
- Offer destination login capture alongside destination path selection.
- Copy all selected files and folders into the destination (preserve structure and metadata).
- Show a live progress bar based on total bytes copied.
- Emit per-item checksums after copy and log verification results.
- Write transfer activity to `log.txt` with timestamps and context.
- Display success banner `File Transfer Completed` when done.
- Display failure banner `!!!Something went Wrong!!` on errors and include the exit code and failed paths.
