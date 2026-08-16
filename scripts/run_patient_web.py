"""Run the local-only Figma-derived patient web client."""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT_TEXT = str(PROJECT_ROOT)
sys.path[:] = [item for item in sys.path if item != PROJECT_ROOT_TEXT]
sys.path.insert(0, PROJECT_ROOT_TEXT)

import continucare
from continucare.patient_web import main


if Path(continucare.__file__).resolve().parent.parent != PROJECT_ROOT:
    raise RuntimeError("patient web imported continucare from outside this project")


if __name__ == "__main__":
    main()
