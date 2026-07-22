"""Update signals are detected deterministically from the turn text — not asked of the model."""
from kaineros.cloud import looks_like_update


def test_detects_deliberate_updates():
    for text in [
        "Update: as of today I work at Initech, not Acme.",
        "I've moved to San Francisco.",
        "I no longer drive the Honda.",
        "I switched to a Tesla.",
        "These days I work at Initech.",
    ]:
        assert looks_like_update(text), text


def test_ignores_plain_statements():
    for text in [
        "I work at Acme Corp.",
        "My birthplace is Boston.",
        "I love mangoes.",
        "In the morning I drink coffee.",
    ]:
        assert not looks_like_update(text), text
