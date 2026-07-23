"""Deep-brain modes (spec §41): rank every pass, cross-page cleanup only on cadence / on demand."""
from kaineros.cli import Session
from kaineros.compiler import Compiler
from kaineros.fakes import FakeJudge
from kaineros.schema import Candidate, Provenance
from kaineros.store import Store


def cand(gene, content):
    return Candidate(gene=gene, content=content, provenance=Provenance(stated=True, confidence="high"))


def first_word_claims(**kw):
    return FakeJudge(claim_of=lambda c: c.content.split()[0], **kw)


def test_rank_mode_promotes_but_skips_cross_page_work():
    store = Store()
    comp = Compiler(store, first_word_claims(), promote_after=1)
    comp.insert(cand("home", "berlin is where i live"))
    comp.insert(cand("city", "berlin flat is mine"))
    comp.housekeep(cleanup=False)  # rank only
    # both promoted (rank + promotion happen), but the duplicate pages are NOT fused (no cleanup)
    assert store.page("home") is not None and store.page("city") is not None
    assert comp.last_report.fused == 0


def test_cleanup_mode_does_the_cross_page_fusion():
    store = Store()
    comp = Compiler(store, first_word_claims(), promote_after=1)
    comp.insert(cand("home", "berlin is where i live"))
    comp.housekeep()
    comp.insert(cand("city", "berlin flat is mine"))
    # "city" is promoted this pass, so the eager frontier pairs it with the existing "home"
    # (both state the "berlin" claim) and fuses at once (spec §47 — no waiting for a later sweep)
    comp.housekeep()
    assert comp.last_report.fused == 1
    assert (store.page("home") is None) != (store.page("city") is None)  # one survivor remains


def test_session_runs_cleanup_only_on_the_cadence():
    # a conflict that only cross-page cleanup catches; cleanup_every=3 -> caught on the 3rd pass
    def job_conflict(a, b):
        t = {a.content.lower(), b.content.lower()}
        return any("backend" in x for x in t) and any("platform" in x for x in t)

    s = Session(judge=FakeJudge(conflict_pred=job_conflict), cleanup_every=3)
    s.turn("i work as a backend engineer")          # pass 1 - rank only
    s.turn("actually i'm on the platform team now")  # pass 2 - rank only
    assert s.store.pending_questions() == []         # no cross-page check yet
    s.turn("still on the platform team")             # pass 3 - cleanup runs
    assert s.store.pending_questions()               # conflict now recognised


def test_force_cleanup_on_demand():
    def job_conflict(a, b):
        t = {a.content.lower(), b.content.lower()}
        return any("backend" in x for x in t) and any("platform" in x for x in t)

    s = Session(judge=FakeJudge(conflict_pred=job_conflict), cleanup_every=99)  # never on cadence
    s.turn("i work as a backend engineer")
    s.turn("actually i'm on the platform team now")
    assert s.store.pending_questions() == []
    s.cleanup()                                      # force it
    assert s.store.pending_questions()
