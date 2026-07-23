"""The gist (spec §48): gene = the question, gist = the bare answer — a terse handle the fast
brain reads without parsing a sentence. Travels with the winner; both gist and sentence kept."""
from __future__ import annotations

from kaineros.cli import Session
from kaineros.cloud import phrase_user
from kaineros.compiler import Compiler
from kaineros.fakes import FakeJudge
from kaineros.persist import load_store, save_store
from kaineros.schema import Candidate, Page, Provenance
from kaineros.store import Store


def _cand(gene: str, text: str, gist: str = "") -> Candidate:
    return Candidate(gene=gene, content=text, provenance=Provenance(stated=True), gist=gist)


def test_promotion_carries_the_gist_onto_the_page():
    comp = Compiler(Store(), FakeJudge())
    comp.insert(_cand("user.food.favorite_cuisine",
                      "The user now prefers Japanese food over Thai.", gist="japanese"))
    comp.housekeep()
    assert comp.store.clean["user.food.favorite_cuisine"].gist == "japanese"


def test_phrase_notes_are_bare_triples_for_the_dumb_model():
    prov = Provenance(stated=True)
    page = Page(gene="user.food.favorite_cuisine",
                content="The user now prefers Japanese food over Thai.",
                provenance=prov, gist="japanese")
    note = phrase_user("What cuisine should you suggest?", [page], [])
    assert "user.food.favorite_cuisine = japanese" in note  # pure key = value, nothing to misread
    # the confusable sentence is NOT fed to the weak fast model (it lives in the wiki for humans)
    assert "prefers Japanese food over Thai" not in note


def test_no_gist_falls_back_to_the_sentence():
    prov = Provenance(stated=True)
    page = Page(gene="user.home", content="The user lives in St Leonards.", provenance=prov)
    note = phrase_user("Where do I live?", [page], [])
    assert "user.home: The user lives in St Leonards." in note  # falls back when no gist


def test_gist_round_trips_and_reaches_the_wiki(tmp_path):
    s = Session(store_dir=str(tmp_path / "mind"))
    s.compiler.insert(_cand("user.food.favorite_cuisine",
                            "The user now prefers Japanese food over Thai.", gist="japanese"))
    s.compiler.housekeep()
    save_store(s.store, s.store_dir)
    loaded = load_store(s.store_dir)
    assert loaded.clean["user.food.favorite_cuisine"].gist == "japanese"
    page_md = (tmp_path / "mind" / "kainome" / "user.food.favorite_cuisine.md").read_text(
        encoding="utf-8"
    )
    assert "**user.food.favorite_cuisine = japanese**" in page_md   # the headline triple
    assert "prefers Japanese food over Thai" in page_md             # the sentence, still there
    index = (tmp_path / "mind" / "kainome" / "index.md").read_text(encoding="utf-8")
    assert "user.food.favorite_cuisine = japanese" in index         # the router sees the value
