# syncdate - Sync Media Dates

## Purpose

This tool was created to solve a common problem when editing or compressing media files: the loss of original creation dates. When you edit a video or photo, or compress it to reduce file size, the new file typically gets the current date as its creation time, losing the original capture date. This script synchronizes the metadata dates of newly processed files with their original counterparts, preserving the authentic creation timestamps.

## Overview

A tool for properly synchronizing media dates. This tool updates both embedded metadata (EXIF/QuickTime) AND filesystem timestamps.

## Features

- **Restore from own metadata**: Reads the best date from file metadata and applies it to all relevant metadata tags
- **Sync from other files/folders**: Synchronizes dates based on basename matching
- **Cross-platform**: Supports macOS, Linux, and Windows
- **Dual update**: Updates both embedded metadata and filesystem timestamps

## Dependencies

### System Requirements

1. **Python 3.6+** - Script uses modern Python 3 features such as:
   - `pathlib.Path`
   - Type hints (`typing` module)
   - f-strings

2. **ExifTool** - External tool that MUST be available in PATH
   - Download from: https://exiftool.org/
   - For macOS: `brew install exiftool`
   - For Ubuntu/Debian: `sudo apt install libimage-exiftool-perl`
   - For Windows: Download executable and add to PATH

### Python Dependencies

#### Built-in Modules (no additional installation required):
- `argparse` - Command line argument parsing
- `json` - JSON output parsing from exiftool
- `os` - Operating system operations
- `shutil` - File and directory utilities
- `subprocess` - Running external commands (exiftool)
- `sys` - Python system access
- `pathlib` - Modern path manipulation
- `typing` - Type hints for Dict, List, Optional, Tuple
- `collections.defaultdict` - Dictionary with default values

#### Optional Dependencies:
- `colorama` - For Windows console color support (optional)
  - Only used on Windows to improve color display
  - Install with: `pip install colorama`
  - If not available, application runs normally without colors on Windows

## Installation

1. **Install ExifTool** (REQUIRED):
   ```bash
   # macOS
   brew install exiftool
   
   # Ubuntu/Debian
   sudo apt install libimage-exiftool-perl
   
   # Windows - download from https://exiftool.org/
   ```

2. **Install colorama for Windows** (optional):
   ```bash
   pip install colorama
   ```

3. **Global Installation** (optional - to use as `syncdate` command anywhere):
   ```bash
   # Copy to /usr/local/bin without .py extension
   sudo cp syncdate.py /usr/local/bin/syncdate
   sudo chmod +x /usr/local/bin/syncdate
   ```

4. **Or make script executable locally**:
   ```bash
   chmod +x syncdate.py
   ```

## Usage

### Mode 1: Restore from own metadata

```bash
# If globally installed:
syncdate --file myvideo
syncdate --folder /path/to/media --recursive

# Or run with Python:
python3 syncdate.py --file myvideo

# Or if locally executable:
./syncdate.py --file myvideo
```

### Mode 2: Sync from other files/folders

```bash
# If globally installed:
syncdate --folder /path/encoded --sync-date-from /path/originals
syncdate --file /path/encoded/movie.mp4 --sync-date-from /path/originals/movie_original.mov
```
# Or with Python:
```bash
python3 syncdate.py --folder /path/encoded --sync-date-from /path/originals
python3 syncdate.py --file /path/encoded/movie.mp4 --sync-date-from /path/originals/movie_original.mov
```

### Options

- `--recursive`: Recurse into subfolders (only with --folder)
- `--quiet`: Minimal output
- `--src-recursive` / `--no-src-recursive`: Control recursion in source folder (default: true)
- `--case-insensitive` / `--no-case-insensitive`: Case-insensitive basename matching (default: true)

## Supported Media Formats

### Video
- `.mp4`, `.mov`, `.m4v`, `.3gp`, `.3g2`, `.avi`, `.mts`, `.m2ts`, `.wmv`
- Metadata tags: QuickTime:CreationDate, QuickTime:CreateDate, MediaCreateDate, etc.

### Photo
- `.jpg`, `.jpeg`, `.heic`, `.heif`, `.png`, `.tif`, `.tiff`, `.webp`
- RAW formats: `.dng`, `.cr2`, `.nef`, `.arw`, `.rw2`
- Metadata tags: EXIF:DateTimeOriginal, EXIF:CreateDate, XMP:CreateDate, etc.

## Notes

- Hidden files (prefix '.') are ignored
- This tool updates both embedded metadata AND filesystem timestamps by default
- If filesystem doesn't support create-time, only Modified time is set
- Metadata tag priorities differ for video and photo for optimal results

## Troubleshooting

### Error: 'exiftool' not found in PATH
- Make sure ExifTool is installed and in PATH
- Test with: `exiftool -ver`

### Permission errors
- Make sure files are not read-only
- Run with appropriate permissions

### No usable time tag found
- File doesn't have valid date metadata
- Try using sync mode from another file that has complete metadata

## Version

Current version: 2.0.0

## License

This script uses ExifTool which has its own license. Make sure to comply with ExifTool's license when using this tool.