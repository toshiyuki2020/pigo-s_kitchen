#!/usr/bin/env python3
# dirdump.py
#
# Cross-platform directory dumper:
# - Works on Windows/macOS/Linux
# - Defaults are friendly for "common tool outside project"
# - If project is a Git repo, uses git-tracked files by default (safer).
# - Can include many text formats (php/twig/html/js/txt/etc.)
# - Can include "all non-binary text-like files" with --all-text
# - Skips binaries (images/pdf/zip/etc.) automatically
# - Supports exclude directories (by name) and exclude path prefixes (e.g. bootstrap/cache)
#
# Usage examples:
#   dirdump.py
#   dirdump.py /path/to/project
#   dirdump.py /path/to/project . /tmp/project_dump.md --all-text
#   dirdump.py ~/Desktop/project app ~/Desktop/app_dump.md --ext .php,.twig,.html,.js,.txt
#   dirdump.py ~/Desktop/project . --all-text --exclude vendor,node_modules,var,storage,bootstrap/cache,public/build,dist
#
from __future__ import annotations

import argparse
import mimetypes
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Set, Tuple


# -----------------------------
# Defaults (safe + practical)
# -----------------------------

# Exclude by directory name anywhere in path
DEFAULT_EXCLUDE_NAMES: Set[str] = {
    ".git",
    "vendor",
    "node_modules",
    "storage",
    "var",            # Symfony caches/logs
    ".idea",
    ".vscode",
    "__pycache__",
    ".pytest_cache",
    ".sass-cache",
    "coverage",
    ".cache",
    ".DS_Store",
}

# Exclude by relative path prefix from target_dir (useful for nested dirs)
DEFAULT_EXCLUDE_PREFIXES: Tuple[str, ...] = (
    "bootstrap/cache",
    "public/build",
    "dist",
    "build",
)

# Default text extensions (common web / PHP projects)
DEFAULT_TEXT_EXTS = (
    ".php,.twig,.html,.htm,.blade.php,.js,.ts,.tsx,.jsx,.css,.scss,.sass,"
    ".json,.yml,.yaml,.xml,.csv,.tsv,.sql,.md,.txt,.env,.ini,.conf,.toml,"
    ".gitignore,.gitattributes,.editorconfig,.sh,.bash,.zsh,.ps1,.bat,.cmd"
)

# Fast blacklist by extension (binary-ish)
BINARY_EXT_BLACKLIST: Set[str] = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".tif", ".tiff", ".svgz",
    ".pdf",
    ".zip", ".7z", ".rar", ".tar", ".gz", ".bz2", ".xz",
    ".mp3", ".wav", ".flac", ".ogg",
    ".mp4", ".mov", ".avi", ".mkv", ".webm",
    ".exe", ".dll", ".so", ".dylib", ".bin", ".dat", ".class", ".jar",
    ".ttf", ".otf", ".woff", ".woff2",
    ".psd", ".ai", ".sketch",
}

# For mimetype-based binary filtering
BINARY_MIME_PREFIXES = ("image/", "audio/", "video/")
BINARY_MIME_EXACT = {"application/pdf", "application/zip"}


# -----------------------------
# Helpers
# -----------------------------

def _to_posix_parts(p: Path) -> Tuple[str, ...]:
    # Normalize to forward-slash semantics
    return tuple(Path(p.as_posix()).parts)

def parse_exts(ext_csv: str) -> Tuple[str, ...]:
    items = []
    for raw in ext_csv.split(","):
        s = raw.strip()
        if not s:
            continue
        if not s.startswith(".") and s not in (".env", ".gitignore", ".editorconfig", ".gitattributes"):
            s = "." + s
        items.append(s.lower())
    return tuple(dict.fromkeys(items))  # preserve order, unique

