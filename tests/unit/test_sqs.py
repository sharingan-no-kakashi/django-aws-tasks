"""Validate the real boto3 adapter against modeled SQS requests and responses."""

import boto3
import pytest
from botocore.exceptions import ClientError
from botocore.stub import Stubber

from django_tasks_aws.sqs import BotoSqsTransport

QUEUE_URL = "https://sqs.eu-west-1.amazonaws.com/123456789012/short"
BODY = '{"schema_version":1,"task_id":"example"}'
SEND_PARAMETERS = {"QueueUrl": QUEUE_URL, "MessageBody": BODY}


@pytest.fixture
def sqs_transport(monkeypatch):
    client = boto3.client(
        "sqs", region_name="eu-west-1", aws_access_key_id="test", aws_secret_access_key="test"
    )
    monkeypatch.setattr("boto3.client", lambda *args, **kwargs: client)
    transport = BotoSqsTransport(region_name="eu-west-1")
    monkeypatch.setattr(transport.send_message.retry, "sleep", lambda seconds: None)
    with Stubber(client) as stubber:
        yield transport, stubber
        stubber.assert_no_pending_responses()


def test_send_message_returns_aws_message_id(sqs_transport):
    transport, stubber = sqs_transport
    stubber.add_response("send_message", {"MessageId": "accepted"}, SEND_PARAMETERS)
    assert transport.send_message(QUEUE_URL, BODY) == "accepted"


def test_send_message_retries_throttling_then_succeeds(sqs_transport):
    transport, stubber = sqs_transport
    stubber.add_client_error(
        "send_message", service_error_code="RequestThrottled", expected_params=SEND_PARAMETERS
    )
    stubber.add_response("send_message", {"MessageId": "retried"}, SEND_PARAMETERS)
    assert transport.send_message(QUEUE_URL, BODY) == "retried"


def test_send_message_stops_after_three_transient_failures(sqs_transport):
    transport, stubber = sqs_transport
    for _ in range(3):
        stubber.add_client_error(
            "send_message", service_error_code="ServiceUnavailable", expected_params=SEND_PARAMETERS
        )
    with pytest.raises(ClientError, match="ServiceUnavailable"):
        transport.send_message(QUEUE_URL, BODY)


def test_send_message_does_not_retry_access_denied(sqs_transport):
    transport, stubber = sqs_transport
    stubber.add_client_error(
        "send_message", service_error_code="AccessDenied", expected_params=SEND_PARAMETERS
    )
    with pytest.raises(ClientError, match="AccessDenied"):
        transport.send_message(QUEUE_URL, BODY)
