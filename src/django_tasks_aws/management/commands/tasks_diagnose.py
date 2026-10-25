"""Read-only deployment diagnosis, with distinct failure and incomplete-evidence exits."""

import json

from django.core.management.base import BaseCommand, CommandError
from django.tasks import task_backends

from django_tasks_aws.backends import DatabaseSqsBackend
from django_tasks_aws.diagnostics import diagnose
from django_tasks_aws.exceptions import ConfigurationError


class Command(BaseCommand):
    help = "Diagnose schema/writer and optional AWS drift; exit 0 passed, 1 failed, 2 unavailable."
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument("--backend", default="default")
        parser.add_argument("--aws", action="store_true")
        parser.add_argument("--json", action="store_true", dest="as_json")

    def handle(self, *args, **options):
        try:
            backend = task_backends[options["backend"]]
            if not isinstance(backend, DatabaseSqsBackend):
                raise ConfigurationError("Select a DatabaseSqsBackend")
            result = diagnose(backend, cloud=options["aws"])
        except (ConfigurationError, ValueError, KeyError):
            result = {
                "version": 1,
                "backend": options["backend"],
                "status": "failed",
                "exit_code": 1,
                "findings": [
                    {
                        "target": "configuration",
                        "status": "failed",
                        "finding": "Invalid backend configuration",
                        "next_action": "Run Django system checks and correct TASKS settings",
                    }
                ],
            }
        self.stdout.write(json.dumps(result, indent=None if options["as_json"] else 2))
        if result["exit_code"]:
            raise CommandError(
                "Deployment diagnosis is " + result["status"], returncode=result["exit_code"]
            )