def parse_excludes(exclude_csv: str) -> Tuple[Set[str], Tuple[Tuple[str, ...], ...]]:
    """
    Returns:
      - exclude_names: set of directory names to skip anywhere
      - exclude_prefix_parts: tuple of path-part tuples representing prefixes relative to target_dir
    """
    exclude_names = set(DEFAULT_EXCLUDE_NAMES)
    prefixes: List[Tuple[str, ...]] = [tuple(Path(x).as_posix().split("/")) for x in DEFAULT_EXCLUDE_PREFIXES]

    if exclude_csv.strip():
        for raw in exclude_csv.split(","):
            token = raw.strip()
            if not token:
                continue
            token = token.replace("\\", "/")
            if "/" in token:
                parts = tuple([p for p in token.split("/") if p])
                if parts:
                    prefixes.append(parts)
            else:
                exclude_names.add(token)

    # De-dup prefixes while keeping order
    seen = set()
    uniq: List[Tuple[str, ...]] = []
    for p in prefixes:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return exclude_names, tuple(uniq)

def is_excluded_rel(rel_parts: Tuple[str, ...], exclude_names: Set[str], exclude_prefixes: Tuple[Tuple[str, ...], ...]) -> bool:
    # Exclude if any directory name matches
    for part in rel_parts[:-1]:  # exclude names apply mainly to directories; safe to apply to all but filename
        if part in exclude_names:
            return True

    # Exclude if path starts with any excluded prefix
    for pref in exclude_prefixes:
        if len(rel_parts) >= len(pref) and rel_parts[:len(pref)] == pref:
            return True

    return False

def is_git_repo(project_dir: Path) -> bool:
    return (project_dir / ".git").exists()

def which_git() -> Optional[str]:
    return shutil.which("git")

def git_ls_files(project_dir: Path, target_rel_posix: str) -> List[str]:
    """
    Return git-tracked file paths (posix-like) under target_rel_posix.
    """
    git = which_git()
    if not git:
        return []
    cmd = [git, "-C", str(project_dir), "ls-files", target_rel_posix]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, text=True)
    except Exception:
        return []
    return [line for line in out.splitlines() if line.strip()]

def looks_binary(path: Path, sniff_bytes: int = 8192) -> bool:
    """
    Heuristic binary detector:
    - extension blacklist (fast)
    - mimetype hint (image/audio/video/pdf/zip etc.)
    - NUL byte presence
    - high ratio of non-text bytes
    """
    suf = path.suffix.lower()
    if suf in BINARY_EXT_BLACKLIST:
        return True

    mt, _ = mimetypes.guess_type(str(path))
    if mt:
        if mt.startswith(BINARY_MIME_PREFIXES):
            return True
        if mt in BINARY_MIME_EXACT:
            return True

    try:
        data = path.read_bytes()[:sniff_bytes]
    except Exception:
        return True

    if b"\x00" in data:
        return True

    # Non-text-ish ratio heuristic (len>=512 to avoid tiny false positives)
    if len(data) >= 512:
        # Define "text-like" bytes: common ASCII printable + whitespace + common controls
        text_like = set(range(32, 127)) | {9, 10, 13, 8}
        nontext = 0
        for b in data:
            if b in text_like:
                continue
            # bytes >= 0x80 might be UTF-8 multibyte; don't count as nontext immediately.
            # But if file is truly binary, it'll have lots of random >=0x80.
            if b >= 0x80:
                nontext += 1
        if (nontext / max(1, len(data))) > 0.30:
            return True

    return False

def safe_read_text(path: Path) -> Optional[str]:
    """
    Read as text safely. Returns None if looks binary or unreadable.
    Encoding strategy:
      - utf-8
      - utf-8-sig
      - cp932 (Windows legacy)
      - fallback utf-8 replace
    """
    if looks_binary(path):
        return None

    try:
        raw = path.read_bytes()
    except Exception:
        return None

    for enc in ("utf-8", "utf-8-sig", "cp932"):
        try:
            return raw.decode(enc)
        except Exception:
            pass

    try:
        return raw.decode("utf-8", errors="replace")
    except Exception:
        return None

