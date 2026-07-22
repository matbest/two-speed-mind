"""Tags (spec §44-45): the fact's own vocabulary, a child of the fact in the population.

The contract: tags are born at extraction, travel with the fact (dedup unions them, promotion
copies them to the page), route implicit questions in the fast brain, scope the conflicts sweep
in the deep brain — and are hints, never verdicts.
"""
from __future__ import annotations

from kaineros.cli import Session
from kaineros.compiler import Compiler
from kaineros.fakes import FakeFastModel, FakeJudge
from kaineros.persist import load_store, save_store
from kaineros.runtime import Runtime
from kaineros.schema import Candidate, Page, Provenance
from kaineros.store import Store
from tests.test_verdict_cache import TallyJudge


def _cand(gene: str, text: str, tags: tuple[str, ...] = ()) -> Candidate:
    return Candidate(gene=gene, content=text, provenance=Provenance(stated=True), tags=tags)


def test_dedup_unions_tags_like_receipts():
    comp = Compiler(Store(), FakeJudge())
    comp.insert(_cand("car", "Just picked up a new Tesla.", ("car", "ev")))
    comp.insert(_cand("car", "just picked up a NEW tesla", ("vehicle", "car")))  # restatement
    comp.housekeep()
    pool = comp.store.pool["car"]
    assert len(pool) == 1
    assert set(pool[0].tags) == {"car", "ev", "vehicle"}


def test_promotion_carries_tags_onto_the_page():
    comp = Compiler(Store(), FakeJudge())
    comp.insert(_cand("car", "Just picked up a new Tesla.", ("car", "drive")))
    comp.housekeep()
    assert comp.store.clean["car"].tags == ("car", "drive")


def test_tags_route_the_implicit_question():
    """The dynamic-implicit shape: the probe says 'car', the page only ever says 'Tesla'."""
    store = Store()
    prov = Provenance(stated=True, created_at=__import__("time").time())
    # gene deliberately does NOT contain "car" - the probe's word appears nowhere but the tags
    store.clean["user.transport"] = Page(
        gene="user.transport", content="Just picked up a new Tesla.", provenance=prov,
        tags=("car", "vehicle", "drive"),
    )
    rt = Runtime(store, FakeFastModel())
    resp = rt.respond("What car do I drive?", [])
    assert not resp.abstained
    assert [p.gene for p in resp.used] == ["user.transport"]
    # and without tags, the same probe finds nothing - the gap the tags exist to close
    store.clean["user.transport"].tags = ()
    assert Runtime(store, FakeFastModel()).respond("What car do I drive?", []).abstained


def test_disjoint_tags_skip_the_conflicts_check():
    comp = Compiler(Store(), TallyJudge())
    comp.insert(_cand("drink", "Green tea is the favourite.", ("drink", "tea")))
    comp.insert(_cand("pet", "A rescue greyhound called Pixel.", ("pet", "dog")))
    comp.housekeep()  # both tagged, topics disjoint -> no model call for the pair
    assert comp.judge.by_kind.get("conflicts", 0) == 0


def test_disjoint_tags_skip_the_fusion_sweep_too():
    """Fusion's both-ways same_claim sweep was the 287-call bill on sample-big — pages with
    declared, disjoint topics can't be one claim, so no model call."""
    comp = Compiler(Store(), TallyJudge(claim_of=lambda c: c.content))
    # claim_of=content: even the judge would say different-claims; the point is it isn't ASKED.
    # Contents share the word 'enjoys' so the token-overlap shortcut alone can't settle it.
    comp.insert(_cand("drink", "The user enjoys green tea.", ("drink", "tea")))
    comp.insert(_cand("pet", "The user enjoys walking Pixel.", ("pet", "dog")))
    comp.housekeep()
    assert comp.judge.by_kind.get("same_claim", 0) == 0


def test_untagged_pair_still_reaches_the_judge():
    """Conservative rule: scoping only skips when BOTH sides declared their topics."""
    comp = Compiler(Store(), TallyJudge())
    comp.insert(_cand("job", "i work as a backend engineer"))          # no tags (fakes/legacy)
    comp.insert(_cand("team", "i'm on the platform team now"))
    comp.housekeep()
    assert comp.judge.by_kind.get("conflicts", 0) > 0  # zero-overlap collision stays covered


def test_shared_tag_pair_reaches_the_judge():
    comp = Compiler(Store(), TallyJudge())
    comp.insert(_cand("job", "i work as a backend engineer", ("job", "work")))
    comp.insert(_cand("team", "platform team member since spring", ("job", "team")))
    comp.housekeep()
    assert comp.judge.by_kind.get("conflicts", 0) > 0  # shared topic -> the model's call


def test_tags_survive_the_disk_round_trip(tmp_path):
    s = Session(store_dir=str(tmp_path / "mind"))
    s.compiler.insert(_cand("car", "Just picked up a new Tesla.", ("car", "ev")))
    s.compiler.housekeep()
    save_store(s.store, s.store_dir)
    loaded = load_store(s.store_dir)
    assert loaded.pool["car"][0].tags == ("car", "ev")
    assert loaded.clean["car"].tags == ("car", "ev")
    index = (tmp_path / "mind" / "kainome" / "index.md").read_text(encoding="utf-8")
    assert "`car, ev`" in index  # the cue line the router reads
