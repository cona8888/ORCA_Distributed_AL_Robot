#!/usr/bin/env python3
"""Losslessly compact raw traces and remove explicitly rebuildable local files.

The script defaults to inventory only. --apply verifies each archive member by
SHA-256 before deleting its original. Simulator source and Git worktrees stay.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tarfile
import time

ROOT = Path(__file__).resolve().parents[2]
STORE = ROOT / 'publication_essentials' / 'raw_archives'
REPORT = ROOT / 'publication_essentials' / 'cleanup_report.json'


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(2**20), b''):
            h.update(chunk)
    return h.hexdigest()


def save(report):
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2) + '\n')


def archive_group(name, paths, report):
    dest = STORE / (name + '.tar.zst')
    manifest = STORE / (name + '.manifest.json')
    if dest.exists() and manifest.exists():
        record = json.loads(manifest.read_text())
        if sha(dest) != record['sha256']:
            raise RuntimeError(f'Existing archive hash mismatch: {dest}')
        subprocess.run(['zstd', '-q', '-t', str(dest)], check=True)
        expected = {row['path']: row for row in record['members']}
        for p in paths:
            row = expected[str(p.relative_to(ROOT))]
            if p.stat().st_size != row['bytes'] or sha(p) != row['sha256']:
                raise RuntimeError(f'Original changed: {p}')
            unlink_sealed(p)
        if not any(a['archive'] == record['archive'] for a in report['archives']):
            report['archives'].append({k: v for k, v in record.items() if k != 'members'})
        save(report)
        print(f'RESUMED verified archive {name}', flush=True)
        return
    if dest.exists() or manifest.exists():
        raise RuntimeError(f'Incomplete archive; inspect before retrying: {dest}')
    rows = [dict(path=str(p.relative_to(ROOT)), bytes=p.stat().st_size,
                 sha256=sha(p)) for p in paths]
    temp = dest.with_suffix('.zst.partial')
    with temp.open('wb') as out:
        compressor = subprocess.Popen(['zstd', '-q', '-T2', '-6', '-c'],
                                      stdin=subprocess.PIPE, stdout=out)
        try:
            with tarfile.open(fileobj=compressor.stdin, mode='w|') as tar:
                for p, row in zip(paths, rows):
                    tar.add(p, arcname=row['path'], recursive=False)
        finally:
            compressor.stdin.close()
        if compressor.wait() != 0:
            raise RuntimeError('zstd compression failed')
    expected = {row['path']: row for row in rows}
    seen = set()
    decoder = subprocess.Popen(['zstd', '-q', '-d', '-c', str(temp)],
                               stdout=subprocess.PIPE)
    try:
        with tarfile.open(fileobj=decoder.stdout, mode='r|') as tar:
            for member in tar:
                if not member.isfile():
                    continue
                if member.name not in expected or member.name in seen:
                    raise RuntimeError('Unexpected/duplicate archive member')
                h = hashlib.sha256()
                f = tar.extractfile(member)
                for chunk in iter(lambda: f.read(2**20), b''):
                    h.update(chunk)
                row = expected[member.name]
                if member.size != row['bytes'] or h.hexdigest() != row['sha256']:
                    raise RuntimeError(f'Archive verification failed: {member.name}')
                seen.add(member.name)
        # Drain the complete zstd frame so the checksum is checked as well.
        while decoder.stdout.read(2**20):
            pass
    finally:
        decoder.stdout.close()
    if decoder.wait() != 0 or seen != set(expected):
        raise RuntimeError('Incomplete/corrupt archive')
    subprocess.run(['zstd', '-q', '-t', str(temp)], check=True)
    temp.rename(dest)
    record = dict(archive=str(dest.relative_to(ROOT)), sha256=sha(dest),
                  source_bytes=sum(r['bytes'] for r in rows),
                  archive_bytes=dest.stat().st_size, members=rows,
                  verification='All member SHA-256 values and zstd checksum passed')
    manifest.write_text(json.dumps(record, indent=2) + '\n')
    # Recheck files to avoid deleting anything changed during compression.
    for p, row in zip(paths, rows):
        if p.stat().st_size != row['bytes'] or sha(p) != row['sha256']:
            raise RuntimeError(f'Original changed; no further deletion: {p}')
        unlink_sealed(p)
    report['archives'].append({k: v for k, v in record.items() if k != 'members'})
    save(report)
    print(f"VERIFIED {name}: {record['source_bytes'] / 2**30:.2f} GiB -> "
          f"{record['archive_bytes'] / 2**20:.1f} MiB", flush=True)


def unlink_sealed(path):
    """Temporarily open only the owning sealed directory, then restore its mode."""
    mode = stat.S_IMODE(path.parent.stat().st_mode)
    if mode & stat.S_IWUSR:
        path.unlink()
        return
    path.parent.chmod(mode | stat.S_IWUSR)
    try:
        path.unlink()
    finally:
        path.parent.chmod(mode)


def remove_rebuildable(path):
    """Remove an inventoried generated path, including sealed build caches."""
    parent_mode = stat.S_IMODE(path.parent.stat().st_mode)
    path.parent.chmod(parent_mode | stat.S_IWUSR | stat.S_IXUSR)
    try:
        if path.is_dir():
            for base, dirs, files in os.walk(path):
                current = Path(base)
                current.chmod(stat.S_IMODE(current.stat().st_mode) | 0o700)
            shutil.rmtree(path)
        else:
            path.unlink()
    finally:
        path.parent.chmod(parent_mode)


def removable_builds():
    # A build directory contains source-pinned worktrees. Keep every worktree;
    # only its ignored generated build subdirectory may be removed.
    keep = {'reference-src', 'optimized-src', 'physics-final', 'trace-v3'}
    if (ROOT / 'build').is_dir():
        for p in (ROOT / 'build').iterdir():
            if p.name != 'comparison':
                yield p
    comp = ROOT / 'build' / 'comparison'
    if comp.is_dir():
        for p in comp.iterdir():
            if p.name not in keep:
                yield p
        for parent in [comp, comp / 'physics-final', comp / 'trace-v3']:
            if parent.is_dir():
                for p in parent.iterdir():
                    if p.name.endswith('-src') and (p / '.git').is_file():
                        if (p / 'build').is_dir():
                            yield p / 'build'
        trace = comp / 'trace-v3'
        if trace.is_dir():
            for p in trace.iterdir():
                if not p.name.endswith('-src'):
                    yield p
    for name in ['.pixi', '.local']:
        if (ROOT / name).is_dir():
            yield ROOT / name


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()
    tracked = set(subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT)
                  .decode().split('\0'))
    groups = {}
    for p in sorted((ROOT / 'results').rglob('*.ndjson')):
        rel = p.relative_to(ROOT)
        if str(rel) in tracked or p.name != 'trace.ndjson':
            continue
        parts = rel.parts
        key = '-'.join(parts[1:5] if parts[1] == 'simulator-demo' else parts[1:3])
        groups.setdefault(key, []).append(p)
    report = dict(date='2026-10-23', root=str(ROOT),
                  local_git_head=subprocess.check_output(
                      ['git', 'rev-parse', 'HEAD'], cwd=ROOT).decode().strip(),
                  archives=[], deleted_rebuildable=[],
                  note='No scientific evidence discarded; expanded untracked traces '
                       'are replaced by SHA-256-verified lossless archives. '
                       'All source worktrees, tracked results, compact analyses, '
                       'figures, configs, protocols and local Git history remain.')
    if REPORT.exists():
        report = json.loads(REPORT.read_text())
        if report.get('completed'):
            raise RuntimeError('Cleanup already completed; no repeat deletion')
    for name, paths in groups.items():
        print(name, len(paths), sum(p.stat().st_size for p in paths), flush=True)
    print('Rebuildable:', [str(p.relative_to(ROOT)) for p in removable_builds()], flush=True)
    if not args.apply:
        return
    STORE.mkdir(parents=True, exist_ok=True)
    save(report)
    for name, paths in groups.items():
        archive_group(name, paths, report)
    for p in list(removable_builds()):
        if p.is_symlink():
            raise RuntimeError(f'Refusing symlink: {p}')
        # Never remove a registered worktree or a tracked path.
        rel = str(p.relative_to(ROOT))
        if any(t == rel or t.startswith(rel + '/') for t in tracked if t):
            raise RuntimeError(f'Tracked path in deletion candidate: {p}')
        if p.is_dir():
            size = sum(f.stat().st_size for f in p.rglob('*') if f.is_file())
        else:
            size = p.stat().st_size
        remove_rebuildable(p)
        report['deleted_rebuildable'].append(dict(path=rel, bytes=size))
        save(report)
        print(f'REMOVED rebuildable {rel}', flush=True)
    report['completed'] = True
    report['reclaimed_logical_bytes'] = sum(
        a['source_bytes'] - a['archive_bytes'] for a in report['archives']) + sum(
        d['bytes'] for d in report['deleted_rebuildable'])
    save(report)
    print('Reclaimed logical GiB:', report['reclaimed_logical_bytes'] / 2**30, flush=True)


if __name__ == '__main__':
    main()
