# Candidate verification and release

A release consists of one source revision, one wheel, one source distribution,
and the AWS images built from those exact distributions. The candidate workflow
builds once, runs the declared compatibility matrix, Terraform checks, installed
consumer/browser checks, disposable AWS acceptance, and capacity/cost checks.
It produces `verified-release-candidate` only after all mandatory evidence passes.

Configure the GitHub `aws-verification` environment and repository variables
`AWS_VERIFICATION_ROLE` and `AWS_VERIFICATION_REGION` for the approved disposable
account. Configure `AWS_CAPACITY_COST_INPUTS` with sourced regional usage/pricing
inputs as described below. Missing variables or results prevent release; they
are not waived by a green local test run. Relevant main-branch changes and the
weekly schedule also exercise AWS when the account variables are configured.

The candidate gate uses `.github/release-policy.json` to require every Python,
PostgreSQL, dependency-bound, infrastructure, AWS, and capacity suite. Receipts
include candidate ID, source digest, CI run, timestamp, report hashes and test
identities, plus retained evidence. The gate rejects stale/foreign receipts,
missing suites, skipped/failed mandatory tests, and changed distribution bytes.
The source digest includes the sample, infrastructure, docs, tests, and workflows.
A changed candidate must be verified again.

To create a local candidate:

```sh
uv build --no-sources --out-dir /tmp/task-candidate/dist
uv run python -m scripts.release_evidence candidate \
  --candidate /tmp/task-candidate/candidate.json --dist /tmp/task-candidate/dist \
  --output /tmp/task-candidate/candidate.json
```

After running a suite against this source and candidate, record its reports:

```sh
uv run python -m scripts.release_evidence record \
  --candidate /tmp/task-candidate/candidate.json --dist /tmp/task-candidate/dist \
  --suite terraform --report artifacts/terraform.xml \
  --output /tmp/task-candidate/receipts/terraform.receipt.json
```

A local receipt cannot substitute for a different CI candidate's evidence.
Review the acceptance matrix and retained observations alongside the suite reports.

## Capacity and regional cost evidence

`../django-aws-tasks-example/workload.json` declares a synthetic short/document
CPU-memory mix, acceptance thresholds, and three concurrency experiments.
the example repository’s `example_tools.capacity_run` runs a five-minute 500/minute burst and a separate
sustained experiment under each budget, retaining outcomes and database/queue
series. Failed lower-capacity experiments remain in the report; a supported
budget must pass the configured objectives for each workload class. Adapt these
inputs to actual application duration/resource distributions before claiming a
production workload envelope.

Pass `--cost-inputs FILE` containing `region`, a primary `source` URL or billing
reference, `as_of`, and a `categories` mapping. Each category provides actual
observed `usage`, `unit_price`, and `unit`. Categories must include
`lambda_compute`, `lambda_requests`, `sqs`, `scheduler`, `logs_metrics`, `network`,
and `database_storage`; add architecture/memory/runtime, allowances, storage,
transfer, and retry assumptions as applicable. The tool multiplies those stated
inputs; it does not discover prices, manufacture measurements, or claim this is
an AWS invoice. Retain the original usage/billing observations with the run.
An unpriced run cannot pass release cost acceptance.

## Publish the verified bytes

Establish the chosen PyPI project and configure its Trusted Publisher for
`.github/workflows/publish.yml` and the `pypi` GitHub environment. Configure
appropriate environment access and review controls. The official setup is
[PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/using-a-publisher/).

Run `Publish verified candidate` only as the separate release action, providing
its verified workflow run ID and exact source commit. It downloads the verified
artifact, revalidates candidate identity, freshness and distribution hashes,
and publishes those files without rebuilding. Trusted Publishing uses the
workflow's short-lived identity; no persistent PyPI token is required.

After publication, tag the matching revision, retain release notes and the
manifest, download the public artifacts, compare hashes, and repeat the installed
consumer smoke checks. Use the resulting public artifacts and release tag when
announcing a version.

## Maintainer sequence

Complete the [GitHub setup checklist](github-maintenance.md) first. Use
Conventional Commit squash titles so Release Please can prepare the changelog and
version PR. Review the proposed version, workspace lock, package version, and
fixed-version test assertion as described in that checklist; merge only after
required PR checks pass.

1. Run `Verify release candidate` on `main` after the release PR is merged.
   Retain the successful run ID and its full 40-character commit SHA. Missing AWS
   or capacity evidence must fail the gate.
2. Run `Publish verified candidate` on `main` with that run ID and SHA. The
   workflow verifies the run's workflow path, event, repository, branch, result,
   and commit before downloading the candidate. Approve the protected `pypi`
   deployment according to the repository's reviewer policy.
3. After publication, the workflow compares the public distribution hashes and
   runs the installed wheel/source consumer checks. Only then does its separate
   GitHub-release job create `v<version>` at the candidate commit, generate notes,
   and attach the exact distributions and manifests.
4. Retain release evidence and move the merged release PR from
   `autorelease: pending` to `autorelease: tagged` as described in the checklist.
   Verify that release notes describe compatibility changes and operator actions.

If the package is public but a later check fails, follow the recovery procedure
in the checklist. PyPI publication cannot be undone by deleting a GitHub tag.
