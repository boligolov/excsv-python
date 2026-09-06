#!/usr/bin/env python3
"""Sync normative docs and test fixtures from boligolov/excsv (upstream).

Usage (repo root):
    python scripts/sync_upstream.py              # specs + manifest + fixture files
    python scripts/sync_upstream.py --specs-only
    python scripts/sync_upstream.py --fixtures-only

Cross-platform by construction (pure stdlib + subprocess), so this one script
replaces the Go repo's sync-upstream.sh / sync-upstream.ps1 pair.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UPSTREAM_BASE = "https://raw.githubusercontent.com/boligolov/excsv/master"
FIXTURE_BASE = f"{UPSTREAM_BASE}/fixtures"
UPSTREAM_REPO = "https://github.com/boligolov/excsv.git"

GUIDE_FILES = [
    "aggregations.md", "checksum.md", "columns.md", "data-section.md",
    "file-metadata.md", "file-structure.md", "full-example.md", "header.md",
    "introduction.md", "json.md", "license.md", "meta-lines.md", "pack.md",
    "prior-art.md", "sql.md", "zip.md",
]

IMPLEMENTATION_FILES = ["README.md"] + GUIDE_FILES


def download(url: str, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    print(f"  {out.relative_to(ROOT)}")
    with urllib.request.urlopen(url, timeout=30) as resp:
        out.write_bytes(resp.read())


def sync_specs() -> None:
    print("Downloading spec/plan snapshots...")
    download(f"{UPSTREAM_BASE}/README.md", ROOT / "docs/downloaded/README.md")
    download(f"{UPSTREAM_BASE}/docs/README.md", ROOT / "docs/downloaded/guide/README.md")
    download(f"{UPSTREAM_BASE}/plan/README.md", ROOT / "docs/downloaded/plan-README.md")
    download(f"{UPSTREAM_BASE}/plan/01-features.md", ROOT / "docs/downloaded/plan-01-features.md")
    download(f"{UPSTREAM_BASE}/plan/02-fixtures.md", ROOT / "docs/downloaded/plan-02-fixtures.md")
    download(f"{FIXTURE_BASE}/fixtures.yaml", ROOT / "test/fixtures/fixtures.yaml")
    download(f"{UPSTREAM_BASE}/schema/excsv.schema.json", ROOT / "docs/downloaded/schema/excsv.schema.json")
    download(f"{UPSTREAM_BASE}/schema/example.excsv.json", ROOT / "docs/downloaded/schema/example.excsv.json")
    for name in GUIDE_FILES:
        download(f"{UPSTREAM_BASE}/docs/{name}", ROOT / f"docs/downloaded/guide/{name}")
    for name in IMPLEMENTATION_FILES:
        download(f"{UPSTREAM_BASE}/docs/implementation/{name}", ROOT / f"docs/downloaded/implementation/{name}")


_ID_RE = re.compile(r"^\s*-\s*id:\s*(.+)$")
_SIBLING_RE = re.compile(r"^\s*data_sibling:\s*(.+)$")


def manifest_paths(manifest_path: Path) -> list[str]:
    paths: set[str] = set()
    for line in manifest_path.read_text(encoding="utf-8").splitlines():
        m = _ID_RE.match(line)
        if m:
            paths.add(m.group(1).strip())
            continue
        m = _SIBLING_RE.match(line)
        if m:
            paths.add(m.group(1).strip())
    return sorted(paths)


def sync_fixtures() -> None:
    manifest_path = ROOT / "test/fixtures/fixtures.yaml"
    if not manifest_path.exists():
        download(f"{FIXTURE_BASE}/fixtures.yaml", manifest_path)

    paths = manifest_paths(manifest_path)
    print(f"Downloading {len(paths)} fixture file(s) from manifest...")
    for rel in paths:
        if rel.startswith("pack/") or rel.startswith("zip/"):
            continue
        if not rel.startswith("plain/"):
            print(f"warning: skipping unexpected path: {rel}", file=sys.stderr)
            continue
        try:
            download(f"{FIXTURE_BASE}/{rel}", ROOT / "test/fixtures" / rel)
        except Exception as e:  # noqa: BLE001
            print(f"warning: skip {rel}: {e}", file=sys.stderr)

    print("Fetching zip/pack fixtures (generated upstream, or generating locally if needed)...")
    spec_dir = Path(tempfile.gettempdir()) / "excsv-spec-sync"
    if not (spec_dir / ".git").exists():
        if spec_dir.exists():
            shutil.rmtree(spec_dir)
        subprocess.run(
            ["git", "clone", "--depth", "1", "--branch", "master", UPSTREAM_REPO, str(spec_dir)], check=True
        )

    zip_gen = spec_dir / "fixtures/generate/make_zip_fixtures.py"
    pack_gen = spec_dir / "fixtures/generate/make_pack_fixtures.py"
    if zip_gen.exists() and pack_gen.exists():
        # Older upstream layout: fixtures/zip and fixtures/pack are generated,
        # not committed.
        subprocess.run([sys.executable, str(zip_gen)], check=True, cwd=spec_dir)
        subprocess.run([sys.executable, str(pack_gen)], check=True, cwd=spec_dir)

    for name in ("zip", "pack"):
        src = spec_dir / "fixtures" / name
        if not src.exists():
            print(f"warning: fixtures/{name} not found upstream (neither committed nor generated)", file=sys.stderr)
            continue
        dst = ROOT / "test/fixtures" / name
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--specs-only", action="store_true")
    group.add_argument("--fixtures-only", action="store_true")
    args = parser.parse_args()

    if not args.fixtures_only:
        sync_specs()
    if not args.specs_only:
        sync_fixtures()
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
