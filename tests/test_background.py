"""Slice 7 — background consolidation (docs/tasks.md T11, spec §24-27).

The answer never waits for the slow brain; the buffer+marker is the work queue; failure loses
nothing and is visible. Event-driven — gates and flushes, no timing guesses.
"""
import threading

from kaineros.cli import Session
from kaineros.fakes import FakeSlowModel
from kaineros.view import deep_panel


class GatedSlowModel(FakeSlowModel):
    """Extraction blocks until the test opens the gate — a controllable 'slow' deep brain."""

    def __init__(self) -> None:
        self.gate = threading.Event()
        self.calls: list[list[str]] = []

    def extract(self, turns):
        assert self.gate.wait(timeout=5), "test gate never opened"
        self.calls.append([t.id for t in turns])
        return super().extract(turns)


def test_turn_answers_while_extraction_is_still_blocked():
    slow = GatedSlowModel()
    s = Session(slow=slow, background=True)
    resp = s.turn("bananas are yellow")  # returns although the extractor is still blocked
    assert resp.answer == "OK"           # a statement is acknowledged immediately, not waited on
    assert s.backlog() == 1              # the backlog is real; extraction happens off the path
    slow.gate.set()
    assert s.flush(timeout=5)
    assert s.store.page("bananas") is not None  # the worker landed and promoted it


def test_a_question_still_abstains_while_the_mind_is_empty():
    slow = GatedSlowModel()
    slow.gate.set()
    s = Session(slow=slow, background=True)
    resp = s.turn("where do I live?")    # a question with nothing known yet
    assert resp.abstained is True        # honest abstention (spec §13)


def test_every_turn_extracted_exactly_once_across_background_passes():
    slow = GatedSlowModel()
    slow.gate.set()
    s = Session(slow=slow, background=True)
    for text in ["bananas are yellow", "moon orbits earth", "tea is hot"]:
        s.turn(text)
    assert s.flush(timeout=5)
    ids = [i for call in slow.calls for i in call]
    assert len(ids) == len(set(ids)) == 3  # no turn extracted twice, none lost


def test_failing_pass_loses_nothing_then_recovers():
    class FlakySlowModel(FakeSlowModel):
        def __init__(self) -> None:
            self.failures_left = 1

        def extract(self, turns):
            if self.failures_left:
                self.failures_left -= 1
                raise RuntimeError("boom")
            return super().extract(turns)

    s = Session(slow=FlakySlowModel(), background=True)
    s.retry_backoff = 0.05
    s.turn("bananas are yellow")
    assert s.flush(timeout=5)
    assert s.store.page("bananas") is not None       # the marker waited; the retry landed it
    assert s.compiler.last_report.error is None      # cleared by the successful pass


def test_persistent_failure_is_visible_and_parks():
    class DeadSlowModel(FakeSlowModel):
        def extract(self, turns):
            raise RuntimeError("model unreachable")

    s = Session(slow=DeadSlowModel(), background=True)
    s.retry_backoff = 0.05
    s.turn("bananas are yellow")
    assert not s.flush(timeout=1)                    # cannot drain
    assert s.backlog() == 1                          # nothing lost
    assert "model unreachable" in (s.compiler.last_report.error or "")
    assert "FAILED" in deep_panel(s.compiler.last_report, s.store)  # spec §26: visible