def language_from_path(p: Path) -> str:
    name = p.name.lower()
    suf = p.suffix.lower()

    if name.endswith(".blade.php"):
        return "php"
    if suf == ".php":
        return "php"
    if suf == ".twig":
        return "twig"
    if suf in (".html", ".htm"):
        return "html"
    if suf in (".js", ".jsx"):
        return "javascript"
    if suf in (".ts", ".tsx"):
        return "typescript"
    if suf in (".css",):
        return "css"
    if suf in (".scss", ".sass"):
        return "scss"
    if suf in (".yml", ".yaml"):
        return "yaml"
    if suf in (".md",):
        return "markdown"
    if suf in (".json",):
        return "json"
    if suf in (".sql",):
        return "sql"
    if suf in (".xml",):
        return "xml"
    if suf in (".ps1",):
        return "powershell"
    if suf in (".sh", ".bash", ".zsh"):
        return "bash"
    return ""

def normalize_target(project_dir: Path, target: str) -> Path:
    target_in = Path(target).expanduser()
    if target_in.is_absolute():
        return target_in.resolve()
    return (project_dir / target_in).resolve()

def default_output_path(project_dir: Path, target_dir: Path) -> Path:
    # If dumping whole project (target "."), name it project_dump.md
    if target_dir == project_dir:
        return (project_dir / "project_dump.md").resolve()
    return (project_dir / f"{target_dir.name}_dump.md").resolve()


# -----------------------------
# Walking and structure output
# -----------------------------

def walk_collect_files(
    target_dir: Path,
    exts: Tuple[str, ...],
    exclude_names: Set[str],
    exclude_prefixes: Tuple[Tuple[str, ...], ...],
    all_text: bool,
) -> List[Path]:
    files: List[Path] = []

    for root, dirs, filenames in os.walk(target_dir):
        root_path = Path(root)
        rel_root = root_path.relative_to(target_dir)
        rel_root_parts = _to_posix_parts(rel_root)

        # prune directories
        kept_dirs: List[str] = []
        for d in dirs:
            if d in exclude_names:
                continue
            rel_dir_parts = rel_root_parts + (d,)
            if is_excluded_rel(rel_dir_parts, exclude_names, exclude_prefixes):
                continue
            kept_dirs.append(d)
        dirs[:] = sorted(kept_dirs)

        # collect files
        for fn in sorted(filenames):
            p = root_path / fn
            if not p.is_file():
                continue

            rel = p.relative_to(target_dir)
            rel_parts = _to_posix_parts(rel)
            if is_excluded_rel(rel_parts, exclude_names, exclude_prefixes):
                continue

            if all_text:
                # include everything for now; binary will be filtered by safe_read_text()
                files.append(p.resolve())
            else:
                name_lower = p.name.lower()
                if name_lower.endswith(".blade.php"):
                    if ".blade.php" in exts:
                        files.append(p.resolve())
                    continue
                if p.suffix.lower() in exts:
                    files.append(p.resolve())

    # stable ordering
    files.sort(key=lambda x: x.relative_to(target_dir).as_posix())
    return files

def git_collect_files(
    project_dir: Path,
    target_dir: Path,
    exts: Tuple[str, ...],
    exclude_names: Set[str],
    exclude_prefixes: Tuple[Tuple[str, ...], ...],
    all_text: bool,
) -> List[Path]:
    """
    Collect files using `git ls-files`, then apply extension/all_text + excludes.
    """
    # relative target for git
    try:
        target_rel = target_dir.relative_to(project_dir)
        target_rel_posix = target_rel.as_posix()
        if target_rel_posix == "":
            target_rel_posix = "."
    except Exception:
        # target is outside project; can't use git safely
        return []

    paths = git_ls_files(project_dir, target_rel_posix)
    results: List[Path] = []

    for posix_path in paths:
        abs_path = (project_dir / posix_path).resolve()
        if not abs_path.is_file():
            continue

        # Must be inside target_dir
        try:
            rel = abs_path.relative_to(target_dir)
        except Exception:
            continue

        rel_parts = _to_posix_parts(rel)
        if is_excluded_rel(rel_parts, exclude_names, exclude_prefixes):
            continue

        if all_text:
            # quick skip for known binary by ext (deep sniff happens later)
            if abs_path.suffix.lower() in BINARY_EXT_BLACKLIST:
                continue
            results.append(abs_path)
        else:
            name_lower = abs_path.name.lower()
            if name_lower.endswith(".blade.php"):
                if ".blade.php" in exts:
                    results.append(abs_path)
                continue
            if abs_path.suffix.lower() in exts:
                results.append(abs_path)

    results.sort(key=lambda x: x.relative_to(target_dir).as_posix())
    return results

