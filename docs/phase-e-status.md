# Phase E implementation and evidence

Consumer deployment/scenario tools referenced below now live in the standalone
example repository’s `example_tools/` package. The original evidence remains
historical; moving the tools does not establish new AWS acceptance.

Phase E infrastructure and workflow code is present; cloud acceptance is pending. Local verification covers Terraform module
validation, plan assertions, the route-manifest round trip, IAM scoping, and the
deployment workflow's plan path. No real AWS apply, SQS-to-Lambda execution, or
destroy result is claimed. Those external acceptance items remain open until the
disposable AWS workflow runs.

| Task | Implementation | Verification / outstanding evidence |
|---|---|---|
| DT-022 | Reusable `task_system` module, typed worker map, immutable image digest, shared route manifest | `terraform/modules/task_system/tests/profiles.tftest.hcl`: isolated profiles, mutable-image rejection, unsafe profile, missing network, reserved identity, missing app config |
| DT-023 | Per-profile source/DLQ queues, redrive policy, Lambda, ESM batch 1/window 0 + partial-batch responses | Module resources; `tests/terraform/test_module.py::test_worker_iam_is_scoped_to_its_source`; **live queue/ESM attributes unverified** |
| DT-024 | Scoped function/producer/scheduler roles, database-secret reference, DB security-group ingress, optional KMS | tftest plan assertions + IAM scoping test |
| DT-025 | EventBridge Scheduler sweeper plus observability module (recovery/function/DLQ/database alarms, dashboard) | `terraform/modules/task_observability/tests/monitoring.tftest.hcl` (2 passed); **alarm firing/SNS delivery unverified** |
| DT-026 | Bootstrap VPC/RDS/ECR/SNS, sample root, and `scripts/deploy_sample.py` workflow | `terraform validate` on both example roots; `scripts/aws_scenarios.py` and `aws_faults.py` import cleanly |
| DT-027 | Terraform fmt/validate/test, provider-mocked plan, manifest round trip | `RUN_TERRAFORM_TESTS=1 uv run pytest tests/terraform` (2 passed); tftest suites (6 task_system + 2 observability) |
| DT-028 | Deployed happy path, transaction rollback, scheduler recovery, duplicate delivery | `scripts/aws_scenarios.py` + `tests/e2e/test_aws_delivery.py`; **requires a real AWS workspace** |

## Reproduce local verification

```sh
terraform -chdir=terraform/modules/task_system init -backend=false
terraform -chdir=terraform/modules/task_system test
terraform -chdir=terraform/modules/task_observability init -backend=false
terraform -chdir=terraform/modules/task_observability test
RUN_TERRAFORM_TESTS=1 uv run pytest tests/terraform
terraform -chdir=examples/sample_project/terraform/bootstrap init -backend=false
terraform -chdir=examples/sample_project/terraform/bootstrap validate
terraform -chdir=examples/sample_project/terraform/application init -backend=false
terraform -chdir=examples/sample_project/terraform/application validate
```

The disposable AWS workflow (`.github/workflows/aws-evidence.yml`) applies the
bootstrap and sample roots, builds the immutable image, migrates through the
private adapter, runs `tests/e2e`, and destroys the stack. Evidence is written
only from observed service responses. Phase E local implementation does not
complete Phase G or H, and does not claim any AWS deployment result until that
workflow runs against a disposable environment.
