"""The input prompt reflects the deep brain's backlog (thinking vs ready)."""
import threading

from kaineros.cli import Session, _prompt
from kaineros.fakes import FakeSlowModel


class GatedSlow(FakeSlowModel):
    def __init__(self):
        self.gate = threading.Event()

    def extract(self, turns):
        assert self.gate.wait(timeout=5)
        return super().extract(turns)


def test_prompt_is_you_when_idle():
    assert _prompt(Session()) == "you> "  # synchronous session, never a backlog


def test_prompt_shows_thinking_while_the_deep_brain_is_behind():
    slow = GatedSlow()
    s = Session(slow=slow, background=True)
    s.turn("bananas are yellow")          # queued, extractor gated -> backlog 1
    assert _prompt(s) == "thinking (1)> "
    slow.gate.set()
    assert s.flush(timeout=5)
    assert _prompt(s) == "you> "          # caught up