def build_structure_lines(
    target_dir: Path,
    exclude_names: Set[str],
    exclude_prefixes: Tuple[Tuple[str, ...], ...],
    max_entries: int = 0,
) -> List[str]:
    """
    Builds a simple tree-like listing (dirs + files), excluding specified dirs/prefixes.
    max_entries=0 means no limit (but large projects may create huge structure).
    """
    lines: List[str] = [f"{target_dir.name}/"]
    entries: List[Tuple[str, bool]] = []

    count = 0
    for root, dirs, filenames in os.walk(target_dir):
        root_path = Path(root)
        rel_root = root_path.relative_to(target_dir)
        rel_root_parts = _to_posix_parts(rel_root)

        # prune dirs
        kept_dirs: List[str] = []
        for d in dirs:
            if d in exclude_names:
                continue
            rel_dir_parts = rel_root_parts + (d,)
            if is_excluded_rel(rel_dir_parts, exclude_names, exclude_prefixes):
                continue
            kept_dirs.append(d)
        dirs[:] = sorted(kept_dirs)

        for d in dirs:
            rel_dir = (root_path / d).relative_to(target_dir).as_posix()
            entries.append((rel_dir + "/", True))
            count += 1
            if max_entries and count >= max_entries:
                break

        if max_entries and count >= max_entries:
            break

        for fn in sorted(filenames):
            rel_file = (root_path / fn).relative_to(target_dir).as_posix()
            rel_parts = tuple(Path(rel_file).parts)
            if is_excluded_rel(rel_parts, exclude_names, exclude_prefixes):
                continue
            entries.append((rel_file, False))
            count += 1
            if max_entries and count >= max_entries:
                break

        if max_entries and count >= max_entries:
            break

    entries.sort(key=lambda x: x[0])

    for rel_posix, is_dir in entries:
        parts = Path(rel_posix.rstrip("/")).parts
        indent = "  " * (len(parts) - 1) if len(parts) > 1 else ""
        name = parts[-1] + ("/" if is_dir else "")
        lines.append(f"{indent}{name}")

    if max_entries and count >= max_entries:
        lines.append("  ...(structure truncated)...")

    return lines


