"""Update-signal detection (spec §42): a deliberate update wins fast; a stray still has to fight.

This resolves the static/dynamic tension the hard corpus exposed — 'recency is a vote not a veto'
still defends against stray contradictions, but an explicit correction (supersedes=True) displaces
an entrenched incumbent immediately.
"""
from kaineros.compiler import Compiler
from kaineros.fakes import FakeJudge
from kaineros.schema import Candidate, Provenance
from kaineros.store import Store


def entrenched(gene, content, score=0.0):
    # a plain, well-established fact (no update signal)
    return Candidate(gene=gene, content=content, provenance=Provenance(created_at=score, stated=True))


def update(gene, content):
    return Candidate(gene=gene, content=content, provenance=Provenance(stated=True, supersedes=True))


def test_deliberate_update_displaces_an_entrenched_incumbent():
    store = Store()
    comp = Compiler(store, FakeJudge(), promote_after=3)
    for i in range(3):  # Acme, stated three times, well-entrenched
        comp.insert(entrenched("job", "acme corp", score=i))
    for _ in range(3):
        comp.housekeep()
    assert store.page("job").content == "acme corp"

    comp.insert(update("job", "initech"))  # "as of today I work at Initech, not Acme"
    comp.housekeep()                        # ONE pass — the update wins fast
    assert store.page("job").content == "initech"  # entrenched incumbent displaced


def test_a_stray_contradiction_still_has_to_fight():
    # supersedes=False -> the static defence holds: a lone stray can't overwrite a held fact
    store = Store()
    comp = Compiler(store, FakeJudge(key=lambda c: c.provenance.created_at), promote_after=3)
    comp.insert(entrenched("home", "boston", score=5))  # established, high rank
    for _ in range(3):
        comp.housekeep()
    comp.insert(entrenched("home", "chicago", score=1))  # a stray, older, NOT an update
    comp.housekeep()
    assert store.page("home").content == "boston"  # stray did not jump the queue


def test_update_jumps_to_the_top_on_insert():
    store = Store()
    comp = Compiler(store, FakeJudge(key=lambda c: c.provenance.created_at))
    comp.insert(entrenched("job", "acme", score=9))          # strong incumbent
    comp.insert(entrenched("job", "stray", score=1))          # stray
    comp.housekeep()                                         # grooming ranks acme back on top
    assert store.candidates("job")[0].content == "acme"
    comp.insert(update("job", "initech"))                    # a deliberate update -> #1 at ingest
    assert store.candidates("job")[0].content == "initech"
    comp.housekeep()                                         # pinned: grooming keeps it on top
    assert store.candidates("job")[0].content == "initech"
