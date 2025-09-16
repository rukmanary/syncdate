#!/usr/bin/env python3
# rstoredate v2.2.0
# Default: UPDATE embedded metadata (EXIF/QuickTime) + filesystem timestamps.
# Sync mode: copy metadata AS-IS + copy filesystem timestamps EXACTLY from source (prevents time shifts).
# New: --shift-hours (+/-N) to add/subtract hours from the chosen timestamp.
#      --set-offset "+07:00" or "Z" to force a timezone suffix in metadata strings (optional).

import argparse, json, os, re, shutil, subprocess, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from collections import defaultdict

APP_NAME = "rstoredate"
VERSION = "2.2.0"

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

# ----- Tag priorities -----
VIDEO_TAG_PRIORITY = [
    "QuickTime:CreationDate",
    "ItemList:ContentCreateDate",
    "Keys:CreationDate",
    "EXIF:CreateDate",
    "EXIF:DateTimeOriginal",
    "QuickTime:CreateDate",
    "QuickTime:MediaCreateDate",
    "QuickTime:TrackCreateDate",
    "H264:DateTimeOriginal",
    "XMP:CreateDate",
    "XMP:DateCreated",
]
PHOTO_TAG_PRIORITY = [
    "EXIF:DateTimeOriginal",
    "EXIF:CreateDate",
    "XMP:CreateDate",
    "XMP:DateCreated",
    "QuickTime:CreationDate",
    "PNG:CreationTime",
]

VIDEO_EXTS = {".mp4",".mov",".m4v",".3gp",".3g2",".avi",".mts",".m2ts",".wmv"}
PHOTO_EXTS = {".jpg",".jpeg",".heic",".heif",".png",".tif",".tiff",".webp",".dng",".cr2",".nef",".arw",".rw2"}

def is_hidden(p: Path) -> bool: return p.name.startswith(".")
def is_video(p: Path) -> bool:  return p.suffix.lower() in VIDEO_EXTS
def is_photo(p: Path) -> bool:  return p.suffix.lower() in PHOTO_EXTS

def ensure_exiftool():
    if shutil.which("exiftool") is None:
        sys.stderr.write(c("ERROR: 'exiftool' not found in PATH.\n", Colors.RED))
        sys.exit(1)

# IMPORTANT: do NOT use -api QuickTimeUTC=1 (preserve original timezone offsets)
def exiftool_json(path: Path) -> Dict[str,str]:
    cmd = ["exiftool","-a","-G1","-s","-time:all","-j",str(path)]
    out = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
    data = json.loads(out.decode("utf-8", errors="ignore"))
    return data[0] if data else {}

TZ_RE = re.compile(r'(Z|[+-]\d{2}:\d{2})$')
DT_RE = re.compile(
    r'^(\d{4}):(\d{2}):(\d{2})\s+(\d{2}):(\d{2}):(\d{2})(\.\d+)?(Z|[+-]\d{2}:\d{2})?$'
)

def parse_exif_dt(s: str) -> Tuple[datetime, Optional[str], Optional[str]]:
    """
    Parse EXIF/QuickTime datetime string.
    Returns (aware_or_naive_dt, frac, tz_suffix_str)
    """
    m = DT_RE.match(s.strip())
    if not m:
        raise ValueError(f"Unsupported datetime format: {s}")
    y,mo,d,hh,mm,ss,frac, tzs = m.groups()
    dt = datetime(int(y), int(mo), int(d), int(hh), int(mm), int(ss))
    if tzs:
        if tzs == 'Z':
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            sign = 1 if tzs[0] == '+' else -1
            off_h = int(tzs[1:3]); off_m = int(tzs[4:6])
            dt = dt.replace(tzinfo=timezone(sign * timedelta(hours=off_h, minutes=off_m)))
    return dt, frac, tzs

