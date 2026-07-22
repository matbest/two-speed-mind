"""External benchmark harnesses, quarantined.

The rule: bench imports from the core; the core never imports bench. Adapters here may only
touch the same public surface a user touches (Session, turn/consolidate/flush, runtime.respond).
If a benchmark seems to need something deeper, that's a core feature discussion — not a
reach-around in the adapter.
"""
