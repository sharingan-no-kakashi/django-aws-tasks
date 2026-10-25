"""Production backend settings for synchronous tests without automatic publication."""

from copy import deepcopy

from tests.settings import TASKS as BASE_TASKS

TASKS = deepcopy(BASE_TASKS)
TASKS["default"]["OPTIONS"]["immediate_dispatch"] = False
