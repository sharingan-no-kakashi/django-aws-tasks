import os
import sys
from pathlib import Path

import pytest

from scripts.example_project import example_root

sys.path.insert(0, str(example_root()))

from example_tools.aws_scenarios import Deployment  # noqa: E402


@pytest.fixture(scope="module")
def deployment():
    directory = os.environ.get("TASK_AWS_WORKSPACE")
    if not directory:
        if os.environ.get("REQUIRE_AWS") == "1":
            pytest.fail("Required AWS deployment workspace is missing")
        pytest.skip("Real AWS deployment is not configured")
    return Deployment.load(Path(directory), os.environ["AWS_PROFILE"], os.environ["AWS_REGION"])
