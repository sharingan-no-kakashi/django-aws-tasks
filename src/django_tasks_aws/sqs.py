"""SQS message delivery with a reusable client and bounded retries.

Only connection failures, throttling, and temporary service errors are retried.
The durable task state and sweeper handle failures after retries are exhausted.
"""

from typing import Any, Protocol

import boto3
from botocore.config import Config
from botocore.exceptions import (
    ClientError,
    ConnectionError,
    ConnectTimeoutError,
    ReadTimeoutError,
)
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_random_exponential

_CONNECTION_ERRORS = (ConnectionError, ConnectTimeoutError, ReadTimeoutError)
_RETRYABLE_ERROR_CODES = {
    "ThrottlingException",
    "Throttling",
    "RequestThrottled",
    "RequestLimitExceeded",
    "ServiceUnavailable",
    "InternalError",
}


def is_retryable_sqs_error(error: BaseException) -> bool:
    if isinstance(error, _CONNECTION_ERRORS):
        return True
    if not isinstance(error, ClientError):
        return False
    error_code = error.response.get("Error", {}).get("Code")
    return error_code in _RETRYABLE_ERROR_CODES


class SqsTransport(Protocol):
    """Message sender used by dispatch and reconciliation."""

    def send_message(self, queue_url: str, body: str) -> str: ...


class BotoSqsTransport:
    """A boto3-backed transport that creates its client lazily."""

    def __init__(self, *, region_name: str | None = None, endpoint_url: str | None = None) -> None:
        self._region_name = region_name
        self._endpoint_url = endpoint_url
        self._client: Any = None

    @retry(
        retry=retry_if_exception(is_retryable_sqs_error),
        stop=stop_after_attempt(3),
        wait=wait_random_exponential(multiplier=0.5, max=2),
        reraise=True,
    )
    def send_message(self, queue_url: str, body: str) -> str:
        """Send a message and return its SQS message ID."""
        response = self.get_client().send_message(QueueUrl=queue_url, MessageBody=body)
        return response["MessageId"]

    def get_client(self) -> Any:
        if self._client is None:
            self._client = self.create_client()
        return self._client

    def create_client(self) -> Any:
        # Tenacity owns retries so botocore cannot multiply the attempt limit.
        config = Config(
            connect_timeout=1,
            read_timeout=2,
            retries={"mode": "standard", "total_max_attempts": 1},
        )
        return boto3.client(
            "sqs",
            region_name=self._region_name,
            endpoint_url=self._endpoint_url,
            config=config,
        )
