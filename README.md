# Files Manager

A guided file transfer tool with multi-protocol support (local, SFTP, SMB, FTP), progress bar, checksum verification, and transfer logging.

## Features

- Local, SFTP, SMB, and FTP transfers
- SHA-256 checksum verification after every transfer
- Real-time progress bar with MB/total display
- Resumable transfers (continues where it left off)
- Parallel copy with configurable worker threads
- Wildcard/glob source pattern support
- Automatic transfer logging to `log.txt`
- Optional GUI via `file_transfer_gui.py`

## Requirements

- Python 3.11+
- Dependencies listed in `requirements.txt`

## Setup

**1. Clone the repository**

```bash
git clone https://github.com/al4nbr3/files_manager.git
cd files_manager
```

**2. Create a virtual environment**

```bash
python3 -m venv .venv
source .venv/bin/activate
```

**3. Install dependencies**

```bash
pip install -r requirements.txt
```

## Usage

### CLI (Terminal)

```bash
python file_transfer.py
```

You will be guided through:

1. Network connectivity check
2. Source IP and destination IP entry
3. Optional credentials for auditing
4. Source file/folder paths (comma-separated, wildcards allowed)
5. Destination directory

Example input:
```
Enter source IP: 192.0.2.10
Enter destination IP: 192.0.2.20
Enter source file/folder paths: /home/user/documents, /home/user/*.log
Enter destination directory: /mnt/backup
```

### GUI

```bash
python file_transfer_gui.py
```

Opens a graphical interface with the same options.

## Transfer Protocols

| Protocol | When used |
|----------|-----------|
| Local    | Same machine or mounted paths |
| SFTP     | Remote Linux/Mac servers over SSH |
| SMB      | Windows shared folders / NAS |
| FTP      | Legacy FTP servers |

## Logs

All transfers are logged to `log.txt` in the project directory with timestamps, source/destination IPs, file paths, and success/failure status.

## Notes

- Credentials are never stored to disk — held in memory only during the session
- Verification with SHA-256 runs automatically after every transfer
- Transfer state is saved so interrupted transfers can be resumed
