# GitHub maintenance

Files in this repository define the automation. GitHub settings, external
accounts, and access controls must be configured by a repository administrator.
Do this after the workflows are merged; their presence alone does not configure
branch protection, AWS, or PyPI. No live repository settings are changed by these
files.

## Repository setup

1. Enable Issues and private vulnerability reporting. Verify the private report
   link in `SECURITY.md` works for a non-maintainer.
2. Confirm `.github/CODEOWNERS` names an active collaborator with write access.
   Enable squash merging and use the PR title as the squash commit title.
3. Allow GitHub Actions to create pull requests under Settings → Actions →
   General. Keep the default workflow token read-only; workflows request their
   additional permissions explicitly.
4. Protect `main` with a ruleset: require pull requests, code-owner review,
   resolved conversations, current approvals after changes, and successful
   `CI required` and `conventional-title` checks. Select checks after their first
   successful run. Block force pushes and deletion; keep bypass access limited.
   Both workflows support merge queues; the title is validated on PR events,
   and the title job also reports success for the resulting merge group.
5. Enable Dependabot alerts and security updates. Weekly update PRs cover Actions
   and the library's uv dependencies. Review major upgrades. Refresh the sample
   lock with `make export-lock` in the example repository when its dependencies
   change. Add Terraform update policies separately when the provider constraints
   and committed lock files can be reviewed together.
6. Optional release-note labels in `.github/release.yml` are `breaking-change`,
   `enhancement`, `bug`, `dependencies`, and `skip-changelog`. Create those labels
   if using GitHub-generated category notes. Issue forms do not depend on labels.

Fork PRs use `pull_request` with a read-only token. Do not change these workflows
to execute PR code under `pull_request_target` or grant PRs deployment secrets.
External Actions are pinned to commits and maintained by Dependabot, following
[GitHub's secure-use guidance](https://docs.github.com/en/actions/reference/security/secure-use).

## Release PR setup

The [Release Please action](https://github.com/googleapis/release-please-action)
opens version/changelog PRs on `main`. Its Python strategy updates
`pyproject.toml` and `src/django_tasks_aws/__init__.py`; the extra TOML updater
keeps the library's workspace version in `uv.lock` aligned. Documentation reads
the package version. The sample application's own version is independent.
The lock selector uses `name.value` because Release Please's TOML parser wraps
scalar values; recheck this updater when upgrading the release action.

The manifest starts at the existing `0.1.0` development baseline. It does not
assert an existing PyPI release. Review the proposed next version before merging;
pre-1.0 features and fixes use patch bumps and breaking changes use minor bumps.

Release PRs created with `GITHUB_TOKEN` do not automatically trigger other
workflows. A maintainer must close and reopen the PR to trigger its PR checks,
or configure a dedicated GitHub App token for Release Please if automatic checks
are needed. Do not bypass required checks to merge an unchecked release PR.
See the action's [token guidance](https://github.com/googleapis/release-please-action#github-credentials).

Before merging a release PR, inspect all version updates, run `uv lock --check`,
refresh the sample export if needed, and update the existing fixed-version
assertion in `tests/unit/test_package.py` for the proposed version. Prefer changing
that assertion to compare runtime and distribution metadata in a dedicated test
maintenance change. The release automation deliberately does not edit tests.

Release Please only opens PRs (`skip-github-release: true`). Merging one does not
publish to PyPI or create a GitHub release. The separate publish workflow does
both only after the candidate gate and public installation checks pass.

## Protected environments

Create `aws-verification` for disposable AWS acceptance and `pypi` for publication.
Limit deployment branches to `main`, require reviewers where available, and avoid
allowing self-review or administrative bypass for releases.

Configure these repository variables:

| Variable | Purpose |
| --- | --- |
| `AWS_VERIFICATION_ROLE` | OIDC role for an approved disposable account |
| `AWS_VERIFICATION_REGION` | Region approved for acceptance resources |
| `AWS_CAPACITY_COST_INPUTS` | Sourced usage/pricing JSON described in `docs/release.md` |

Scope AWS role trust to this repository and the `aws-verification` environment.
Grant only the resource and service access needed by the documented disposable
deployment. Scheduled and relevant main-branch runs can create billable resources
once configured. Failed teardown needs operator follow-up; do not cancel a
running deployment to supersede it. Evidence artifacts can contain account and
resource identifiers; limit access and inspect them before sharing externally.

Configure a PyPI Trusted Publisher for this repository, workflow `publish.yml`,
and environment `pypi`, using the
[official PyPI procedure](https://docs.pypi.org/trusted-publishers/adding-a-publisher/).
No persistent PyPI token is used. Missing external acceptance or environment
configuration blocks release readiness.

## Checks and recovery

The CI workflow checks formatting, lint, traceability, sample lock drift,
PostgreSQL behavior, browser journeys, installed artifacts, Terraform, docs, and
workflow syntax. `CI required` fails if any required job fails or skips. A green
PR run does not substitute for the deployed AWS and capacity release gate.

The publish workflow accepts only a successful `release-candidate.yml` manual run
from this repository's `main` at the exact supplied full commit SHA. It downloads
and revalidates the original bytes. Publication is serialized; GitHub tagging
and asset attachment follow successful public package hash and consumer checks.

If publication succeeds but a later public check or GitHub-release step fails,
the version may already exist on PyPI. Do not rebuild or try to replace it.
Inspect retained evidence and the public artifacts first. Resume only failed
post-publication work: `verify-publication` and `github-release` are separate
jobs, so rerunning failed jobs does not republish after `publish` succeeds.
If a tag or release already exists after a partial GitHub API failure, verify
that its commit matches the manifest and attach missing verified assets manually;
do not move the tag or overwrite different bytes. If a new package is required,
prepare and verify a new version. Preserve the candidate manifest and original
distribution hashes.

After a successful publish, remove `autorelease: pending` from the merged release
PR and apply `autorelease: tagged` so PR-only Release Please can prepare the next
release. These labels normally move during Release Please's tagging operation,
which this repository delegates to the evidence-gated publish workflow.