def fmt_exif_dt(dt: datetime, frac: Optional[str], tz_suffix: Optional[str]) -> str:
    s = dt.strftime("%Y:%m:%d %H:%M:%S")
    if frac:
        # keep original fractional seconds if present
        s += frac
    if tz_suffix:
        if tz_suffix == 'Z':
            # ensure dt is in UTC for honesty; but we keep suffix as requested
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        s += tz_suffix
    return s

def apply_time_adjustments(value: str, shift_hours: float = 0.0, set_offset: Optional[str] = None) -> str:
    """
    Add/sub hours and/or set timezone suffix (without converting the wall clock again).
    - shift-hours: add/subtract hours to the wall clock.
    - set-offset: force suffix "+HH:MM" or "Z"; does NOT additionally shift time.
    """
    dt, frac, tzs = parse_exif_dt(value)
    # Shift wall clock
    if shift_hours and shift_hours != 0.0:
        dt = dt + timedelta(hours=float(shift_hours))
    # Force suffix if requested
    if set_offset:
        so = set_offset.strip().upper()
        if so != 'Z' and not re.match(r'^[+-]\d{2}:\d{2}$', so):
            raise ValueError(f"Invalid --set-offset '{set_offset}' (use 'Z' or ±HH:MM)")
        tzs = so
    return fmt_exif_dt(dt, frac, tzs)

def pick_best_time_tag(tags: Dict[str,str], path: Path) -> Optional[Tuple[str,str]]:
    """
    Choose the best time tag, preferring those with timezone suffix.
    Fallback to System:FileModifyDate if all candidates are naive.
    """
    if is_video(path): base = VIDEO_TAG_PRIORITY.copy()
    elif is_photo(path): base = PHOTO_TAG_PRIORITY.copy()
    else: base = VIDEO_TAG_PRIORITY + PHOTO_TAG_PRIORITY
    base += ["System:FileModifyDate"]

    candidates = []
    for tag in base:
        if tag in tags and str(tags[tag]).strip():
            candidates.append((tag, str(tags[tag]).strip()))
    # prefer ones with explicit TZ
    for tag, val in candidates:
        if TZ_RE.search(val):
            return tag, val
    # fallback any
    if candidates:
        return candidates[0]
    # last resort: scan all
    for k, v in tags.items():
        if (k.endswith("CreateDate") or k.endswith("DateTimeOriginal")) and str(v).strip():
            return k, str(v).strip()
    return None

# ----- Writers -----
def set_metadata_dates_from_value(path: Path, value: str) -> Tuple[bool,str]:
    if is_photo(path):
        args = ["-overwrite_original",
                f"-DateTimeOriginal={value}",
                f"-CreateDate={value}",
                f"-ModifyDate={value}",
                str(path)]
    else:
        args = ["-overwrite_original",
                f"-QuickTime:CreateDate={value}",
                f"-QuickTime:ModifyDate={value}",
                f"-MediaCreateDate={value}",
                f"-TrackCreateDate={value}",
                f"-TrackModifyDate={value}",
                f"-ModifyDate={value}",
                f"-ItemList:ContentCreateDate={value}",
                f"-Keys:CreationDate={value}",
                str(path)]
    try:
        out = subprocess.check_output(["exiftool", *args], stderr=subprocess.STDOUT)
        return True, out.decode("utf-8", errors="ignore").strip()
    except subprocess.CalledProcessError as e:
        return False, e.output.decode(errors="ignore")

def set_filesystem_dates_from_value(path: Path, value: str) -> Tuple[bool,str]:
    try:
        out = subprocess.check_output(
            ["exiftool","-overwrite_original",
             f"-FileCreateDate={value}", f"-FileModifyDate={value}", str(path)],
            stderr=subprocess.STDOUT)
        return True, out.decode("utf-8", errors="ignore").strip()
    except subprocess.CalledProcessError as e:
        msg = e.output.decode(errors="ignore")
        try:
            out2 = subprocess.check_output(
                ["exiftool","-overwrite_original",
                 f"-FileModifyDate={value}", str(path)],
                stderr=subprocess.STDOUT)
            return True, "(create date unsupported) " + out2.decode("utf-8", errors="ignore").strip()
        except subprocess.CalledProcessError as e2:
            return False, msg + "\n" + e2.output.decode(errors="ignore")

