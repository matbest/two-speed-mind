"""Slice 8d — persona harness (spec §35). Corpus is well-formed; the runner is sound on fakes."""
from kaineros.persona import list_personas, load, run


def test_the_three_personas_exist_and_parse():
    names = list_personas()
    for expected in ("programmer", "elderly", "teenager"):
        assert expected in names
    for name in names:
        convo = load(name)
        assert convo["description"].strip()
        assert len(convo["turns"]) >= 8
        assert all(t.strip() for t in convo["turns"])


def test_runner_builds_a_wiki_on_fakes():
    lines: list[str] = []
    session = run("programmer", fakes=True, out=lines.append)
    # the crude fakes still build *a* wiki and the harness runs end-to-end
    assert session.store.pages()
    assert any("the wiki this persona produced" in ln for ln in lines)
    assert any("totals" in ln for ln in lines)
