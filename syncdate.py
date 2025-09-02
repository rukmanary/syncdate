#!/usr/bin/env python3
# syncdate: Sync media dates the right way.
# Default behavior: update embedded metadata (EXIF/QuickTime) AND filesystem timestamps.
# Modes:
#   - Restore from the file's own metadata
#   - Sync date from another file/folder (match by basename)
#
# Requirements: exiftool in PATH (we call the bundled/global exiftool).
# Cross-platform: macOS, Linux, Windows (PowerShell/cmd).

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from collections import defaultdict

APP_NAME = "syncdate"
VERSION = "2.0.0"

# ---------- Colors ----------
class Colors:
    RESET  = "\033[0m"
    GREEN  = "\033[32m"
    RED    = "\033[31m"
    YELLOW = "\033[33m"

def _enable_ansi():
    if os.name == "nt":
        try:
            import colorama  # type: ignore
            colorama.just_fix_windows_console()
        except Exception:
            pass
_enable_ansi()

def c(text: str, color: str) -> str:
    if sys.stdout.isatty():
        return f"{color}{text}{Colors.RESET}"
    return text

# ---------- Known tags & formats ----------
VIDEO_TAG_PRIORITY = [
    "QuickTime:CreationDate",
    "QuickTime:CreateDate",
    "QuickTime:MediaCreateDate",
    "QuickTime:TrackCreateDate",
    "H264:DateTimeOriginal",
    "EXIF:CreateDate",
    "EXIF:DateTimeOriginal",
    "XMP:CreateDate",
    "XMP:DateCreated",
]
PHOTO_TAG_PRIORITY = [
    "EXIF:DateTimeOriginal",
    "EXIF:CreateDate",
    "XMP:CreateDate",
    "XMP:DateCreated",
    "QuickTime:CreationDate",  # beberapa HEIC/JPG bisa punya ini
    "PNG:CreationTime",
]

VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".3gp", ".3g2", ".avi", ".mts", ".m2ts", ".wmv"}
PHOTO_EXTS = {".jpg", ".jpeg", ".heic", ".heif", ".png", ".tif", ".tiff", ".webp", ".dng", ".cr2", ".nef", ".arw", ".rw2"}

# ---------- Helpers ----------
def is_hidden(p: Path) -> bool:
    return p.name.startswith(".")

def is_video(p: Path) -> bool:
    return p.suffix.lower() in VIDEO_EXTS

def is_photo(p: Path) -> bool:
    return p.suffix.lower() in PHOTO_EXTS

def ensure_exiftool():
    if shutil.which("exiftool") is None:
        sys.stderr.write(c("ERROR: 'exiftool' not found in PATH. Install or bundle it.\n", Colors.RED))
        sys.exit(1)

def exiftool_json(path: Path) -> Dict[str, str]:
    cmd = ["exiftool", "-api", "QuickTimeUTC=1", "-a", "-G1", "-s", "-time:all", "-j", str(path)]
    out = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
    data = json.loads(out.decode("utf-8", errors="ignore"))
    return data[0] if data else {}

def pick_best_time_tag(tags: Dict[str, str], path: Path) -> Optional[Tuple[str, str]]:
    if is_video(path):
        priority = VIDEO_TAG_PRIORITY
    elif is_photo(path):
        priority = PHOTO_TAG_PRIORITY
    else:
        seen = set(); priority = []
        for lst in (VIDEO_TAG_PRIORITY, PHOTO_TAG_PRIORITY):
            for t in lst:
                if t not in seen:
                    seen.add(t); priority.append(t)
    for tag in priority:
        if tag in tags and str(tags[tag]).strip():
            return tag, str(tags[tag]).strip()
    # last resort
    for k, v in tags.items():
        if (k.endswith("CreateDate") or k.endswith("DateTimeOriginal")) and str(v).strip():
            return k, str(v).strip()
    return None

