========
Releases
========

This project is a personal prototype associated with a blog post. Do not use
it as a production system. Version numbers identify snapshots of the prototype.

Release notes
=============

See the :download:`changelog <../CHANGELOG.md>`
and `GitHub Releases
<https://github.com/sharingan-no-kakashi/django-aws-tasks/releases>`_
for changes, tags, and artifacts.

Compatibility and upgrades
==========================

The version boundaries and installation requirements are in :doc:`installation`
and :doc:`database-backends`. The current schema supports fresh installations;
there is no upgrade path from earlier development schemas.

Keep producer and worker task modules compatible during version changes. Apply
migrations before enabling code that requires them, preserve import paths for
queued work, and review held tasks before resuming execution. See
:doc:`operations` for migration and recovery guidance.

Maintainer procedure
====================

Release Please prepares version and changelog pull requests from Conventional
Commit titles. The repository's ``docs/release.md`` describes candidate
verification, artifact identity, and the separate publication workflow.
``docs/github-maintenance.md`` describes repository and publishing configuration.

Use :doc:`testing` for development checks and recorded results. Keep release
notes focused on API changes, compatibility, migrations, and operator actions.
