#!/usr/bin/env bash
set -euo pipefail

subject=''
IFS= read -r subject < "$1" || true
pattern='^(feat|fix|perf|docs|refactor|test|build|ci|chore|revert|style|deps)(\([a-zA-Z0-9_./-]+\))?!?: [^[:space:]].*$'

if [[ ! "$subject" =~ $pattern ]]; then
    echo 'Commit rejected: use type(scope): description (scope is optional).' >&2
    echo 'Examples: feat(workflows): add retries; fix: handle expired leases' >&2
    echo 'Types: feat, fix, perf, docs, refactor, test, build, ci, chore, revert, style, deps' >&2
    exit 1
fi