# ---------- Core setters (metadata + filesystem) ----------
def set_metadata_dates_from_value(path: Path, value: str) -> Tuple[bool, str]:
    """
    Set embedded metadata dates to 'value'.
    - Photos: DateTimeOriginal, CreateDate, ModifyDate
    - Videos: QuickTime:CreateDate/ModifyDate, MediaCreateDate, TrackCreateDate/ModifyDate, ModifyDate
    """
    if is_photo(path):
        args = [
            "-overwrite_original",
            f"-DateTimeOriginal={value}",
            f"-CreateDate={value}",
            f"-ModifyDate={value}",
        ]
    else:
        # treat as video/other
        args = [
            "-overwrite_original",
            f"-QuickTime:CreateDate={value}",
            f"-QuickTime:ModifyDate={value}",
            f"-MediaCreateDate={value}",
            f"-TrackCreateDate={value}",
            f"-TrackModifyDate={value}",
            f"-ModifyDate={value}",
        ]
    cmd = ["exiftool"] + args + [str(path)]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
        return True, out.decode("utf-8", errors="ignore").strip()
    except subprocess.CalledProcessError as e:
        return False, e.output.decode(errors="ignore")

def set_filesystem_dates_from_value(path: Path, value: str) -> Tuple[bool, str]:
    """
    Set FileCreateDate and FileModifyDate of the filesystem to 'value'.
    If FileCreateDate unsupported, fallback to ModifyDate only.
    """
    try:
        out = subprocess.check_output([
            "exiftool", "-overwrite_original",
            f"-FileCreateDate={value}", f"-FileModifyDate={value}", str(path)
        ], stderr=subprocess.STDOUT)
        return True, out.decode("utf-8", errors="ignore").strip()
    except subprocess.CalledProcessError as e:
        msg = e.output.decode(errors="ignore")
        try:
            out2 = subprocess.check_output([
                "exiftool", "-overwrite_original",
                f"-FileModifyDate={value}", str(path)
            ], stderr=subprocess.STDOUT)
            return True, "(create date unsupported) " + out2.decode("utf-8", errors="ignore").strip()
        except subprocess.CalledProcessError as e2:
            return False, msg + "\n" + e2.output.decode(errors="ignore")

def process_file_restore_from_own_metadata(path: Path) -> Tuple[str, bool, str]:
    """
    Read best time from the file's own metadata, write it back into all relevant metadata tags,
    then sync filesystem timestamps.
    """
    try:
        tags = exiftool_json(path)
        picked = pick_best_time_tag(tags, path)
        if not picked:
            return (str(path), False, "No usable time tag found in metadata")
        src_tag, value = picked
        ok1, m1 = set_metadata_dates_from_value(path, value)
        if not ok1:
            return (str(path), False, f"Failed to set metadata from {src_tag}={value}: {m1}")
        ok2, m2 = set_filesystem_dates_from_value(path, value)
        if not ok2:
            return (str(path), False, f"Metadata set OK, filesystem set FAILED: {m2}")
        return (str(path), True, f"Set metadata+filesystem from {src_tag} = {value}")
    except subprocess.CalledProcessError as e:
        return (str(path), False, f"ExifTool error: {e.output.decode(errors='ignore')}")
    except Exception as e:
        return (str(path), False, f"Error: {e}")

# ---------- Sync mode (from another file / folder) ----------
def read_best_time_value(src: Path) -> Optional[str]:
    tags = exiftool_json(src)
    picked = pick_best_time_tag(tags, src)
    if not picked:
        return None
    return picked[1]  # value string

def sync_from_source_value_and_apply(dst: Path, value: str) -> Tuple[bool, str]:
    ok1, m1 = set_metadata_dates_from_value(dst, value)
    if not ok1:
        return False, f"Set metadata FAILED: {m1}"
    ok2, m2 = set_filesystem_dates_from_value(dst, value)
    if not ok2:
        return False, f"Filesystem set FAILED: {m2}"
    return True, "Synced metadata+filesystem"

def build_source_index(src_folder: Path, recursive: bool, case_insensitive: bool):
    # index: key (stem or stem.lower()) -> dict(ext_lower -> [paths])
    index: Dict[str, Dict[str, List[Path]]] = defaultdict(lambda: defaultdict(list))
    iterator = src_folder.rglob("*") if recursive else src_folder.iterdir()
    for p in iterator:
        try:
            if p.is_file() and not is_hidden(p):
                key = p.stem.lower() if case_insensitive else p.stem
                ext = p.suffix.lower()
                index[key][ext].append(p)
        except PermissionError:
            continue
    return index

