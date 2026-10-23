#!/usr/bin/env python3
"""Prepare the exact registered epoch-5 validation plan; publication disabled."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
import sys
import types
from typing import Any


sys.dont_write_bytecode = True

PLAN_ROOT = PurePosixPath("comparison/plans/simulator-demo/v1/headless-epoch-5-validation")
FREEZER_PATH = PurePosixPath("scripts/comparison/freeze_headless_validation_epoch_v1.py")
FREEZER_IDENTITY = {
    "bytes": 19606,
    "path": FREEZER_PATH.as_posix(),
    "sha256": "825fd22e81e57c1d65e09ac33b4363798ebcd02297ef9643022d8aeacb895213",
}
BASE = {
    "bytes": 7629,
    "path": "scripts/comparison/register_headless_task_calibration_epoch_v1.py",
    "sha256": "2877d089117411de7aee2018213e03cd1cbccedbcb4f7f7fc47217e1497132d5",
}
REGISTRATION_SCHEMA = "dancers-headless-validation-registration-receipt-v1"


class RegistrationError(ValueError):
    """The validation registration candidate differs."""


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _core(root: Path) -> types.ModuleType:
    raw = root.joinpath(*PurePosixPath(BASE["path"]).parts).read_bytes()
    if len(raw) != BASE["bytes"] or _sha(raw) != BASE["sha256"]:
        raise RegistrationError("base registrar identity differs")
    module = types.ModuleType("_dancers_validation_registrar_core")
    module.__file__ = BASE["path"]
    sys.modules[module.__name__] = module
    exec(compile(raw, module.__file__, "exec", dont_inherit=True, optimize=0), module.__dict__)
    module.PLAN_ROOT = PLAN_ROOT
    module.FREEZER_PATH = FREEZER_PATH
    module.FREEZER_IDENTITY = dict(FREEZER_IDENTITY)
    module.REGISTRATION_SCHEMA = REGISTRATION_SCHEMA
    return module


def prepare_registered_compilation(repository_root: Path) -> Any:
    root = Path(os.path.abspath(repository_root))
    return _core(root).prepare_registered_compilation(root)


def publish_registered_create_exclusive(repository_root: Path, compilation: Any) -> dict[str, Any]:
    root = Path(os.path.abspath(repository_root))
    return _core(root).publish_registered_create_exclusive(root, compilation)


def main() -> int:
    raise RegistrationError("validation plan registration is disabled pending a committed one-use authorization")


if __name__ == "__main__":
    try:
        main()
    except RegistrationError as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
