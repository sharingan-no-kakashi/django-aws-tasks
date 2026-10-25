"""Run in artifact and publication checks to test an installed wheel or sdist.
An isolated consumer detects packaging defects that source-checkout tests can miss.
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from importlib.metadata import version
from pathlib import Path

from scripts.example_project import example_root

ROOT = Path(__file__).resolve().parents[1]

# Declared lower bounds from pyproject.toml. ``psycopg[binary]==3.2.0`` points at
# an unpublished dev1 binary extra, so the first installable release is pinned.
_MINIMUM_RUNTIME_BOUNDS = [
    "aws-lambda-powertools[tracer,parser]==3.35.0",
    "boto3==1.40.0",
    "django==6.1",
    "pydantic==2.12.0",
    "tenacity==8.4.2",
    "psycopg[binary]==3.2.1",
]


def run(*arguments, cwd, environment):
    subprocess.run(arguments, cwd=cwd, env=environment, check=True)


def copy_verification_suite(destination):
    tests = destination / "tests"
    tests.mkdir()
    for name in (
        "__init__.py",
        "settings.py",
        "local_settings.py",
        "conftest.py",
        "demo_tasks.py",
        "admin_urls.py",
    ):
        shutil.copy2(ROOT / "tests" / name, tests / name)
    for name in ("integration", "support"):
        shutil.copytree(
            ROOT / "tests" / name, tests / name, ignore=shutil.ignore_patterns("__pycache__")
        )


def verify(wheel, report, browser=False, python=sys.executable, bounds="latest"):
    environment = {**os.environ, "REQUIRE_POSTGRES": "1", "AWS_EC2_METADATA_DISABLED": "true"}
    environment.pop("PYTHONPATH", None)
    environment["DJANGO_SETTINGS_MODULE"] = "tests.settings"
    with tempfile.TemporaryDirectory(prefix="django-tasks-installed-") as folder:
        directory = Path(folder)
        interpreter = directory / "venv" / "bin" / "python"
        run(
            "uv",
            "venv",
            "--python",
            python,
            str(directory / "venv"),
            cwd=directory,
            environment=environment,
        )
        install(interpreter, wheel, directory, environment, browser, bounds)
        copy_verification_suite(directory)
        run(str(interpreter), "-c", package_probe(), cwd=directory, environment=environment)
        for workflows in (False, True):
            run(
                str(interpreter),
                "-c",
                headless_probe(workflows),
                cwd=directory,
                environment=environment,
            )
        run(
            str(interpreter),
            "-m",
            "pytest",
            "tests/integration",
            "--ds=tests.settings",
            "-q",
            f"--junitxml={report}",
            cwd=directory,
            environment=environment,
        )

        if browser:
            verify_browser(
                interpreter, directory, environment, report.with_name("installed-browser.xml")
            )


def install(interpreter, wheel, directory, environment, browser, bounds):
    packages = [
        str(wheel),
        "pytest>=9.0.3,<10",
        "pytest-django>=4.11,<5",
        "hypothesis>=6.140,<7",
    ]
    if bounds == "minimum":
        packages.extend(_MINIMUM_RUNTIME_BOUNDS)
    else:
        packages.append("psycopg[binary]>=3.2.1,<4")
    if browser:
        packages.extend([str(example_root()), f"playwright=={version('playwright')}"])
    run(
        "uv",
        "pip",
        "install",
        "--no-sources",
        "--python",
        str(interpreter),
        *packages,
        cwd=directory,
        environment=environment,
    )


def verify_browser(interpreter, directory, environment, report):
    probe = (
        "from pathlib import Path; import sample_app; "
        "print(Path(sample_app.__file__).parent / 'tests/test_operator_browser.py')"
    )
    test_path = subprocess.check_output(
        [str(interpreter), "-c", probe], cwd=directory, env=environment, text=True
    ).strip()
    environment = {
        **environment,
        "RUN_BROWSER_TESTS": "1",
        "DJANGO_SETTINGS_MODULE": "sample_project.settings",
    }
    run(
        str(interpreter),
        "-m",
        "pytest",
        test_path,
        "--ds=sample_project.settings",
        "-q",
        "--junit-prefix=examples.sample_project",
        "-o",
        "markers=browser: real browser journey",
        f"--junitxml={report}",
        cwd=directory,
        environment=environment,
    )


def package_probe():
    return """
from pathlib import Path
import django_tasks_aws
from django_tasks_aws.handlers.worker import lambda_handler
from django_tasks_aws.handlers.sweeper import lambda_handler
root = Path(django_tasks_aws.__file__).parent
assert "site-packages" in str(root), root
assert (root / "templates/django_tasks_aws/review.html").is_file()
assert (root / "templates/django_tasks_aws/detail.html").is_file()
assert (root / "management/commands/tasks_replay.py").is_file()
assert (root / "management/commands/tasks_review_invariant.py").is_file()
assert sorted(path.name for path in (root / "migrations").glob("[0-9]*.py")) == ["0001_initial.py"]
assert (root / "workflows/migrations/0001_initial.py").is_file()
assert sorted(path.name for path in (root / "workflows/migrations").glob("[0-9]*.py")) == [
    "0001_initial.py"
]
assert (root / "workflows/management/commands/workflows_retry.py").is_file()
assert (root / "workflows/management/commands/workflows_cancel.py").is_file()
assert (root / "templates/django_tasks_aws/workflow_review.html").is_file()
print("Installed entry points, operator commands, templates, and migrations verified:", root)
"""


def headless_probe(workflows):
    installed = ["django_tasks_aws"]
    if workflows:
        installed.append("django_tasks_aws.workflows")
    return f"""
import sys
from django.conf import settings
settings.configure(INSTALLED_APPS={installed!r}, SECRET_KEY="headless",
    DJANGO_TASKS_WORKFLOW_ADAPTER="consumer.adapter.CustomAdapter")
import django
django.setup()
assert "django_tasks_aws.workflows.admin" not in sys.modules
assert "django_tasks_aws.workflows.aws" not in sys.modules
print("Headless installed application ready; workflows enabled:", {workflows!r})
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=ROOT / "dist")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument(
        "--browser", action="store_true", help="Also test the installed sample in Chromium"
    )
    parser.add_argument("--artifact", choices=["wheel", "sdist"], default="wheel")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--dependency-bounds", choices=["minimum", "latest"], default="latest")
    options = parser.parse_args()
    pattern = (
        "django_tasks_aws-*.whl" if options.artifact == "wheel" else "django_tasks_aws-*.tar.gz"
    )
    wheels = list(options.dist.resolve().glob(pattern))
    if len(wheels) != 1:
        parser.error(
            "Provide a distribution directory containing exactly one selected backend artifact"
        )
    report = options.report.resolve()
    report.parent.mkdir(parents=True, exist_ok=True)
    verify(wheels[0], report, options.browser, options.python, options.dependency_bounds)


if __name__ == "__main__":
    main()
