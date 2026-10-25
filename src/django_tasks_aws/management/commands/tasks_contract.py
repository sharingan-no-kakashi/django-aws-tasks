"""Emit an artifact-identified manifest or compare one with accepted unfinished work."""

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.tasks import task_backends

from django_tasks_aws.backends import DatabaseSqsBackend
from django_tasks_aws.contracts import build_contract_manifest, compare_contract
from django_tasks_aws.exceptions import ConfigurationError


class Command(BaseCommand):
    help = "Build a task contract with --artifact, or read-only compare with --compare MANIFEST."
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument("--backend", default="default")
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--artifact", type=Path)
        group.add_argument("--compare", type=Path)
        parser.add_argument("--limit", type=int, default=200)

    def handle(self, *args, **options):
        try:
            backend = task_backends[options["backend"]]
            if not isinstance(backend, DatabaseSqsBackend):
                raise ConfigurationError("Select a durable SQL task backend")
            if options["artifact"]:
                result = build_contract_manifest(backend, artifact=options["artifact"])
            else:
                manifest = json.loads(options["compare"].read_text())
                result = compare_contract(backend, manifest, limit=options["limit"])
        except (OSError, ValueError, ConfigurationError) as error:
            raise CommandError(str(error)) from error
        self.stdout.write(json.dumps(result, indent=2))
        if result.get("status") in {"incompatible", "incomplete"}:
            raise CommandError(
                "Candidate contract comparison is " + result["status"],
                returncode=1 if result["status"] == "incompatible" else 2,
            )