# ----- Restore from own metadata -----
def restore_from_own_metadata(path: Path, shift_hours: float, set_offset: Optional[str]) -> Tuple[str,bool,str]:
    try:
        tags = exiftool_json(path)
        picked = pick_best_time_tag(tags, path)
        if not picked:
            return (str(path), False, "No usable time tag found")
        src_tag, value = picked  # keep any timezone offset
        adj = apply_time_adjustments(value, shift_hours, set_offset)
        ok1, m1 = set_metadata_dates_from_value(path, adj)
        if not ok1:
            return (str(path), False, f"Set metadata failed from {src_tag}={value} -> {adj}: {m1}")
        ok2, m2 = set_filesystem_dates_from_value(path, adj)
        if not ok2:
            return (str(path), False, f"Metadata OK, filesystem FAILED: {m2}")
        return (str(path), True, f"Set metadata+filesystem from {src_tag} = {value} -> {adj}")
    except subprocess.CalledProcessError as e:
        return (str(path), False, f"ExifTool error: {e.output.decode(errors='ignore')}")
    except Exception as e:
        return (str(path), False, f"Error: {e}")

# ----- SYNC: copy metadata + copy FS timestamps from source; optional shift afterward -----
def sync_copy_metadata_from_src(src: Path, dst: Path) -> Tuple[bool,str]:
    cmd = [
        "exiftool","-overwrite_original",
        "-TagsFromFile", str(src),
        "-QuickTime:CreateDate>QuickTime:CreateDate",
        "-QuickTime:ModifyDate>QuickTime:ModifyDate",
        "-MediaCreateDate>MediaCreateDate",
        "-TrackCreateDate>TrackCreateDate",
        "-TrackModifyDate>TrackModifyDate",
        "-EXIF:CreateDate>CreateDate",
        "-EXIF:DateTimeOriginal>DateTimeOriginal",
        "-EXIF:ModifyDate>ModifyDate",
        "-ItemList:ContentCreateDate>ItemList:ContentCreateDate",
        "-Keys:CreationDate>Keys:CreationDate",
        "-UserData:CreationDate>UserData:CreationDate",
        str(dst),
    ]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
        return True, out.decode("utf-8","ignore").strip()
    except subprocess.CalledProcessError as e:
        return False, e.output.decode(errors="ignore")

def sync_copy_filesystem_dates_from_src(src: Path, dst: Path) -> Tuple[bool,str]:
    try:
        out = subprocess.check_output(
            ["exiftool","-overwrite_original",
             "-TagsFromFile", str(src),
             "-FileCreateDate<FileCreateDate",
             "-FileModifyDate<FileModifyDate",
             str(dst)],
            stderr=subprocess.STDOUT)
        return True, out.decode("utf-8","ignore").strip()
    except subprocess.CalledProcessError as e:
        msg = e.output.decode(errors="ignore")
        try:
            out2 = subprocess.check_output(
                ["exiftool","-overwrite_original",
                 "-TagsFromFile", str(src),
                 "-FileModifyDate<FileModifyDate",
                 str(dst)],
                stderr=subprocess.STDOUT)
            return True, "(create date unsupported) " + out2.decode("utf-8","ignore").strip()
        except subprocess.CalledProcessError as e2:
            return False, msg + "\n" + e2.output.decode(errors="ignore")

