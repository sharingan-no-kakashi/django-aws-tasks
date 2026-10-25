"""Observe delivered Terraform resources through real AWS APIs; no mocks satisfy this gate."""

import json

from django_tasks_aws.schemas import load_route_manifest


def test_applied_public_module_matches_live_route_manifest(deployment):
    manifest = load_route_manifest(deployment.configuration["route_manifest"])
    client = deployment.session.client("lambda")
    sqs = deployment.session.client("sqs")
    observations = []
    for name, route in manifest.items():
        function = deployment.configuration["worker_functions"][name]
        configuration = client.get_function_configuration(FunctionName=function)
        attributes = sqs.get_queue_attributes(
            QueueUrl=route.source_queue_url, AttributeNames=["All"]
        )["Attributes"]
        mappings = client.list_event_source_mappings(FunctionName=function)["EventSourceMappings"]
        mapping = next(row for row in mappings if row["EventSourceArn"] == route.source_queue_arn)
        variables = configuration["Environment"]["Variables"]
        assert variables["TASK_WORKER_NAME"] == name
        assert variables["TASK_WORKER_SOURCE_ARN"] == route.source_queue_arn
        assert (
            json.loads(variables["TASK_WORKER_ROUTES"])
            == deployment.configuration["route_manifest"]
        )
        assert configuration["Timeout"] == route.timeout_seconds
        assert configuration["Architectures"] == ["x86_64"]
        assert attributes["QueueArn"] == route.source_queue_arn
        assert int(attributes["VisibilityTimeout"]) == route.visibility_timeout_seconds
        assert (
            int(json.loads(attributes["RedrivePolicy"])["maxReceiveCount"])
            == route.max_receive_count
        )
        assert mapping["BatchSize"] == 1 and mapping["MaximumBatchingWindowInSeconds"] == 0
        assert mapping["FunctionResponseTypes"] == ["ReportBatchItemFailures"]
        assert (
            configuration["VpcConfig"]["SubnetIds"]
            and configuration["VpcConfig"]["SecurityGroupIds"]
        )
        observations.append(
            {
                "function": function,
                "function_arn": configuration["FunctionArn"],
                "source": attributes["QueueArn"],
                "mapping": mapping["UUID"],
            }
        )
    deployment.retain("terraform-live-identities", observations)
