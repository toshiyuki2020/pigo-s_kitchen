#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path


def _resolve_loose(p: Path) -> Path:
    """resolve() が strict=False を受けない環境でも動くようにする。"""
    try:
        return p.resolve(strict=False)  # py3.9+
    except TypeError:
        try:
            return p.resolve()
        except Exception:
            return p


def normalize_excludes(raw_csv: str, project_dir: Path, target_dir: Path) -> str:
    """
    cook側で exclude を補正する。
    - ディレクトリ名（vendor 等）はそのまま
    - パスっぽいもの（a/b, C:\\x\\y, ./x/y 等）は
      可能なら target_dir からの相対 prefix に変換して dirdump に渡す
    """
    if not raw_csv:
        return ""

    out = []
    for token in raw_csv.split(","):
        t = token.strip()
        if not t:
            continue

        t = t.replace("\\", "/")

        # パスっぽい（スラッシュ含む）ものは補正対象
        if "/" in t or t.startswith("."):
            cand = Path(t)

            # 絶対/相対どちらでも project_dir 基準でまず解決
            abs1 = _resolve_loose(cand if cand.is_absolute() else (project_dir / cand))
            abs2 = _resolve_loose(cand if cand.is_absolute() else (target_dir / cand))

            rel = None
            for abs_p in (abs1, abs2):
                try:
                    rel = abs_p.relative_to(target_dir).as_posix()
                    break
                except Exception:
                    pass

            # target配下に落とせたら相対prefixにする
            if rel:
                rel = rel.strip("/")
                if rel:
                    out.append(rel)
                continue

            # 落とせなければ、dirdumpの prefix 判定に任せてそのまま
            out.append(t.strip("/"))
            continue

        # ディレクトリ名扱い
        out.append(t)

    return ",".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="cook",
        description="Pigo's Kitchen wrapper for dirdump.py (kitchen args -> dirdump args).",
        add_help=True,
    )

    # 表示名だけ厨房ワードに（内部destは project/target/out のまま）
    parser.add_argument("project", nargs="?", default=".", metavar="dish",
                        help="Dish (= project root). default: .")
    parser.add_argument("target",  nargs="?", default=".", metavar="food",
                        help="Food (= target under dish or absolute path). default: .")
    parser.add_argument("out",     nargs="?", default=None, metavar="serve",
                        help="Serve (= output path). optional")

    # 厨房オプション + dirdump互換オプション（両方受け付けて dest を統一）
    parser.add_argument("--recipe", "--format", dest="format",
                        choices=["md", "txt"], default="md",
                        help="Output format. (alias: --format)")

    parser.add_argument("--portion-mb", "--split-mb", dest="split_mb",
                        type=int, default=0,
                        help="Split by MB. (alias: --split-mb)")

    parser.add_argument("--portion-bytes", "--split-bytes", dest="split_bytes",
                        type=int, default=0,
                        help="Split by bytes. (alias: --split-bytes)")

    parser.add_argument("--ingredients", "--ext", dest="ext",
                        default=None,
                        help="Extensions csv. (alias: --ext)")

    parser.add_argument("--all-ingredients", "--all-text", dest="all_text",
                        action="store_true",
                        help="Include all non-binary text-like files. (alias: --all-text)")

    parser.add_argument("--discard", "--exclude", dest="exclude",
                        default="",
                        help="Exclude names/paths csv (cook will normalize). (alias: --exclude)")

    parser.add_argument("--forage", "--all-files", dest="all_files",
                        action="store_true",
                        help="Walk filesystem. (alias: --all-files)")

    parser.add_argument("--max-bite", "--max-bytes", dest="max_bytes",
                        type=int, default=0,
                        help="Skip files larger than bytes. (alias: --max-bytes)")

    parser.add_argument("--no-menu", "--no-structure", dest="no_structure",
                        action="store_true",
                        help="Disable structure output. (alias: --no-structure)")

    parser.add_argument("--menu-max", "--structure-max", dest="structure_max",
                        type=int, default=0,
                        help="Limit structure entries. (alias: --structure-max)")

    parser.add_argument("--dry-run", action="store_true", help="Print mapped command and exit")
    parser.add_argument("-v", "--verbose", action="store_true", help="Print mapped command before run")

    # 未知オプションは dirdump にそのまま渡す
    args, passthrough = parser.parse_known_args()

    argv = build_dirdump_argv(args, passthrough)

    if args.verbose or args.dry_run:
        print("=> mapped dirdump command:")
        print("   " + " ".join(shlex.quote(x) for x in argv))

    if args.dry_run:
        return 0

    cp = subprocess.run(argv)
    return cp.returncode


def build_dirdump_argv(args: argparse.Namespace, passthrough: list[str]) -> list[str]:
    here = Path(__file__).resolve().parent
    dirdump = here / "dirdump.py"
    if not dirdump.exists():
        raise FileNotFoundError(f"dirdump.py not found next to cook.py: {dirdump}")

    project_dir = _resolve_loose(Path(args.project).expanduser())
    target_dir = _resolve_loose(
        (project_dir / args.target).expanduser()
        if not Path(args.target).is_absolute()
        else Path(args.target).expanduser()
    )

    # dirdump positional: project_dir target_dir output_path
    argv = [sys.executable, str(dirdump), str(project_dir), args.target]

    if args.out:
        argv.append(str(Path(args.out).expanduser()))

    # format
    argv += ["--format", args.format]

    # split
    if args.split_bytes and args.split_bytes > 0:
        argv += ["--split-bytes", str(args.split_bytes)]
    elif args.split_mb and args.split_mb > 0:
        argv += ["--split-mb", str(args.split_mb)]

    # ext / all-text
    if args.all_text:
        argv.append("--all-text")
    elif args.ext:
        argv += ["--ext", args.ext]

    # walk
    if args.all_files:
        argv.append("--all-files")

    # size
    if args.max_bytes and args.max_bytes > 0:
        argv += ["--max-bytes", str(args.max_bytes)]

    # structure
    if args.no_structure:
        argv.append("--no-structure")
    if args.structure_max and args.structure_max > 0:
        argv += ["--structure-max", str(args.structure_max)]

    # exclude（cook側で補正してから渡す）
    fixed_ex = normalize_excludes(args.exclude, project_dir=project_dir, target_dir=target_dir)
    if fixed_ex:
        argv += ["--exclude", fixed_ex]

    # 追加で dirdump の生オプションを通す
    argv += passthrough

    return argv


if __name__ == "__main__":
    raise SystemExit(main())
