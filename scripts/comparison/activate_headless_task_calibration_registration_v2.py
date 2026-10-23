#!/usr/bin/env python3
"""Execute the one exact epoch-3 plan registration and nothing else."""

from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath
import sys
import types
from typing import Any


sys.dont_write_bytecode = True

BASE = {
    "bytes": 6077,
    "path": "scripts/comparison/activate_headless_task_calibration_registration_v1.py",
    "sha256": "4bf56338dbec7f98aca0fbc1ee5b827d7b8492cd98a44e3cc23c677f71be30a9",
}
AUTH_PATH = "docs/simulator/headless_task_calibration_epoch_3_registration_v1.json"
REGISTRAR_PATH = "scripts/comparison/register_headless_task_calibration_epoch_v2.py"
REGISTRAR_IDENTITY = {
    "bytes": 2524,
    "path": REGISTRAR_PATH,
    "sha256": "5348d4d83c621c0b97b214cea0db2e730786ff9a50ba34564e09789a8eec739e",
}
EXPECTED_PLAN = {
    "file_count": 63,
    "plan_bytes": 349797,
    "plan_sha256": "2eb98865b61d185d7df92b79445e978b4ccab574113437cc7f1bc3bdbd6f0636",
    "total_bytes": 475179,
}


class ActivationError(ValueError):
    """The exact epoch-3 registration authority differs."""


def _core(root: Path) -> types.ModuleType:
    raw = root.joinpath(*PurePosixPath(BASE["path"]).parts).read_bytes()
    if len(raw) != BASE["bytes"] or hashlib.sha256(raw).hexdigest() != BASE["sha256"]:
        raise ActivationError("base registration activation identity differs")
    name = "_dancers_epoch3_registration_activation_core"
    module = types.ModuleType(name)
    module.__file__ = BASE["path"]
    sys.modules[name] = module
    exec(compile(raw, module.__file__, "exec", dont_inherit=True, optimize=0), module.__dict__)
    module.AUTH_PATH = AUTH_PATH
    module.REGISTRAR_PATH = REGISTRAR_PATH
    module.REGISTRAR_IDENTITY = dict(REGISTRAR_IDENTITY)
    module.EXPECTED_PLAN = dict(EXPECTED_PLAN)
    return module


def execute_one_use_registration(repository_root: Path) -> dict[str, Any]:
    root = Path(repository_root).resolve()
    return _core(root).execute_one_use_registration(root)


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    try:
        receipt = execute_one_use_registration(root)
    except BaseException as error:
        print(f"epoch-3 task registration rejected: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    sys.stdout.buffer.write(_core(root)._canonical(receipt))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
