# Issue migration staging

GitHub returned `401: Bad credentials` when the documentation cleanup attempted
to read existing issues. These entries are staging material for issue creation,
not a second active backlog. After authenticating with `gh auth login`, review
existing issues for duplicates, create the applicable entries from `issues.json`,
and replace this directory with links to the created issues.

Entries consolidate the remaining work explicitly identified in the archived
23 October verification record. Historical checkbox lists also contain old
implementation plans and optional feature proposals; do not reopen delivered
work solely because an old box is unchecked. Reconcile scope with current code
and existing issues before assigning work.
