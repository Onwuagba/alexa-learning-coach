"""Play the child's side of the Alexa+ demo against a RUNNING coach over real MCP (streamable HTTP).

  python scripts/demo_tv.py --child ada --answers 2 [--url http://127.0.0.1:8000] [--db progress.db]
                            [--mcp-token T] [--wrong]

Stands in for Alexa+ (we could not verify how Alexa+ calls MCP servers). Answers are read from the
coach's SQLite file (--db, must be the file the server uses): a demo shortcut for "the child answers aloud".
"""
import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mcp import ClientSession  # noqa: E402
from mcp.client.streamable_http import streamablehttp_client  # noqa: E402

from learning_coach.store import Store  # noqa: E402


async def main(a):
    store = Store(a.db)
    headers = {"Authorization": f"Bearer {a.mcp_token}"} if a.mcp_token else None
    async with streamablehttp_client(a.url.rstrip("/") + "/mcp/", headers=headers) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            async def call(n, **k):
                res = await s.call_tool(n, k)
                return res.structuredContent or json.loads(res.content[0].text)
            print("Child: Alexa, let's practise", a.topic)
            for _ in range(a.answers):
                q = await call("next_question", child=a.child, topic=a.topic)
                print(f"Coach [level {q['level']}]: {q['prompt']}")
                ans = store.peek_pending(a.child.lower())["answer"]
                ans = "999999" if a.wrong else ans
                res = await call("check_answer", child=a.child, answer=ans)
                print(f"  {a.child}: {ans} -> {'correct' if res['correct'] else 'not quite'}; level now {res['level']}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--child", default="ada"); p.add_argument("--topic", default="abacus")
    p.add_argument("--answers", type=int, default=2); p.add_argument("--wrong", action="store_true")
    p.add_argument("--url", default="http://127.0.0.1:8000"); p.add_argument("--db", default="progress.db")
    p.add_argument("--mcp-token", default=os.getenv("MCP_AUTH_TOKEN"))
    asyncio.run(main(p.parse_args()))