def find_source_match(index, target: Path, case_insensitive: bool) -> Optional[Path]:
    key = target.stem.lower() if case_insensitive else target.stem
    ext = target.suffix.lower()
    # Prefer same extension first
    if key in index and ext in index[key] and index[key][ext]:
        return index[key][ext][0]
    # Else any ext of same basename
    if key in index:
        for _, lst in index[key].items():
            if lst:
                return lst[0]
    return None

# ---------- Target enumeration ----------
def expand_file_argument(arg: str) -> List[Path]:
    p = Path(arg)
    if p.suffix:
        candidate = p if p.exists() else (Path.cwd() / p)
        return [candidate] if candidate.exists() and candidate.is_file() and not is_hidden(candidate) else []
    else:
        directory = p.parent if str(p.parent) not in ("", ".") else Path.cwd()
        basename = p.name
        candidates = [c for c in directory.glob(basename + ".*") if c.is_file() and not is_hidden(c)]
        if (directory / basename).exists() and (directory / basename).is_file() and not is_hidden(directory / basename):
            candidates.append(directory / basename)
        # de-dup
        uniq = []
        seen = set()
        for c in candidates:
            rp = c.resolve()
            if rp not in seen:
                seen.add(rp)
                uniq.append(c)
        return uniq

def iter_folder(folder: Path, recursive: bool) -> List[Path]:
    if recursive:
        return [p for p in folder.rglob("*") if p.is_file() and not is_hidden(p)]
    else:
        return [p for p in folder.iterdir() if p.is_file() and not is_hidden(p)]