# -----------------------------
# Main
# -----------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        prog="dirdump",
        description="Dump directory structure and file contents into a single md/txt file (skip binaries).",
    )

    # Optional positional args (defaults)
    parser.add_argument("project_dir", nargs="?", default=".",
                        help="Project root directory (default: current dir).")
    parser.add_argument("target_dir", nargs="?", default="app",
                        help="Target directory under project, or '.' for whole project (default: app).")
    parser.add_argument("output_path", nargs="?", default=None,
                        help="Output file path (default: <project_dir>/<target>_dump.md).")

    parser.add_argument("--format", choices=["md", "txt"], default="md",
                        help="Output format (default: md).")

    # Text scope
    parser.add_argument("--ext", default=DEFAULT_TEXT_EXTS,
                        help="Comma-separated extensions (default: common text types). "
                             "Ignored when --all-text is set.")
    parser.add_argument("--all-text", action="store_true",
                        help="Include all non-binary text-like files (ignore --ext).")

    # Excludes
    parser.add_argument("--exclude", default="",
                        help="Comma-separated excludes. "
                             "Either dir name (e.g. vendor) or path prefix (e.g. bootstrap/cache). "
                             "Added to sensible defaults.")

    # Git / walk behavior
    parser.add_argument("--all-files", action="store_true",
                        help="Walk filesystem instead of using git tracked list (even if git repo).")

    # Size control
    parser.add_argument("--max-bytes", type=int, default=0,
                        help="Skip files larger than this size in bytes (0 = no limit).")

    # Structure control
    parser.add_argument("--no-structure", action="store_true",
                        help="Do not output structure listing.")
    parser.add_argument("--structure-max", type=int, default=0,
                        help="Limit structure entries (0 = no limit). Useful for large projects.")

    args = parser.parse_args()

    project_dir = Path(args.project_dir).expanduser().resolve()
    if not project_dir.is_dir():
        print(f"[ERROR] project_dir not found: {project_dir}", file=sys.stderr)
        return 2

    target_dir = normalize_target(project_dir, args.target_dir)

    # Treat target "." as whole project
    if args.target_dir.strip() in (".", "./"):
        target_dir = project_dir

    if not target_dir.is_dir():
        print(f"[ERROR] target_dir not found: {target_dir}", file=sys.stderr)
        return 2

    exclude_names, exclude_prefixes = parse_excludes(args.exclude)

    # Extensions
    exts = parse_exts(args.ext)

    # Output path default
    if args.output_path:
        out_in = Path(args.output_path).expanduser()
        output_path = out_in.resolve() if out_in.is_absolute() else (Path.cwd() / out_in).resolve()
    else:
        output_path = default_output_path(project_dir, target_dir)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Collect files: git-tracked by default if repo and target inside project, unless --all-files
    use_git = (is_git_repo(project_dir) and not args.all_files and which_git() is not None)

    files: List[Path] = []
    if use_git:
        files = git_collect_files(
            project_dir=project_dir,
            target_dir=target_dir,
            exts=exts,
            exclude_names=exclude_names,
            exclude_prefixes=exclude_prefixes,
            all_text=args.all_text,
        )
        if not files:
            # fallback
            files = walk_collect_files(
                target_dir=target_dir,
                exts=exts,
                exclude_names=exclude_names,
                exclude_prefixes=exclude_prefixes,
                all_text=args.all_text,
            )
    else:
        files = walk_collect_files(
            target_dir=target_dir,
            exts=exts,
            exclude_names=exclude_names,
            exclude_prefixes=exclude_prefixes,
            all_text=args.all_text,
        )

    # Write (force LF newlines for consistent dumps)
    fence = "```"
    written = 0
    skipped_binary = 0
    skipped_large = 0

    with output_path.open("w", encoding="utf-8", newline="\n") as w:
        w.write(f"ディレクトリ:{target_dir.name}\n")
        w.write(f"対象:{target_dir.as_posix()}\n")
        w.write(f"出力:{output_path.as_posix()}\n")
        w.write("\n")

        if not args.no_structure:
            w.write("構造:\n")
            for line in build_structure_lines(
                target_dir=target_dir,
                exclude_names=exclude_names,
                exclude_prefixes=exclude_prefixes,
                max_entries=args.structure_max,
            ):
                w.write(line + "\n")
            w.write("\n---\n\n")

        for p in files:
            # Prevent self-inclusion
            if p.resolve() == output_path.resolve():
                continue

            if args.max_bytes and p.stat().st_size > args.max_bytes:
                skipped_large += 1
                continue

            content = safe_read_text(p)
            if content is None:
                skipped_binary += 1
                continue

            rel = p.relative_to(target_dir)
            rel_parent = rel.parent.as_posix()
            path_str = "/" if rel_parent == "." else (rel_parent + "/")

            w.write(f"ファイル名:{p.name}\n")
            w.write(f"パス:{path_str}\n")
            w.write("内容\n")

            if args.format == "md":
                lang = language_from_path(p)
                w.write(f"{fence}{lang}\n")
                w.write(content)
                if not content.endswith("\n"):
                    w.write("\n")
                w.write(f"{fence}\n\n")
            else:
                w.write(content)
                if not content.endswith("\n"):
                    w.write("\n")
                w.write("\n")

            w.write("---\n\n")
            written += 1

        w.write(f"出力ファイル数: {written}\n")
        w.write(f"スキップ（バイナリ判定）: {skipped_binary}\n")
        if args.max_bytes:
            w.write(f"スキップ（max-bytes超過）: {skipped_large}\n")
        w.write(f"収集方式: {'git ls-files' if use_git else 'filesystem walk'}\n")
        w.write(f"モード: {'all-text' if args.all_text else 'ext-filter'}\n")

    print(f"OK: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

