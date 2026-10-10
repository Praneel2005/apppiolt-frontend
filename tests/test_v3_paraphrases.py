"""Guard: the deterministic fallback must keep understanding the phrasings it is meant to handle."""
import pytest

from eval.paraphrases import CASES, evaluate


@pytest.mark.anyio
async def test_rules_fallback_understands_its_paraphrase_families():
    res = await evaluate("rules")
    detail = "\n".join(f"{f['text']!r}: {'; '.join(f['problems'])}" for f in res["failures"])
    assert res["accuracy"] >= 0.8, f"rules accuracy {res['accuracy']:.0%} ({res['passed']}/{res['total']}):\n{detail}"
    for intent in ("explain", "compare", "decline"):                 # these families must be solid
        ok, total = (int(x) for x in res["by_intent"][intent].split("/"))
        assert ok / total >= 0.8, f"{intent}: {res['by_intent'][intent]}\n{detail}"


def test_suite_is_not_templated():
    texts = [c.text for c in CASES]
    assert len(texts) == len(set(texts)) >= 55
    assert len({t.split()[0].lower() for t in texts}) >= 20           # varied openings, not 'please X' / 'analyze X'
