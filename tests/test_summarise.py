"""Summarise mode (spec §50): consolidate a fragmented cluster of same-tag pages into one dense
page, so retrieval isn't diluted across many narrow genes."""
from __future__ import annotations

from kaineros.compiler import Compiler
from kaineros.fakes import FakeJudge, FakeSlowModel
from kaineros.schema import Candidate, Provenance
from kaineros.store import Store


def _promote(comp: Compiler, gene: str, content: str, tags: tuple[str, ...]) -> None:
    comp.insert(Candidate(gene=gene, content=content, gist=content,
                          provenance=Provenance(stated=True), tags=tags))


def _build() -> Compiler:
    comp = Compiler(Store(), FakeJudge(), promote_after=1)
    comp.summariser = FakeSlowModel()  # deterministic: joins the facts
    comp.summarise_enabled = True  # off by default now (see compiler); these tests exercise it
    comp.summarise_min, comp.summarise_max = 4, 10
    return comp


def test_a_fragmented_cluster_consolidates():
    comp = _build()
    # five narrow pages all about music production (share the 'production' tag)
    for i, (g, c) in enumerate([
        ("user.hobby.music_producer", "digital remix producer"),
        ("user.skill.daw", "uses DAW software"),
        ("user.music.production_experience", "found formal production a chore"),
        ("user.music_production.start_year", "started in 2010"),
        ("user.music.remix_status", "on hiatus from remixing"),
    ]):
        _promote(comp, g, c, ("music", "production"))
    # an unrelated page that must NOT be swept in
    _promote(comp, "user.pet.species", "a greyhound", ("pet", "dog"))
    comp.housekeep(cleanup=True)

    pages = comp.store.pages()
    prod = [p for p in pages if "production" in p.tags]
    assert len(prod) == 1                                  # the five fragments became one page
    assert "digital remix producer" in prod[0].content     # details preserved (fake joins them)
    assert "uses DAW software" in prod[0].content
    assert set(("music", "production")) <= set(prod[0].tags)  # keeps the cluster's tags for routing
    assert any(p.gene == "user.pet.species" for p in pages)  # the unrelated page is untouched


def test_no_summariser_means_no_consolidation():
    comp = Compiler(Store(), FakeJudge(), promote_after=1)  # summariser stays None
    for i in range(5):
        _promote(comp, f"user.music.fact{i}", f"music fact {i}", ("music", "production"))
    comp.housekeep(cleanup=True)
    assert len(comp.store.pages()) == 5  # nothing consolidated without a summariser


def test_summarise_is_off_by_default_even_with_a_summariser():
    # a summariser can be wired in, but consolidation only runs when explicitly enabled — the
    # default is OFF (it merged distinct facts on PersonaMem and cost recall)
    comp = Compiler(Store(), FakeJudge(), promote_after=1)
    comp.summariser = FakeSlowModel()  # wired, but summarise_enabled stays False
    assert comp.summarise_enabled is False
    for i in range(5):
        _promote(comp, f"user.music.fact{i}", f"music fact {i}", ("music", "production"))
    comp.housekeep(cleanup=True)
    assert len(comp.store.pages()) == 5  # gated off: nothing consolidated


def test_a_broad_topic_is_not_over_consolidated():
    comp = _build()
    comp.summarise_max = 6
    # 8 pages share the broad 'music' tag — too many to be one aspect, so leave them
    for i in range(8):
        _promote(comp, f"user.music.thing{i}", f"music thing {i}", ("music",))
    comp.housekeep(cleanup=True)
    assert len(comp.store.pages()) == 8  # over-max cluster is left alone
