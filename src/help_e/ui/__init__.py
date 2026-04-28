"""Interactive demo UI — pick an ablation system + profile, chat with it,
and watch the per-turn internal state (extraction, intents, TTM stages,
instruction, MI candidates, graph snapshot) update live.

The backend wraps the existing per-turn pipeline (`session_driver` +
baseline turn_fns) so UI-initiated conversations reuse the same code path
that full matrix runs do. No Mind-1 — the human at the keyboard supplies
the user message.
"""
