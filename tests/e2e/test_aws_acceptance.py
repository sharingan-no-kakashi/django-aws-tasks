"""Required disposable AWS cases, including explicit non-baseline batch fixtures."""

from example_tools.aws_acceptance import (
    initialization_outage,
    partial_batch,
    remote_acceptance_loss,
    terminal_replay,
    terminated_batch,
)


def test_initialization_dlq_delivery_hold_and_audited_release(deployment):
    initialization_outage(deployment)


def test_terminal_cli_replay_is_idempotent_and_conflicts(deployment):
    terminal_replay(deployment)


def test_remote_provider_acceptance_with_lost_response(deployment):
    remote_acceptance_loss(deployment)


def test_multi_record_partial_failure(deployment):
    partial_batch(deployment)


def test_process_death_after_first_batch_record(deployment):
    terminated_batch(deployment)


def test_old_queued_and_held_work_survives_additive_rollout(deployment):
    from example_tools.aws_acceptance import compatible_rollout

    compatible_rollout(deployment)


def test_saved_log_metadata_resolves_to_actual_invocation(deployment):
    from example_tools.aws_acceptance import deployed_logs

    deployed_logs(deployment)
