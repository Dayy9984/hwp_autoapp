"""Headless auto-evaluation harness for the HWP auto-writing verify loop.

Modules:
    corpus   — load test cases (filesystem template_pairs scan / DB / manifest JSON).
    scorer   — pure, HWP-free deterministic scoring against diff.json ground truth.
    auto_eval — the unattended runner (MODE A deterministic replay, MODE B vision).

MODE A is the primary regression engine: zero LLM, zero API key. It replays the
known filled values into the diff-targeted cells via the real execute_delta path
and scores whether each value landed in the right cell.
"""