def sync_from_source(src: Path, dst: Path, shift_hours: float, set_offset: Optional[str]) -> Tuple[bool,str]:
    ok1, m1 = sync_copy_metadata_from_src(src, dst)
    if not ok1: return False, f"Copy metadata failed: {m1}"
    ok2, m2 = sync_copy_filesystem_dates_from_src(src, dst)
    if not ok2: return False, f"Copy filesystem dates failed: {m2}"
    # If user asked to shift/offset, apply after copy (override both metadata & FS)
    if (shift_hours and shift_hours != 0.0) or set_offset:
        tags = exiftool_json(dst)
        picked = pick_best_time_tag(tags, dst)
        if not picked:
            return False, "Post-sync shift requested but no usable time tag found"
        _, value = picked
        adj = apply_time_adjustments(value, shift_hours, set_offset)
        okm, _ = set_metadata_dates_from_value(dst, adj)
        okf, _ = set_filesystem_dates_from_value(dst, adj)
        if not (okm and okf):
            return False, "Post-sync shift failed to apply"
        return True, f"Synced then shifted -> {adj}"
    return True, "Synced metadata + filesystem (exact copy)"

# ----- discovery helpers -----
def expand_file_argument(arg: str) -> List[Path]:
    p = Path(arg)
    if p.suffix:
        candidate = p if p.exists() else (Path.cwd() / p)
        return [candidate] if candidate.exists() and candidate.is_file() and not is_hidden(candidate) else []
    else:
        directory = p.parent if str(p.parent) not in ("",".") else Path.cwd()
        basename = p.name
        candidates = [c for c in directory.glob(basename + ".*") if c.is_file() and not is_hidden(c)]
        if (directory / basename).exists() and (directory / basename).is_file() and not is_hidden(directory / basename):
            candidates.append(directory / basename)
        uniq, seen = [], set()
        for c in candidates:
            rp = c.resolve()
            if rp not in seen:
                seen.add(rp); uniq.append(c)
        return uniq

def iter_folder(folder: Path, recursive: bool) -> List[Path]:
    if recursive:
        return [p for p in folder.rglob("*") if p.is_file() and not is_hidden(p)]
    else:
        return [p for p in folder.iterdir() if p.is_file() and not is_hidden(p)]

def build_source_index(src_folder: Path, recursive: bool, case_insensitive: bool):
    index = defaultdict(lambda: defaultdict(list))  # key -> ext -> [paths]
    iterator = src_folder.rglob("*") if recursive else src_folder.iterdir()
    for p in iterator:
        try:
            if p.is_file() and not is_hidden(p):
                stem = p.stem.lower() if case_insensitive else p.stem
                index[stem][p.suffix.lower()].append(p)
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
            if lst: return lst[0]
    return None

