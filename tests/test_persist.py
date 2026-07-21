"""Slice 6 — the mind persists (docs/tasks.md T10, spec §20-23).

The pool (snippets) and the kainome (wiki) live on disk as JSON and survive restarts; loading is
honest (corrupt files raise, they never silently start empty).
"""
import json

import pytest

from kaineros.cli import Session
from kaineros.compiler import Compiler
from kaineros.fakes import FakeJudge
from kaineros.persist import load_store, save_store
from kaineros.schema import Candidate, Provenance
from kaineros.store import Store


def cand(gene: str, content: str, score: float = 0.0, turn: str = "t1") -> Candidate:
    return Candidate(
        gene=gene,
        content=content,
        provenance=Provenance(
            source_turn_ids=(turn,), created_at=score, confidence="high", stakes="high"
        ),
    )


def test_store_round_trips_exactly(tmp_path):
    store = Store()
    comp = Compiler(store, FakeJudge(key=lambda c: c.provenance.created_at), promote_after=1)
    comp.insert(cand("home", "i live in berlin", score=5, turn="t9"))
    comp.insert(cand("home", "i live in london", score=1, turn="t2"))
    comp.insert(cand("food", "i love mangoes", score=3))
    comp.housekeep()  # promotes both tops; pages get rank_history

    save_store(store, tmp_path)
    loaded = load_store(tmp_path)

    assert loaded.genes() == store.genes()
    for gene in store.genes():
        got, want = loaded.candidates(gene), store.candidates(gene)
        assert [c.content for c in got] == [c.content for c in want]  # rank order preserved
        assert [c.wins for c in got] == [c.wins for c in want]
        assert [c.id for c in got] == [c.id for c in want]
        assert [c.provenance for c in got] == [c.provenance for c in want]
    for gene, page in store.clean.items():
        got = loaded.page(gene)
        assert got is not None
        assert got.content == page.content
        assert got.provenance == page.provenance
        assert got.rank_history == page.rank_history


def test_two_sessions_over_one_dir_share_a_mind(tmp_path):
    first = Session(store_dir=tmp_path)
    first.turn("bananas are yellow")
    first.turn("moon orbits earth")
    first.turn("tea is hot")  # third pass -> "bananas" promoted and saved

    second = Session(store_dir=tmp_path)  # a fresh start, same mind
    resp = second.turn("what about bananas")
    assert resp.abstained is False
    assert "bananas are yellow" in resp.answer


def test_each_page_is_its_own_file_and_retired_files_disappear(tmp_path):
    store = Store()
    comp = Compiler(store, FakeJudge(), promote_after=1)
    comp.insert(cand("user.home_city", "the user lives in berlin"))
    comp.insert(cand("user.food.loves", "the user loves mangoes"))
    comp.housekeep()
    save_store(store, tmp_path)
    assert (tmp_path / "kainome" / "user.home_city.json").exists()
    assert (tmp_path / "kainome" / "user.food.loves.json").exists()

    del store.clean["user.food.loves"]  # page retired (e.g. by fission)
    save_store(store, tmp_path)
    assert (tmp_path / "kainome" / "user.home_city.json").exists()
    assert not (tmp_path / "kainome" / "user.food.loves.json").exists()


def test_page_files_are_readable_json(tmp_path):
    store = Store()
    comp = Compiler(store, FakeJudge(), promote_after=1)
    comp.insert(cand("user.home_city", "the user lives in berlin"))
    comp.housekeep()
    save_store(store, tmp_path)
    doc = json.loads((tmp_path / "kainome" / "user.home_city.json").read_text(encoding="utf-8"))
    assert doc["gene"] == "user.home_city"
    assert doc["content"] == "the user lives in berlin"
    assert doc["provenance"]["confidence"] == "high"
    assert doc["schema_version"] == 1


def test_corrupt_pool_raises_instead_of_starting_empty(tmp_path):
    (tmp_path / "pool.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(RuntimeError, match="corrupt"):
        load_store(tmp_path)


def test_missing_home_is_a_fresh_mind(tmp_path):
    store = load_store(tmp_path / "nowhere")
    assert store.genes() == []
    assert store.pages() == []


def test_wipe_erases_the_persisted_mind(tmp_path):
    s = Session(store_dir=tmp_path)
    s.turn("bananas are yellow")
    assert (tmp_path / "pool.json").exists()
    s.wipe()
    assert not (tmp_path / "pool.json").exists()
    assert load_store(tmp_path).genes() == []
