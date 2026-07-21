"""Slice 9 — the mind asks (spec §36-39): the deep brain queues disambiguation questions,
the fast brain asks them, the answer flows back through the normal pipeline."""
from kaineros.cli import Session
from kaineros.compiler import Compiler
from kaineros.fakes import FakeJudge
from kaineros.persist import load_store, save_store
from kaineros.schema import Candidate, Provenance
from kaineros.store import Store


def cand(gene, content):
    return Candidate(gene=gene, content=content, provenance=Provenance(stated=True, confidence="high"))


def job_conflict(a, b):
    # backend-vs-platform: one says backend, the other says platform+"not backend"
    texts = {a.content.lower(), b.content.lower()}
    return any("backend" in t for t in texts) and any("platform" in t for t in texts)


def make_compiler():
    store = Store()
    return store, Compiler(store, FakeJudge(conflict_pred=job_conflict), promote_after=1)


def test_conflicting_pages_queue_a_grounded_question():
    store, comp = make_compiler()
    comp.insert(cand("user.job.title", "works as a backend engineer"))
    comp.insert(cand("user.job.team", "now on the platform team, not backend"))
    comp.housekeep()

    q = store.pending_questions()
    assert len(q) == 1
    assert comp.last_report.queued == 1
    # grounded: the question is built from BOTH conflicting pages' own words
    assert "backend engineer" in q[0].text
    assert "platform team" in q[0].text
    assert set(q[0].genes) == {"user.job.title", "user.job.team"}
    # both pages keep serving until the user resolves it — non-destructive
    assert store.page("user.job.title") is not None
    assert store.page("user.job.team") is not None


def test_question_is_not_queued_twice():
    store, comp = make_compiler()
    comp.insert(cand("user.job.title", "works as a backend engineer"))
    comp.insert(cand("user.job.team", "now on the platform team, not backend"))
    comp.housekeep()
    comp.housekeep()  # the conflict persists, but the question must not be re-queued
    assert len(store.pending_questions()) == 1


def test_non_conflicting_pages_ask_nothing():
    store, comp = make_compiler()
    comp.insert(cand("user.food.loves", "loves mangoes"))
    comp.insert(cand("user.food.allergy", "allergic to peanuts"))
    comp.housekeep()
    assert store.pending_questions() == []


def test_fast_brain_asks_then_the_answer_marks_it_answered():
    s = Session(judge=FakeJudge(conflict_pred=job_conflict))
    s.turn("i work as a backend engineer")
    s.turn("actually i'm on the platform team now, not backend")
    # a conflict now sits between the two promoted pages -> a question is queued
    assert s.store.pending_questions()

    asked = s.take_question()          # the fast brain surfaces it
    assert asked is not None and asked.status == "asked"
    assert s.store.pending_questions() == []  # no longer pending, it's been asked

    s.turn("platform team, the backend title is out of date")  # the user answers
    assert asked.status == "answered"  # the reply closed it (spec §39)


def test_questions_persist(tmp_path):
    store, comp = make_compiler()
    comp.insert(cand("user.job.title", "works as a backend engineer"))
    comp.insert(cand("user.job.team", "now on the platform team, not backend"))
    comp.housekeep()
    save_store(store, tmp_path)

    loaded = load_store(tmp_path)
    assert len(loaded.pending_questions()) == 1
    assert "backend engineer" in loaded.questions[0].text
