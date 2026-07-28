"""Slice 3 — the chat shell (docs/tasks.md T7).

Drives scripted transcripts through Session (the REPL's engine) and through main() itself.
"""
from kaineros.cli import Session, main
from kaineros.fakes import FakeSlowModel
from kaineros.schema import Candidate, Turn


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


def test_abstains_when_the_mind_is_empty():
    # asked directly (no turn: the fake extractor would file the question itself as an
    # instantly-promoted "fact", which a real extractor skips)
    s = Session()
    resp = s.runtime.respond("what do I like?")
    assert resp.abstained is True


def test_forget_resets_buffer_and_marker():
    s = Session()
    s.turn("bananas are yellow")
    s.forget()
    assert s.buffer == []
    assert s.compiled_upto == 0


def test_repl_smoke(monkeypatch, capsys, tmp_path):
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

    assert main(["--plain", "--mind", str(tmp_path)]) == 0

    out = capsys.readouterr().out
    assert "bananas are yellow" in out  # the answer drew on the bananas page
    assert "[bananas] bananas are yellow" in out          # /notebook shows the promoted page
    assert "page 'bananas'" in out                        # /why is grounded in state
    assert "(buffer cleared" in out                       # /forget
    assert "bye." in out
    assert (tmp_path / "pool.json").exists()              # the mind hit disk (spec §22)
    assert (tmp_path / "kainome" / "bananas.md").exists() # and the wiki rendered


def test_social_reply_matches_greetings_but_never_swallows_a_question():
    from kaineros.cli import _social_reply
    # pure social turns -> a warm canned reply
    assert _social_reply("hi there") == "Hi — what can I help you with?"
    assert _social_reply("hello!") is not None
    assert _social_reply("thanks so much") == "Anytime — that's what I'm here for."
    assert _social_reply("goodnight") == "Talk soon."
    assert _social_reply("what can you do?") is not None
    # a REAL question that merely opens with a greeting word must fall through, not be swallowed
    assert _social_reply("hey what do I like?") is None
    assert _social_reply("what is my home city?") is None
    assert _social_reply("I live in Berlin") is None


def test_greeting_turn_is_warm_zero_tokens_and_unstored():
    s = Session()
    resp = s.turn("hi there")
    assert resp.answer == "Hi — what can I help you with?"   # warm, not a cold "OK"
    assert resp.used == []                                    # no retrieval
    assert len(s.buffer) == 0                                 # nothing stored — "hi" is no fact


def test_a_real_statement_still_acks_and_stores():
    s = Session()  # background=False: synchronous
    resp = s.turn("I live in Berlin")
    assert resp.answer == "OK"                                # fact-statements are unchanged
    assert any("Berlin" in t.text for t in s.buffer)          # and buffered for the deep brain


def test_bare_topics_are_queried_not_filed_as_a_fact():
    from kaineros.cli import _looks_like_statement
    # first-person declaratives are told facts (stored)...
    assert _looks_like_statement("I live in Berlin")
    assert _looks_like_statement("my car is a Tesla")
    assert _looks_like_statement("we moved to London")
    # ...but bare topics and imperatives are queries, not facts
    assert not _looks_like_statement("good morning routine")
    assert not _looks_like_statement("morning routine")
    assert not _looks_like_statement("suggest a hobby for me")

    s = Session()  # empty mind
    assert s.turn("good morning routine").answer != "OK"   # answered (abstains here), not filed "OK"
    assert s.turn("I love hiking").answer == "OK"           # a told fact is still stored + acked
