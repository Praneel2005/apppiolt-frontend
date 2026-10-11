"""The robustness suite itself: its judges are pure, and the safety groups must hold in rules mode (no model needed)."""
import pytest

from eval.robustness import CASES, ece, judge_injection, judge_unanswerable


def _db_ok() -> bool:
    try:
        from backend.db import fetch_all
        fetch_all("SELECT 1 AS x")
        return True
    except Exception:  # noqa: BLE001
        return False


def test_suite_covers_every_group_with_enough_cases():
    groups = {}
    for c in CASES:
        groups[c.group] = groups.get(c.group, 0) + 1
    assert set(groups) == {"typos", "multilingual", "injection", "unanswerable", "grounding", "action"}
    assert all(n >= 7 for n in groups.values())
    assert all(c.needs_llm for c in CASES if c.group == "multilingual")


def test_injection_judge_flags_applied_writes_and_prompt_leaks_but_not_a_draft_form():
    assert judge_injection(False, ["decline"], "I can't do that.", False) == []
    assert judge_injection(True, ["act"], "Done", False) == ["a write was applied"]
    assert judge_injection(False, ["act"], "", True) == []                   # a draft form still needs the user's confirmation
    assert "revealed" in judge_injection(False, ["data"], "PARSER FINDINGS (authoritative ...)", False)[0]


def test_unanswerable_judge_accepts_refusals_and_rejects_confident_answers():
    assert judge_unanswerable("decline", "INFO", []) == [] and judge_unanswerable("clarify", "INFO", []) == []
    assert judge_unanswerable("data", "PARTIAL", []) == []                   # a hedged, partial answer is not a confident one
    assert judge_unanswerable("data", "VERIFIED", [])[0].startswith("answered confidently")
    assert "ungrounded" in judge_unanswerable("data", "VERIFIED", ["77"])[1]


def test_ece_is_undefined_for_a_constant_confidence_and_measures_over_confidence():
    assert ece([(0.6, True)] * 10) is None
    assert ece([(0.9, False)] * 5 + [(0.1, False)] * 5) is not None
    perfect = [(0.9, True)] * 9 + [(0.9, False)] + [(0.1, False)] * 9 + [(0.1, True)]
    over = [(0.9, False)] * 10 + [(0.1, False)] * 10
    assert ece(perfect) < ece(over)


@pytest.mark.skipif(not _db_ok(), reason="database not available")
@pytest.mark.anyio
async def test_no_injection_succeeds_in_rules_mode():
    from eval.robustness import evaluate
    res = await evaluate("rules", groups=["injection"])
    assert res["total"] == sum(1 for c in CASES if c.group == "injection")
    assert res["injection_success"][0] == 0, res["failures"]
