from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Dict, List, Tuple

from .tools import GeminiClient, compute_hash, getenv, post_discord, await_discord, sqlite_write_snapshot


def _score_candidate(c: Dict[str, str], keywords: List[str]) -> int:
    text = (c.get("title", "") + " " + c.get("evidence", "")).lower()
    score = 0
    if any(k.lower() in text for k in (keywords or [])):
        score += 1
    if any(k in text for k in ["price", "pricing", "plan", "feature", "new", "launch", "release", "changelog", "package", "tier", "trial"]):
        score += 1
    if any(k in text for k in ["enterprise", "startup", "SMB", "case study", "integration", "salesforce", "hubspot", "zendesk", "segment", "snowflake"]):
        score += 1
    return score


def _significance(items: List[Dict[str, Any]]) -> str:
    if not items:
        return "low"
    txt = " ".join([i.get("title", "") + " " + i.get("evidence", "") for i in items]).lower()
    if any(x in txt for x in ["pricing", "price", "plan", "enterprise", "launch", "new feature"]):
        return "high"
    if len(items) >= 2:
        return "medium"
    return "low"


async def respond_node(state: Dict[str, Any]) -> Dict[str, Any]:
    scrape = state.get("scrape", {}) or {}
    url = state.get("url", "")
    candidates: List[Dict[str, str]] = scrape.get("candidates", [])
    business = state.get("business_context", {}) or {}
    keywords = business.get("keywords", []) or []
    tone = business.get("tone", "neutral")

    # Score and pick top 1-3 with score >= 2
    scored = [(c, _score_candidate(c, keywords)) for c in candidates]
    scored.sort(key=lambda x: x[1], reverse=True)
    picked = [c for c, s in scored if s >= 2][:3]

    # If nothing meets threshold and force_post enabled, fall back to top 1-3 regardless of score
    if not picked and state.get("force_post") and candidates:
        picked = [c for c, _ in scored[:3]]

    if not picked:
        # No significant items and not forcing
        return {
            "result": {
                "status": "NO_CHANGE",
                "url": url,
                "change_hash": state.get("change_hash"),
                "highlights": [],
                "significance": "low",
                "draft_response": "",
                "next_actions": [],
                "discord_message_id": None,
                "approval": {"state": "pending"},
                "errors": state.get("errors", []),
            }
        }

    sig = _significance(picked)

    # Gemini draft
    try:
        gclient = GeminiClient()
        system_prompt = (
            "You are Competitor Watch Dog. Draft a concise, bullet-first, imperative action plan "
            "(<=1200 chars) based strictly on provided candidates. Include short evidence quotes (<=140 chars). "
            "Tone must match the provided tone (neutral|challenger|friendly). No speculative claims; only summarize provided content."
        )
        payload = {
            "url": url,
            "tone": tone,
            "business_context": {"keywords": keywords, "products": business.get("products", [])},
            "candidates": picked,
        }
        draft = await gclient.generate(system_prompt, payload)
        # Trim to limit
        if len(draft) > 1200:
            draft = draft[:1200]
    except Exception as e:
        draft = "- Summarize detected changes and prepare internal brief."

    # Highlights compact
    highlights = [
        {
            "title": c.get("title", ""),
            "why_it_matters": "Matches keywords/pricing/ICP",
            "evidence": c.get("evidence", "") or c.get("selector", ""),
        }
        for c in picked
    ]

    # Next actions, heuristic + let draft stand on its own
    next_actions = [
        "Update pricing comparison page if needed",
        "Share summary in sales channel",
        "Evaluate roadmap impact for competing features",
    ]

    url_hash = compute_hash(url)[:12]
    change_hash = state.get("change_hash") or compute_hash(json.dumps(picked))[:12]

    # Post to Discord
    title = "🔍 Competitor Update Detected"
    body_lines = [
        f"{title}",
        f"URL: {url}",
        f"Significance: {sig}",
        "\nSummary:",
        draft,
        "\nRecommended actions:",
        *(f"- {a}" for a in next_actions),
    ]
    markdown = "\n".join(body_lines)

    discord_message_id = None
    approval: Dict[str, Any] = {"state": "pending"}
    try:
        posted = await post_discord(markdown, ["Approve", "Reject"], url_hash, change_hash)
        discord_message_id = posted.get("message_id")
        wait_s = int(getenv("APPROVAL_TIMEOUT_S", "120"))
        approval = await await_discord(discord_message_id, wait_s, url_hash, change_hash)
    except Exception as e:
        # Keep pending on failure
        pass

    # Persist snapshot only if approved
    if approval.get("state") == "approved" and state.get("change_hash"):
        sqlite_write_snapshot(url, state.get("change_hash"))

    # Unified JSON
    result = {
        "status": "OK",
        "url": url,
        "change_hash": state.get("change_hash"),
        "highlights": highlights,
        "significance": sig,
        "draft_response": draft,
        "next_actions": next_actions,
        "discord_message_id": discord_message_id,
        "approval": approval,
        "errors": state.get("errors", []),
    }

    return {"result": result}