# ---------- CLI ----------
def build_parser() -> argparse.ArgumentParser:
    epilog = (
        "Examples:\n"
        "\n"
        f"  # Restore from a file's own metadata (single file or basename)\n"
        f"  {APP_NAME} --file myvideo\n"
        f"  {APP_NAME} --file /path/movie.mp4\n"
        "\n"
        f"  # Restore for all files in a folder (non-recursive / recursive)\n"
        f"  {APP_NAME} --folder /path/to/media\n"
        f"  {APP_NAME} --folder /path/to/media --recursive\n"
        "\n"
        f"  # Sync dates from originals (folder-to-folder, match by basename)\n"
        f"  {APP_NAME} --folder /path/encoded --sync-date-from /path/originals\n"
        "\n"
        f"  # Sync date for single file from a source file\n"
        f"  {APP_NAME} --file /path/encoded/movie.mp4 --sync-date-from /path/originals/movie_original.mov\n"
        "\n"
        "Notes:\n"
        "  - Hidden files (prefix '.') are ignored.\n"
        "  - This tool updates both embedded metadata and filesystem timestamps by default.\n"
        "  - If the filesystem has no create-time support, only Modified time is set.\n"
    )
    parser = argparse.ArgumentParser(
        prog=APP_NAME,
        description="Restore or sync media dates: updates embedded EXIF/QuickTime metadata AND filesystem timestamps.",
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--file", dest="file", help="Target file basename or path. If no extension, process all matching files in the same directory.")
    group.add_argument("--folder", dest="folder", help="Target folder path. Process all files within (use --recursive for subfolders).")
    parser.add_argument("--recursive", action="store_true", help="Recurse into subfolders (only with --folder).")
    parser.add_argument("--quiet", action="store_true", help="Minimal output.")
    parser.add_argument("--sync-date-from", dest="sync_from",
                        help="Sync timestamps from source file or source folder (match by basename). With --file: provide source file. With --folder: provide source folder.")
    # knobs for sync behavior
    parser.add_argument("--src-recursive", dest="src_recursive", action=argparse.BooleanOptionalAction, default=True,
                        help="When syncing from a source folder, search recursively (default: true). Use --no-src-recursive to disable.")
    parser.add_argument("--case-insensitive", dest="case_insensitive", action=argparse.BooleanOptionalAction, default=True,
                        help="Case-insensitive basename matching when syncing (default: true). Use --no-case-insensitive to disable.")
    return parser

def main(argv=None):
    ensure_exiftool()
    parser = build_parser()
    args = parser.parse_args(argv)

    sync_mode = args.sync_from is not None

    # Build target list
    if args.file:
        targets = expand_file_argument(args.file)
        if not targets:
            print(c(f"[WARN] No files matched: {args.file}", Colors.YELLOW))
            sys.exit(2)
    else:
        folder = Path(args.folder)
        if not folder.exists() or not folder.is_dir():
            print(c(f"[ERROR] Not a folder: {args.folder}", Colors.RED))
            sys.exit(2)
        targets = iter_folder(folder, args.recursive)

    if not targets:
        print(c("[INFO] Nothing to do.", Colors.YELLOW))
        return

    processed = succeeded = failed = 0

    # Prepare sync source
    src_folder: Optional[Path] = None
    src_file: Optional[Path] = None
    src_index = None
    if sync_mode:
        src = Path(args.sync_from)
        if args.file:
            if not src.exists() or not src.is_file():
                print(c(f"[ERROR] Source file does not exist: {src}", Colors.RED))
                sys.exit(2)
            src_file = src
        else:
            if not src.exists() or not src.is_dir():
                print(c(f"[ERROR] Source folder does not exist: {src}", Colors.RED))
                sys.exit(2)
            src_folder = src
            src_index = build_source_index(src_folder, args.src_recursive, args.case_insensitive)

    for path in targets:
        processed += 1
        try:
            if sync_mode:
                if src_file is not None:
                    # Single file <- single file
                    value = read_best_time_value(src_file)
                    if not value:
                        failed += 1
                        print(c("[FAIL] ", Colors.RED) + f"{path} <- {src_file} :: Source has no usable date")
                        continue
                    ok, msg = sync_from_source_value_and_apply(path, value)
                    if ok:
                        succeeded += 1
                        if not args.quiet:
                            print(c("[OK] ", Colors.GREEN) + f"{path} <- {src_file} :: {value}")
                    else:
                        failed += 1
                        print(c("[FAIL] ", Colors.RED) + f"{path} <- {src_file} :: {msg}")
                else:
                    # Folder-to-folder match by basename
                    match = find_source_match(src_index, path, args.case_insensitive) if src_index else None
                    if not match:
                        failed += 1
                        print(c("[FAIL] ", Colors.RED) + f"{path} :: No match in {src_folder} by basename")
                        continue
                    value = read_best_time_value(match)
                    if not value:
                        failed += 1
                        print(c("[FAIL] ", Colors.RED) + f"{path} <- {match} :: Source has no usable date")
                        continue
                    ok, msg = sync_from_source_value_and_apply(path, value)
                    if ok:
                        succeeded += 1
                        if not args.quiet:
                            print(c("[OK] ", Colors.GREEN) + f"{path} <- {match} :: {value}")
                    else:
                        failed += 1
                        print(c("[FAIL] ", Colors.RED) + f"{path} <- {match} :: {msg}")
            else:
                # Restore from own metadata
                res_path, ok, msg = process_file_restore_from_own_metadata(path)
                if ok:
                    succeeded += 1
                    if not args.quiet:
                        print(c("[OK] ", Colors.GREEN) + f"{res_path} -> {msg}")
                else:
                    failed += 1
                    print(c("[FAIL] ", Colors.RED) + f"{res_path} -> {msg}")
        except KeyboardInterrupt:
            print(c("\n[INFO] Interrupted by user.", Colors.YELLOW))
            break
        except Exception as e:
            failed += 1
            print(c("[FAIL] ", Colors.RED) + f"{path} -> Unexpected error: {e}")

    print(f"\nSummary: processed={processed}, success={succeeded}, failed={failed}")
    if failed > 0:
        sys.exit(1)

# ---- utilities for indexing (placed after main for clarity) ----
def build_source_index(src_folder: Path, recursive: bool, case_insensitive: bool):
    index: Dict[str, Dict[str, List[Path]]] = defaultdict(lambda: defaultdict(list))
    iterator = src_folder.rglob("*") if recursive else src_folder.iterdir()
    for p in iterator:
        try:
            if p.is_file() and not is_hidden(p):
                key = p.stem.lower() if case_insensitive else p.stem
                ext = p.suffix.lower()
                index[key][ext].append(p)
        except PermissionError:
            continue
    return index

def find_source_match(index, target: Path, case_insensitive: bool) -> Optional[Path]:
    key = target.stem.lower() if case_insensitive else target.stem
    ext = target.suffix.lower()
    if key in index and ext in index[key] and index[key][ext]:
        return index[key][ext][0]
    if key in index:
        for _, lst in index[key].items():
            if lst:
                return lst[0]
    return None

if __name__ == "__main__":
    main()