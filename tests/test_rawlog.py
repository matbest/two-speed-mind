"""The primary source (spec §46): raw turns captured verbatim; facts carry their own quotes."""
from __future__ import annotations

import json

from kaineros.cli import Session
from kaineros.compiler import Compiler
from kaineros.fakes import FakeJudge
from kaineros.persist import load_store, save_store
from kaineros.schema import Candidate, Provenance
from kaineros.store import Store


def test_turns_captured_verbatim_exactly_once(tmp_path):
    s = Session(store_dir=str(tmp_path / "mind"))
    s.turn("Green tea is my favourite drink.")
    s.turn("What do I drink?")  # a question — still a turn, still primary source
    raw = (tmp_path / "mind" / "turns.jsonl").read_text(encoding="utf-8").splitlines()
    entries = [json.loads(x) for x in raw]
    texts = [e["text"] for e in entries]
    assert "Green tea is my favourite drink." in texts
    assert len(texts) == len(set(e["id"] for e in entries))  # no duplicates: exactly-once
    n = len(entries)
    s.consolidate()  # nothing new in the buffer -> nothing re-logged
    raw2 = (tmp_path / "mind" / "turns.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(raw2) == n


def test_facts_carry_their_raw_words():
    s = Session()
    s.turn("Green tea is my favourite drink.")
    s.consolidate()
    pool = next(iter(s.store.pool.values()))
    assert pool[0].provenance.source_texts == ("Green tea is my favourite drink.",)


def test_dedup_unions_raw_quotes():
    comp = Compiler(Store(), FakeJudge())
    a = Candidate(gene="g", content="same account",
                  provenance=Provenance(source_texts=("first phrasing",)))
    b = Candidate(gene="g", content="same account",
                  provenance=Provenance(source_texts=("second phrasing",)))
    comp.insert(a)
    comp.insert(b)
    comp.housekeep()
    assert set(comp.store.pool["g"][0].provenance.source_texts) == {
        "first phrasing", "second phrasing"
    }


def test_quotes_survive_disk_and_reach_the_wiki(tmp_path):
    s = Session(store_dir=str(tmp_path / "mind"))
    s.turn("Green tea is my favourite drink.")
    s.consolidate()
    save_store(s.store, s.store_dir)
    loaded = load_store(s.store_dir)
    page = next(iter(loaded.clean.values()))
    assert page.provenance.source_texts == ("Green tea is my favourite drink.",)
    gene_md = next(
        f for f in (tmp_path / "mind" / "kainome").glob("*.md") if f.name != "index.md"
    ).read_text(encoding="utf-8")
    assert 'said as: "Green tea is my favourite drink."' in gene_md
