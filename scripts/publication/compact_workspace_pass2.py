#!/usr/bin/env python3
"""Remove clean duplicate worktrees and losslessly archive older raw JSONL.

Dry-run by default. All source branches/history and compact analyses remain.
Tracked raw files removed from the working tree are listed in the archive
manifests; their originals remain recoverable from both the archive and Git.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

import compact_workspace as first

ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / 'publication_essentials' / 'cleanup_pass2_report.json'
first.REPORT = REPORT
BUNDLE = ROOT / 'results/publication/dancers-multimap-v1-second-host-capsule/Beyond-Dancers.bundle'


def git(*args, cwd=ROOT):
    return subprocess.check_output(['git', *args], cwd=cwd).decode()


def worktrees():
    rows = []
    for block in git('worktree', 'list', '--porcelain', '-z').split('\0\0'):
        fields = dict(s.split(' ', 1) for s in block.split('\0') if ' ' in s)
        if not fields:
            continue
        path = Path(fields['worktree'])
        if path == ROOT:
            continue
        if not path.is_relative_to(ROOT / 'build/comparison'):
            raise RuntimeError(f'Out-of-scope worktree: {path}')
        if git('status', '--porcelain=v1', '--untracked-files=all', cwd=path):
            raise RuntimeError(f'Non-clean worktree: {path}')
        ignored = git('ls-files', '--others', '--ignored', '--exclude-standard',
                      cwd=path).splitlines()
        if any('__pycache__/' not in s or not s.endswith('.pyc') for s in ignored):
            raise RuntimeError(f'Unpreserved ignored file: {path}')
        branch = fields.get('branch')
        if not branch or git('rev-parse', branch).strip() != fields['HEAD']:
            raise RuntimeError(f'Missing pinned branch: {path}')
        rows.append(dict(path=str(path.relative_to(ROOT)), head=fields['HEAD'],
                         branch=branch,
                         allocated_bytes=sum(f.stat().st_blocks * 512
                             for f in path.rglob('*') if f.is_file()),
                         restore=f"git worktree add '{path.relative_to(ROOT)}' "
                                 f"'{branch.removeprefix('refs/heads/')}'"))
    return rows


def bundle_record():
    if not BUNDLE.exists():
        return None
    heads = [dict(sha=s.split(' ', 1)[0], ref=s.split(' ', 1)[1])
             for s in git('bundle', 'list-heads', str(BUNDLE)).splitlines()]
    # Check the complete object closure of every bundle tip is already local.
    closure = git('rev-list', '--objects', '--missing=print',
                  *sorted({h['sha'] for h in heads}))
    if any(line.startswith('?') for line in closure.splitlines()):
        raise RuntimeError('Bundle contains source objects not available locally')
    return dict(path=str(BUNDLE.relative_to(ROOT)), bytes=BUNDLE.stat().st_size,
                sha256=first.sha(BUNDLE), heads=heads,
                verification='Every bundle tip and its complete object closure '
                             'exists in retained local Git history',
                note='Redundant transport package removed; its original byte '
                     'identity is recorded, but is no longer a retained artifact. '
                     'A replacement transfer bundle can be regenerated from '
                     'the retained source revisions; it may have a new checksum.')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()
    groups = {}
    for name, rel in [
        ('older-comparison-calibration-jsonl', 'results/comparison/calibration'),
        ('older-visual-diagnostic-jsonl', 'results/simulator-demo/v1/visual-diagnostic')]:
        files = sorted((ROOT / rel).rglob('*.jsonl'))
        if files:
            groups[name] = files
    wt = worktrees()
    bundle = bundle_record()
    print(json.dumps(dict(worktrees=wt, raw_groups={name: dict(files=len(paths),
          bytes=sum(p.stat().st_size for p in paths)) for name, paths in groups.items()},
          bundle=bundle), indent=2), flush=True)
    if not args.apply:
        return
    if REPORT.exists():
        raise RuntimeError('Pass-2 report already exists; inspect before retry')
    report = dict(date='2026-10-23', archives=[], removed_worktrees=[],
                  removed_bundle=None, local_git_head=git('rev-parse', 'HEAD').strip(),
                  baseline_tracked_status=git('status', '--porcelain=v1',
                                             '--untracked-files=no'),
                  note='Main source checkout and all Git branches/history retained. '
                       'Only expanded older raw logs are removed from tracked '
                       'working-tree paths; verified lossless copies are retained.')
    first.save(report)
    for name, paths in groups.items():
        first.archive_group(name, paths, report)
    for row in wt:
        # git itself refuses removal if new changes appeared after inventory.
        subprocess.run(['git', 'worktree', 'remove', str(ROOT / row['path'])],
                       cwd=ROOT, check=True)
        assert git('rev-parse', row['branch']).strip() == row['head']
        report['removed_worktrees'].append(row)
        first.save(report)
        print('REMOVED clean duplicate worktree', row['path'], flush=True)
    if bundle:
        if first.sha(BUNDLE) != bundle['sha256']:
            raise RuntimeError('Bundle changed since inventory')
        bundle['preserved_local_refs'] = []
        for sha in sorted({head['sha'] for head in bundle['heads']}):
            ref = 'refs/archive/storage-cleanup-20261023/bundle-tip/' + sha
            subprocess.run(['git', 'update-ref', ref, sha], cwd=ROOT, check=True)
            bundle['preserved_local_refs'].append(ref)
        first.unlink_sealed(BUNDLE)
        report['removed_bundle'] = bundle
        first.save(report)
        print('REMOVED redundant transfer bundle', flush=True)
    report['completed'] = True
    report['reclaimed_bytes_estimate'] = sum(a['source_bytes'] - a['archive_bytes']
        for a in report['archives']) + sum(w['allocated_bytes']
        for w in report['removed_worktrees']) + (bundle['bytes'] if bundle else 0)
    first.save(report)
    print('Additional recovered GiB:', report['reclaimed_bytes_estimate'] / 2**30,
          flush=True)


if __name__ == '__main__':
    main()
