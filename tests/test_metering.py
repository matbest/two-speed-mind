"""Slice 8b — token metering (spec §33). A meter per brain; total + rolling last-hour."""
from kaineros.metering import TokenMeter
from kaineros.view import deep_panel, fast_panel
from kaineros.compiler import Compiler
from kaineros.fakes import FakeJudge
from kaineros.schema import Candidate, CompileReport, Provenance
from kaineros.store import Store


def test_total_accumulates_and_ignores_nonpositive():
    m = TokenMeter()
    m.add(100)
    m.add(50)
    m.add(0)
    m.add(-5)
    assert m.total == 150


def test_last_hour_is_a_rolling_window():
    m = TokenMeter()
    now = 10_000.0
    m.add(100, at=now - 4000)  # older than an hour
    m.add(30, at=now - 100)    # within the hour
    m.add(20, at=now - 10)
    assert m.total == 150          # total never forgets
    assert m.last_hour(now=now) == 50  # only the recent two


def test_deep_panel_shows_token_line_when_metered():
    store = Store()
    text = deep_panel(CompileReport(), store, tokens_total=12345, tokens_hour=2100)
    assert "tokens: 12,345 total · 2,100 last hour" in text


def test_panels_omit_tokens_when_unmetered():
    store = Store()
    assert "tokens:" not in deep_panel(CompileReport(), store)
    assert "tokens:" not in fast_panel(None, None, [])
