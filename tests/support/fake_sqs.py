"""Injectable SQS transport failures for publication tests.

Models the two distinct, hard-to-reproduce failure modes the publisher must
survive: a send that is rejected before acceptance, and a send that is accepted
but whose response is lost.
"""

from dataclasses import dataclass, field


class SendMessageFailure(Exception):
    """The send was rejected and never reached the queue."""


class ResponseLost(Exception):
    """The send was accepted but its response never arrived."""


@dataclass
class FakeSqsTransport:
    queues: list[str] = field(default_factory=list)
    sent: list[str] = field(default_factory=list)
    fail_next_sends: int = 0
    lose_response_once: bool = False

    def send_message(self, queue_url: str, body: str) -> str:
        if self.fail_next_sends > 0:
            self.fail_next_sends -= 1
            raise SendMessageFailure("injected send failure")
        self.queues.append(queue_url)
        self.sent.append(body)
        if self.lose_response_once:
            self.lose_response_once = False
            raise ResponseLost("accepted but response lost")
        return body
