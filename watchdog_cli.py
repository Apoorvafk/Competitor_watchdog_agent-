from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any, Dict

from dotenv import load_dotenv

from src.competitor_watchdog.graph import build_graph
from src.competitor_watchdog.state import WatchDogState, empty_output
from src.competitor_watchdog.tools import sqlite_read_snapshot, compute_hash, post_discord, await_discord


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Competitor Watch Dog (backend-only)")
    p.add_argument("--url", required=True, help="Target URL to monitor")
    p.add_argument("--crawl-depth", type=int, default=0, choices=[0, 1])
    p.add_argument("--allow-subpaths", action="store_true")
    p.add_argument("--allowed-paths", nargs="*", default=[])
    p.add_argument("--products", nargs="*", default=[])
    p.add_argument("--keywords", nargs="*", default=[])
    p.add_argument("--tone", default="neutral", choices=["neutral", "challenger", "friendly"])
    p.add_argument("--force-post", action="store_true", help="Force posting to Discord even if low significance")
    p.add_argument("--force-change", action="store_true", help="Bypass snapshot equality and treat as changed")
    p.add_argument("--force-discord", action="store_true", help="Immediately send a test Discord post for this URL and await approval")
    p.add_argument("--timeout-s", type=int, default=None)
    p.add_argument("--wait-selector", default=None)
    return p.parse_args()


async def run_once(ns: argparse.Namespace) -> Dict[str, Any]:
    # Direct Discord test path: bypass scraping entirely
    if ns.force_discord:
        url = ns.url
        url_hash = compute_hash(url)[:12]
        change_hash = compute_hash("force-discord")[:12]
        title = "🔍 Competitor Update Detected (Test)"
        body = "\n".join([
            f"{title}",
            f"URL: {url}",
            "Significance: low",
            "\nSummary:",
            "- Test post to verify Discord wiring.",
            "\nRecommended actions:",
            "- Confirm bot can post and buttons work",
        ])
        try:
            posted = await post_discord(body, ["Approve", "Reject"], url_hash, change_hash)
            message_id = posted.get("message_id")
            from os import getenv as _getenv
            wait_s = int(_getenv("APPROVAL_TIMEOUT_S", "60"))
            approval = await await_discord(message_id, wait_s, url_hash, change_hash)
        except Exception as e:
            return {
                "status": "ERROR",
                "url": url,
                "change_hash": change_hash,
                "highlights": [],
                "significance": "low",
                "draft_response": "",
                "next_actions": [],
                "discord_message_id": None,
                "approval": {"state": "pending"},
                "errors": [str(e)],
            }

        return {
            "status": "OK",
            "url": url,
            "change_hash": change_hash,
            "highlights": [
                {"title": "Test", "why_it_matters": "Verify Discord connectivity", "evidence": "manual test"}
            ],
            "significance": "low",
            "draft_response": "- Test run to verify posting.",
            "next_actions": ["Verify buttons", "Check bot permissions"],
            "discord_message_id": message_id,
            "approval": approval or {"state": "pending"},
            "errors": [],
        }
    state = WatchDogState(
        url=ns.url,
        crawl_policy={
            "depth": ns.crawl_depth,
            "allow_subpaths": bool(ns.allow_subpaths),
            "allowed_paths": ns.allowed_paths or [],
        },
        business_context={
            "products": ns.products or [],
            "keywords": ns.keywords or [],
            "tone": ns.tone,
        },
    )

    # Load last snapshot hash
    try:
        row = sqlite_read_snapshot(ns.url)
        if row:
            state.last_snapshot_hash = row.get("hash")
    except Exception:
        pass

    graph = build_graph()
    # Graph expects a dict and returns streaming states; we just invoke and await final
    inputs = {"url": state.url, "crawl_policy": state.crawl_policy, "business_context": state.business_context, "last_snapshot_hash": state.last_snapshot_hash, "force_post": bool(ns.force_post), "force_change": bool(ns.force_change)}
    final_state = await graph.ainvoke(inputs)

    # Scrape-only NO_CHANGE path
    if final_state.get("status") == "NO_CHANGE" or (final_state.get("scrape", {}) or {}).get("status") == "NO_CHANGE":
        out = {
            "status": "NO_CHANGE",
            "url": ns.url,
            "change_hash": final_state.get("change_hash"),
            "highlights": [],
            "significance": "low",
            "draft_response": "",
            "next_actions": [],
            "discord_message_id": None,
            "approval": {"state": "pending"},
            "errors": final_state.get("errors", []),
        }
        return out

    # If respond_node ran, it should have "result"
    res = final_state.get("result")
    if res:
        # Emit approved payload if approved
        try:
            from datetime import datetime, timezone

            appr = res.get("approval", {})
            if appr.get("state") == "approved":
                payload = {
                    "type": "COMPETITOR_UPDATE_APPROVED",
                    "url": res.get("url"),
                    "change_hash": res.get("change_hash"),
                    "actions": res.get("next_actions", []),
                    "approved_by": appr.get("by"),
                    "approved_at": datetime.now(timezone.utc).isoformat(),
                }
                print(json.dumps(payload, ensure_ascii=False))
        except Exception:
            pass
        return res

    # Fallback error
    out = empty_output(ns.url)
    out["errors"] = final_state.get("errors", ["Unknown error"])
    return out


def main() -> None:
    load_dotenv()
    ns = parse_args()
    try:
        out = asyncio.run(run_once(ns))
        print(json.dumps(out, ensure_ascii=False))
    except KeyboardInterrupt:
        pass
    except Exception as e:
        out = empty_output(ns.url)
        out["errors"].append(str(e))
        print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
