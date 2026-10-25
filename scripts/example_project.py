"""Used by acceptance and installed-package checks to locate the companion example.
A shared locator supports separate checkouts and the TASK_EXAMPLE_ROOT override.
"""

import os
from pathlib import Path


def example_root():
    library = Path(__file__).resolve().parents[1]
    path = Path(os.environ.get("TASK_EXAMPLE_ROOT", library.parent / "django-aws-tasks-example"))
    if not (path / "sample_project/settings.py").is_file():
        raise ValueError(f"Example repository missing at {path}; set TASK_EXAMPLE_ROOT")
    return path.resolve()
