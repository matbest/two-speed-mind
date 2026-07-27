"""T23 / spec §51: arc synthesis — the deep brain dreams the story.

The contract, written before the code. The single-model baseline proved a compiled memory loses to
raw context on the NARRATIVE question types (reason-behind-update, preference-evolution) because we
store flat, latest-value facts and lose the arc. Arc synthesis is the fix: during grooming (its
"sleep"), the deep brain consolidates a thread with temporal structure — a `supersedes` chain on one
gene — into ONE narrative page ("initially X → now Y, because Z"), layered BESIDE the facts (not
replacing them, the anti-summarise), grounded in real turns and never confabulating a reason.

The narrator is a model, so the fakes here stand in for it — an honest one and a lying one — to prove
the GROUNDING guard lives in the code, not the model's goodwill.
"""
from __future__ import annotations

from kaineros.compiler import Compiler
from kaineros.fakes import FakeFastModel, FakeJudge
from kaineros.runtime import Runtime
from kaineros.schema import ArcDraft, Candidate, Provenance
from kaineros.store import Store


class _ArcModel:
    """An honest narrator (stands in for the deep model): it orders the beats it is handed and
    invents nothing. Facts arrive oldest-first."""

    def __init__(self) -> None:
        self.calls = 0

    def arc(self, facts: list[str]) -> ArcDraft:
        self.calls += 1
        return ArcDraft(gist=f"initially {facts[0]} -> now {facts[-1]}", beats=list(facts))


class _ConfabModel:
    """A DISHONEST narrator: it appends a 'because …' beat that no source fact supports. The
    grounding guard — in the compiler, not the model — must drop it before the arc is promoted."""

    def arc(self, facts: list[str]) -> ArcDraft:
        return ArcDraft(
            gist=f"initially {facts[0]} -> now {facts[-1]}",
            beats=list(facts) + ["because a spiteful rival mocked them"],  # ungrounded invention
        )


class _EmptyArcModel:
    """A narrator that returns nothing usable (bad JSON, a refusal). The empty-arc guard must refuse
    to promote a story with no grounded beats, rather than pollute the wiki with a blank page."""

    def arc(self, facts: list[str]) -> ArcDraft:
        return ArcDraft(gist="", beats=[])


def _comp(arc_model=None) -> Compiler:
    comp = Compiler(Store(), FakeJudge(), promote_after=1)
    comp.arc_model = arc_model or _ArcModel()  # the deep model available while grooming
    comp.arc_enabled = True                    # off by default (like summarise); tests opt in
    return comp


def _add(comp: Compiler, content: str, turn: str, when: int, *, supersedes: bool = False) -> None:
    comp.insert(Candidate(
        gene="user.music_theory", content=content, tags=("music", "theory"),
        provenance=Provenance(source_turn_ids=(turn,), source_texts=(content,),
                              created_at=float(when), supersedes=supersedes),
    ))
    comp.housekeep(cleanup=True)


def _evolving_thread(comp: Compiler) -> None:
    """A supersedes chain: disliked → now loves. States WHAT changed, never WHY."""
    _add(comp, "found music theory a dry chore", turn="t1", when=1)
    _add(comp, "now loves music theory", turn="t2", when=2, supersedes=True)


def _fact(comp: Compiler, gene: str, content: str, turn: str, when: int,
          tags: tuple[str, ...]) -> None:
    """A single, distinct fact (its OWN gene — no supersedes chain)."""
    comp.insert(Candidate(
        gene=gene, content=content, tags=tags,
        provenance=Provenance(source_turn_ids=(turn,), source_texts=(content,), created_at=float(when)),
    ))
    comp.housekeep(cleanup=True)


def _arcs(comp: Compiler) -> list:
    return [p for p in comp.store.pages() if p.kind == "arc"]


def test_arc_synthesised_from_a_supersedes_chain():
    comp = _comp()
    _evolving_thread(comp)
    arcs = _arcs(comp)
    assert len(arcs) == 1
    arc = arcs[0]
    assert arc.kind == "arc"
    assert "chore" in arc.content and "loves" in arc.content   # both endpoints, in order
    assert "initially" in arc.gist.lower()                     # the arc-shaped gist


def test_arc_is_additive_not_destructive():
    comp = _comp()
    _evolving_thread(comp)
    pages = comp.store.pages()
    # the atomic fact page SURVIVES (contrast summarise §50, which retires its fragments)...
    facts = [p for p in pages if p.gene == "user.music_theory" and p.kind == "fact"]
    assert len(facts) == 1
    assert "loves" in facts[0].content     # still the current value — plain recall is unaffected
    assert _arcs(comp)                      # ...AND an arc sits beside it


def test_arc_is_grounded_no_invented_reason():
    comp = _comp(_ConfabModel())            # the model TRIES to fabricate a motive
    _evolving_thread(comp)                  # ...but the turns never state a reason
    arc = _arcs(comp)[0]
    # the guard dropped the unsupported beat — no invented 'because' reached the page
    assert "rival" not in arc.content and "mocked" not in arc.content
    # and the page is grounded: provenance traces ONLY to the turns that really fed it, and isn't empty
    assert set(arc.provenance.source_turn_ids) <= {"t1", "t2"}
    assert arc.provenance.source_turn_ids


def test_arc_routes_for_a_why_question():
    comp = _comp()
    _evolving_thread(comp)
    rt = Runtime(comp.store, FakeFastModel())
    resp = rt.respond("how did your view of music theory change over time?")
    assert any(p.kind == "arc" for p in resp.used)   # the narrative question reaches the arc page


def test_arc_is_rebuilt_when_the_thread_gains_a_new_fact():
    comp = _comp()
    _evolving_thread(comp)
    assert len(_arcs(comp)) == 1
    _add(comp, "now teaches music theory to beginners", turn="t3", when=3, supersedes=True)
    arcs = _arcs(comp)
    assert len(arcs) == 1                 # the SAME thread's arc is UPDATED, not a second one added
    assert "teaches" in arcs[0].content   # the new beat folded in — a stale arc never outlives facts


def test_arc_from_a_same_tag_cluster_across_time():
    """The multi-gene evolution — where the real PersonaMem threads live. Three DISTINCT genes (no
    supersedes chain, each its own pool) share a tag and are stated at different times: a thread
    that evolved. Arc detection must catch it via the shared tag, not only via a supersedes chain."""
    comp = _comp()
    _fact(comp, "user.music.podcast_start", "started a music podcast in 2018", "t1", 1, ("podcast",))
    _fact(comp, "user.music.podcast_growth", "the podcast gained a following in 2019", "t2", 2, ("podcast",))
    _fact(comp, "user.music.podcast_stop", "stopped the podcast in 2020", "t3", 3, ("podcast",))
    arcs = _arcs(comp)
    assert len(arcs) == 1
    assert "started" in arcs[0].content and "stopped" in arcs[0].content   # narrated across time
    assert set(arcs[0].provenance.source_turn_ids) == {"t1", "t2", "t3"}   # grounded in all three
    # the atomic facts survive (additive) — plain recall of any one is unaffected
    assert sum(1 for p in comp.store.pages() if p.kind == "fact") == 3


def test_empty_arc_is_not_promoted():
    comp = _comp(_EmptyArcModel())
    _evolving_thread(comp)                # a real supersedes chain exists...
    assert _arcs(comp) == []              # ...but nothing groundable came back -> no blank arc page
