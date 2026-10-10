"""Show exactly how the agent understands a message and which pages it considers (rules mode, no LLM, no database writes).

    python scripts/debug_understanding.py "open orders"
    python scripts/debug_understanding.py "payments received by payment method" "why did sales drop"
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.capabilities import get_capability_model  # noqa: E402
from agent.interpret import interpret  # noqa: E402
from agent.lexicon import get_lexicon  # noqa: E402
from agent.schema import TurnContext, get_agent_memory  # noqa: E402
from agent.skills.view import score_pages  # noqa: E402
from backend.sessions import create_session  # noqa: E402
from contracts.actions import AgentConfig  # noqa: E402


async def main(texts: list[str]) -> None:
    lex, cap = get_lexicon(), get_capability_model()
    for text in texts:
        s = create_session()
        ctx = TurnContext(session=s, text=text, page_ctx={"page": {}, "widgets": [], "filters": []}, cfg=AgentConfig(),
                          lex=lex, cap=cap, mem=get_agent_memory(s.id), llm=None, as_of=lex.as_of)
        it = await interpret(ctx)
        print(f"\n=== {text!r}")
        print("parser :", json.dumps(ctx.ann.summary(), ensure_ascii=False))
        print("intent :", it.brief(), f"[{it.source}]")
        if it.assumptions:
            print("notes  :", " | ".join(it.assumptions))
        if it.intent == "view":
            for sc in score_pages(ctx, it)[:6]:
                print(f"   {sc.score:6.2f}  {sc.page_id:38s} {sc.title:32s} missing={sc.missing}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:] or ["open orders"]))