# ----- CLI -----
def build_parser() -> argparse.ArgumentParser:
    epilog = (
        "Examples:\n"
        f"  {APP_NAME} --file myvideo\n"
        f"  {APP_NAME} --folder /path/media --recursive\n"
        f"  {APP_NAME} --folder /path/encoded --sync-date-from /path/originals\n"
        f"  {APP_NAME} --file /path/encoded/movie.mp4 --sync-date-from /path/originals/movie.mov\n"
        f"  # with timezone fix (e.g., add 7 hours and set explicit +07:00 suffix)\n"
        f"  {APP_NAME} --folder /path/media --shift-hours 7 --set-offset +07:00\n"
        "Notes:\n"
        "  - Hidden files (prefix '.') are ignored.\n"
        "  - This tool updates embedded metadata and filesystem timestamps.\n"
        "  - Timezone offsets are preserved; no UTC conversion is applied unless you set --set-offset.\n"
    )
    p = argparse.ArgumentParser(
        prog=APP_NAME,
        description="Restore/sync media dates: update embedded EXIF/QuickTime metadata AND filesystem timestamps (no UTC conversion).",
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--file",   dest="file",   help="Target file (path or basename; basename processes all matching extensions).")
    g.add_argument("--folder", dest="folder", help="Target folder. Use --recursive for subfolders.")
    p.add_argument("--recursive", action="store_true", help="Process subfolders (with --folder).")
    p.add_argument("--quiet", action="store_true", help="Minimal output.")
    p.add_argument("--sync-date-from", dest="sync_from",
                   help="Sync dates from source file (with --file) or source folder (with --folder, match by basename).")
    p.add_argument("--src-recursive", dest="src_recursive", action=argparse.BooleanOptionalAction, default=True,
                   help="When syncing from a source folder, search recursively (default: true). Use --no-src-recursive to disable.")
    p.add_argument("--case-insensitive", dest="case_insensitive", action=argparse.BooleanOptionalAction, default=True,
                   help="Case-insensitive basename matching when syncing (default: true). Use --no-case-insensitive to disable.")
    # New knobs:
    p.add_argument("--shift-hours", dest="shift_hours", type=float, default=0.0,
                   help="Add/subtract hours to the chosen timestamp before writing (e.g., 7 or -7).")
    p.add_argument("--set-offset", dest="set_offset", default=None,
                   help="Force timezone suffix in metadata (e.g., +07:00 or Z). Optional.")
    return p

def main(argv=None):
    ensure_exiftool()
    args = build_parser().parse_args(argv)
    sync_mode = args.sync_from is not None

    if args.file:
        targets = expand_file_argument(args.file)
        if not targets:
            print(c(f"[WARN] No files matched: {args.file}", Colors.YELLOW)); sys.exit(2)
    else:
        folder = Path(args.folder)
        if not folder.exists() or not folder.is_dir():
            print(c(f"[ERROR] Not a folder: {args.folder}", Colors.RED)); sys.exit(2)
        targets = iter_folder(folder, args.recursive)

    if not targets:
        print(c("[INFO] Nothing to do.", Colors.YELLOW)); return

    src_index = None; src_file = None; src_folder = None
    if sync_mode:
        src = Path(args.sync_from)
        if args.file:
            if not src.exists() or not src.is_file():
                print(c(f"[ERROR] Source file does not exist: {src}", Colors.RED)); sys.exit(2)
            src_file = src
        else:
            if not src.exists() or not src.is_dir():
                print(c(f"[ERROR] Source folder does not exist: {src}", Colors.RED)); sys.exit(2)
            src_folder = src
            src_index = build_source_index(src_folder, args.src_recursive, args.case_insensitive)

    processed = succeeded = failed = 0
    for path in targets:
        processed += 1
        try:
            if sync_mode:
                if src_file is not None:
                    ok, msg = sync_from_source(src_file, path, args.shift_hours, args.set_offset)
                    if ok:
                        succeeded += 1
                        if not args.quiet: print(c("[OK] ", Colors.GREEN) + f"{path} <- {src_file} :: {msg}")
                    else:
                        failed += 1
                        print(c("[FAIL] ", Colors.RED) + f"{path} <- {src_file} :: {msg}")
                else:
                    match = find_source_match(src_index, path, args.case_insensitive) if src_index else None
                    if not match:
                        failed += 1
                        print(c("[FAIL] ", Colors.RED) + f"{path} :: No match in {src_folder} by basename")
                        continue
                    ok, msg = sync_from_source(match, path, args.shift_hours, args.set_offset)
                    if ok:
                        succeeded += 1
                        if not args.quiet: print(c("[OK] ", Colors.GREEN) + f"{path} <- {match} :: {msg}")
                    else:
                        failed += 1
                        print(c("[FAIL] ", Colors.RED) + f"{path} <- {match} :: {msg}")
            else:
                res_path, ok, msg = restore_from_own_metadata(path, args.shift_hours, args.set_offset)
                if ok:
                    succeeded += 1
                    if not args.quiet: print(c("[OK] ", Colors.GREEN) + f"{res_path} -> {msg}")
                else:
                    failed += 1
                    print(c("[FAIL] ", Colors.RED) + f"{res_path} -> {msg}")
        except KeyboardInterrupt:
            print(c("\n[INFO] Interrupted by user.", Colors.YELLOW)); break
        except Exception as e:
            failed += 1
            print(c("[FAIL] ", Colors.RED) + f"{path} -> Unexpected error: {e}")

    print(f"\nSummary: processed={processed}, success={succeeded}, failed={failed}")
    if failed > 0: sys.exit(1)

if __name__ == "__main__":
    main()