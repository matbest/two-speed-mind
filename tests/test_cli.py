"""Slice 3 — the chat shell (docs/tasks.md T7).

Drives scripted transcripts through Session (the REPL's engine) and through main() itself.
"""
from kaineus.cli import Session, main
from kaineus.fakes import FakeSlowModel
from kaineus.schema import Candidate, Turn


class CountingSlowModel(FakeSlowModel):
    """Records every turn it is asked to extract — pins spec §17 (exactly-once)."""

    def __init__(self) -> None:
        self.seen_turn_ids: list[str] = []

    def extract(self, turns: list[Turn]) -> list[Candidate]:
        self.seen_turn_ids.extend(t.id for t in turns)
        return super().extract(turns)


def test_conversation_end_to_end():
    s = Session()
    s.turn("bananas are yellow")
    s.turn("moon orbits earth")
    s.turn("tea is hot")  # third housekeep pass: "bananas" has held #1 three times
    assert s.store.page("bananas") is not None

    resp = s.turn("what about bananas")
    assert resp.abstained is False
    assert "bananas are yellow" in resp.answer   # words came via the fake model + page
    assert "bananas" in resp.why                 # reason grounded in the retrieved gene


def test_turns_are_extracted_exactly_once():
    slow = CountingSlowModel()
    s = Session(slow=slow)
    for text in ["bananas are yellow", "moon orbits earth", "tea is hot"]:
        s.turn(text)
    ids = slow.seen_turn_ids
    assert len(ids) == 3
    assert len(set(ids)) == 3  # no turn was ever re-extracted


def test_abstains_before_anything_is_promoted():
    s = Session()
    resp = s.turn("what do I like?")
    assert resp.abstained is True


def test_forget_resets_buffer_and_marker():
    s = Session()
    s.turn("bananas are yellow")
    s.forget()
    assert s.buffer == []
    assert s.compiled_upto == 0


def test_repl_smoke(monkeypatch, capsys):
    lines = iter([
        "bananas are yellow",
        "moon orbits earth",
        "tea is hot",
        "what about bananas",
        "/notebook",
        "/why",
        "/forget",
        "/quit",
    ])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(lines))

    assert main(["--plain"]) == 0

    out = capsys.readouterr().out
    assert "From what I know: bananas are yellow" in out  # the answer
    assert "[bananas] bananas are yellow" in out          # /notebook shows the promoted page
    assert "page 'bananas'" in out                        # /why is grounded in state
    assert "(buffer cleared)" in out                      # /forget
    assert "bye." in out
