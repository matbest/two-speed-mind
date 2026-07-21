"""Slice 4.5 — gene identity: dedup (T8.4), fission (T8.5), fusion (T8.6).

A gene means one claim; its candidates are distinct accounts. The slow brain maintains that
partition in housekeeping: restatements merge, crowded mixed pools split, duplicate promoted
pages fuse. Spec §7-§11.
"""
from twospeed.compiler import Compiler
from twospeed.fakes import FakeJudge
from twospeed.schema import Candidate, Provenance
from twospeed.store import Store


def cand(gene: str, content: str, score: float = 0.0, turn: str = "") -> Candidate:
    return Candidate(
        gene=gene,
        content=content,
        provenance=Provenance(
            source_turn_ids=(turn,) if turn else (), created_at=score
        ),
    )


def by_score() -> FakeJudge:
    return FakeJudge(key=lambda c: c.provenance.created_at)


def first_word_claims(**kw) -> FakeJudge:
    """Claim identity = first word of the content (so tests can mix claims in one gene)."""
    return FakeJudge(claim_of=lambda c: c.content.split()[0], **kw)


# -- T8.4 dedup ---------------------------------------------------------------------------------


def test_restatements_coexist_until_housekeep_then_merge():
    store = Store()
    comp = Compiler(store, FakeJudge())  # account identity: exact content
    comp.insert(cand("food", "i love bananas", turn="t1"))
    comp.insert(cand("food", "i love bananas", score=2, turn="t2"))
    assert len(store.candidates("food")) == 2  # insert never checks identity (spec §11)

    comp.housekeep()
    pool = store.candidates("food")
    assert len(pool) == 1  # merged in cleanup
    assert set(pool[0].provenance.source_turn_ids) == {"t1", "t2"}  # receipts accumulate
    assert pool[0].provenance.created_at == 2  # recency refreshes to the latest statement
    assert comp.last_report.merged == 1


def test_distinct_accounts_survive_cleanup():
    store = Store()
    comp = Compiler(store, FakeJudge())
    comp.insert(cand("food", "i love bananas"))
    comp.insert(cand("food", "bananas are yellow"))
    comp.housekeep()
    assert len(store.candidates("food")) == 2
    assert comp.last_report.merged == 0


def test_duplicates_never_masquerade_as_crowding():
    store = Store()
    comp = Compiler(store, FakeJudge(), split_after=3)
    for i in range(5):  # same account restated past split_after
        comp.insert(cand("food", "i love bananas", turn=f"t{i}"))
    comp.housekeep()
    assert len(store.candidates("food")) == 1  # dedup ran before the crowding check
    assert comp.last_report.merged == 4
    assert comp.last_report.split == 0
    assert store.genes() == ["food"]  # no spurious fission


# -- T8.5 fission -------------------------------------------------------------------------------


def test_mixed_pool_splits_into_two_claims():
    store = Store()
    comp = Compiler(store, first_word_claims(), promote_after=99, split_after=3)
    for content in ["apple tastes good", "apple is red", "banana is long", "banana is yellow"]:
        comp.insert(cand("food", content))
    comp.housekeep()

    assert comp.last_report.split == 1
    assert set(store.genes()) == {"food", "food.2"}
    for gene in store.genes():
        claims = {c.content.split()[0] for c in store.candidates(gene)}
        assert len(claims) == 1  # each child pool argues about one claim


def test_large_but_pure_pool_does_not_split():
    store = Store()
    comp = Compiler(store, first_word_claims(), promote_after=99, split_after=3)
    for i in range(5):
        comp.insert(cand("food", f"apple variant number {i}"))
    comp.housekeep()
    assert comp.last_report.split == 0
    assert store.genes() == ["food"]


def test_wins_and_pages_do_not_survive_a_split():
    store = Store()
    comp = Compiler(store, first_word_claims(), promote_after=2, split_after=3)
    comp.insert(cand("food", "apple tastes good"))
    comp.housekeep()
    comp.housekeep()  # two passes at #1 -> promoted
    assert store.page("food") is not None

    for content in ["apple is red", "banana is long", "banana is yellow", "banana is big"]:
        comp.insert(cand("food", content))
    comp.housekeep()  # crowded and mixed -> split

    assert comp.last_report.split == 1
    assert store.page("food") is None  # the page was retired
    assert store.page("food.2") is None
    for gene in ("food", "food.2"):  # streaks reset; this pass's win is the most anyone has
        assert all(c.wins <= 1 for c in store.candidates(gene))


def test_three_claim_pool_converges_over_passes():
    store = Store()
    comp = Compiler(store, first_word_claims(), promote_after=99, split_after=2)
    contents = [
        "apple one", "apple two",
        "banana one", "banana two",
        "cherry one", "cherry two",
    ]
    for content in contents:
        comp.insert(cand("food", content))

    comp.housekeep()  # first split peels one claim off
    comp.housekeep()  # the still-mixed child splits on the next pass
    genes = store.genes()
    assert len(genes) == 3
    for gene in genes:
        claims = {c.content.split()[0] for c in store.candidates(gene)}
        assert len(claims) == 1
        assert len(store.candidates(gene)) == 2


# -- T8.6 fusion --------------------------------------------------------------------------------


def test_duplicate_promoted_pages_fuse_into_the_older_gene():
    store = Store()
    comp = Compiler(store, first_word_claims(), promote_after=1)
    comp.insert(cand("home", "berlin is where i live"))
    comp.housekeep()  # "home" promoted first — it is the older key
    comp.insert(cand("city", "berlin flat is mine"))
    comp.housekeep()  # "city" promoted this pass
    assert store.page("home") is not None and store.page("city") is not None

    comp.housekeep()  # both pages state the "berlin" claim -> fuse
    assert comp.last_report.fused == 1
    assert "city" not in store.genes()
    assert store.page("city") is None
    assert len(store.candidates("home")) == 2  # the accounts now compete in one pool


def test_unrelated_pages_do_not_fuse():
    store = Store()
    comp = Compiler(store, first_word_claims(), promote_after=1)
    comp.insert(cand("home", "berlin is where i live"))
    comp.insert(cand("food", "apple tastes good"))
    for _ in range(3):
        comp.housekeep()
    assert comp.last_report.fused == 0
    assert store.page("home") is not None and store.page("food") is not None


def test_fusion_only_reads_the_clean_layer():
    store = Store()
    comp = Compiler(store, first_word_claims(), promote_after=99)  # nothing ever promotes
    comp.insert(cand("home", "berlin is where i live"))
    comp.insert(cand("city", "berlin flat is mine"))
    comp.housekeep()
    assert comp.last_report.fused == 0  # duplicates below the promotion line go unnoticed (v1)
    assert set(store.genes()) == {"home", "city"}
