"""Executable artifact gates run only in clean-consumer CI jobs."""

import os
from pathlib import Path

from scripts.verify_installed import verify

ROOT = Path(__file__).resolve().parents[2]


def verify_distribution(kind):
    extension = "*.whl" if kind == "wheel" else "*.tar.gz"
    distributions = list((ROOT / "dist").glob(extension))
    assert len(distributions) == 1
    report = (ROOT / "artifacts" / f"installed-{kind}.xml").resolve()
    report.parent.mkdir(exist_ok=True)
    verify(
        distributions[0],
        report,
        browser=True,
        python=os.environ["VERIFY_PYTHON"],
        bounds=os.environ.get("VERIFY_BOUNDS", "latest"),
    )


def test_installed_wheel_migrations_commands_and_browser():
    verify_distribution("wheel")


def test_installed_sdist_migrations_commands_and_browser():
    verify_distribution("sdist")
