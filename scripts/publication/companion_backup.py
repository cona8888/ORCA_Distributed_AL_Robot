#!/usr/bin/env python3
"""Prepare a source-excluded publication backup for the existing companion repo.

Inventory only by default. --batch N emits one JSON tree-content batch for the
GitHub connector. Source-restricted C/C++ files, patches, raw runs, licensed
paper copies and Python files containing embedded C++ markers stay local.
"""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[2]
TEXT_EXTENSIONS = {'.py', '.sh', '.md', '.txt', '.csv', '.json', '.svg',
                   '.yaml', '.yml', '.toml', '.lock'}
CPP = re.compile(r'#include|void [A-Za-z_][A-Za-z_0-9]*::|namespace [A-Za-z_]')
SOURCE_DIRS = ['scripts', 'tools', 'docs/publication',
               'figures/publication/python_packages_v1']
FIXED_FILES = ['publication_essentials/README.md',
               'publication_essentials/GITHUB_README.md']
REPORT = ROOT / 'publication_essentials' / 'github_backup_manifest.json'


def inventory():
    files = []
    excluded = []
    for directory in SOURCE_DIRS:
        for p in sorted((ROOT / directory).rglob('*')):
            if not p.is_file() or '__pycache__' in p.parts or 'packages' in p.parts:
                continue
            if p.suffix not in TEXT_EXTENSIONS and p.suffix != '.png':
                continue
            # PNGs required as scientific map inputs only; rendered previews
            # and duplicate PNG/PDF/ZIP exports remain locally available.
            if p.suffix == '.png' and not (p.name.startswith('map') or
                                          p.name == 'contact_crop.png'):
                continue
            if p.suffix != '.png':
                text = p.read_text(encoding='utf-8')
                if CPP.search(text) and p != Path(__file__).resolve():
                    excluded.append(str(p.relative_to(ROOT)))
                    continue
            files.append(p)
    for rel in FIXED_FILES:
        files.append(ROOT / rel)
    return sorted(set(files)), excluded


def batches(files):
    out = []
    current = []
    size = 0
    for p in files:
        if p.suffix == '.png':
            continue
        # Preserve CRLF in CSV files so provenance byte hashes remain exact.
        content = p.read_bytes().decode('utf-8')
        row = dict(path=remote_path(p), mode='100755' if p.stat().st_mode & 0o111
                   else '100644', type='blob', content=content)
        n = len(json.dumps(row))
        if current and size + n > 450000:
            out.append(current)
            current = []
            size = 0
        current.append(row)
        size += n
    if current:
        out.append(current)
    return out


def remote_path(p):
    rel = str(p.relative_to(ROOT))
    return 'README.md' if rel == 'publication_essentials/GITHUB_README.md' else rel


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--batch', type=int)
    ap.add_argument('--binary', type=int)
    ap.add_argument('--write-manifest', action='store_true')
    args = ap.parse_args()
    files, excluded = inventory()
    bs = batches(files)
    binary = [p for p in files if p.suffix == '.png']
    if args.batch is not None:
        print(json.dumps(bs[args.batch]))
    elif args.binary is not None:
        p = binary[args.binary]
        print(json.dumps(dict(path=str(p.relative_to(ROOT)),
                              content=base64.b64encode(p.read_bytes()).decode())))
    else:
        result = dict(repository='cona8888/ORCA_Distributed_AL_Robot',
                      local_git_head=subprocess.check_output(
                          ['git', 'rev-parse', 'HEAD'], cwd=ROOT).decode().strip(),
                      source_policy='No simulator source/patches/raw attempts; '
                          'Python files with embedded C++ markers also excluded.',
                      excluded_python_with_cpp_markers=excluded,
                      batch_count=len(bs), binary_count=len(binary),
                      file_count=len(files),
                      total_bytes=sum(p.stat().st_size for p in files),
                      files=[dict(path=remote_path(p), bytes=p.stat().st_size,
                                  sha256=hashlib.sha256(p.read_bytes()).hexdigest(),
                                  git_blob_sha1=hashlib.sha1(
                                      b'blob ' + str(p.stat().st_size).encode() + b'\0' +
                                      p.read_bytes()).hexdigest()) for p in files])
        if args.write_manifest:
            REPORT.write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps({k: v for k, v in result.items() if k != 'files'}, indent=2))


if __name__ == '__main__':
    main()
