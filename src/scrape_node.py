from __future__ import annotations

import asyncio
import re
from typing import Any, Dict, List

from .tools import compute_hash, diff as diff_text, fetch_url, parse_html, getenv


async def scrape_node(state: Dict[str, Any]) -> Dict[str, Any]:
    url = state.get("url", "")
    result: Dict[str, Any] = {"url": url, "http": {}, "hash": "", "blocks": [], "diff": {"added": [], "removed": [], "changed": []}, "candidates": []}
    try:
        wait_sel = None
        http = await fetch_url(url, headers=None, timeout=None, wait_selector=wait_sel)
        result["http"] = {"status": http.get("status"), "etag": http.get("headers", {}).get("etag"), "last_modified": http.get("headers", {}).get("last_modified")}

        blocks = parse_html(http["html"]) or []
        # Build main_text by concatenating top blocks (cap length)
        main_text = "\n\n".join([b["text"] for b in blocks])
        main_text = re.sub(r"\s+", " ", main_text).strip()
        cap = int(getenv("SCRAPE_MAX_BYTES", str(1_500_000)))
        if len(main_text.encode("utf-8")) > cap:
            main_text = main_text[: cap // 2]

        h = compute_hash(main_text)
        result["hash"] = h
        result["blocks"] = blocks

        # If no previous snapshot or unchanged
        last_hash = state.get("last_snapshot_hash")
        if not state.get("force_change") and last_hash is not None and last_hash == h:
            state["scrape"] = result
            state["change_hash"] = h
            return {"scrape": result, "change_hash": h, "status": "NO_CHANGE"}

        # Compute diff if we had old text (not available — treat as added on first change)
        old_text = ""  # Unknown; on first run treat everything as added
        d = diff_text(old_text, main_text) if old_text else {"added": [{"text": b["text"], "selector": b["selector"], "title": b["text"][:60] + ("…" if len(b["text"]) > 60 else "")} for b in blocks[:50]], "removed": [], "changed": []}
        result["diff"] = d

        # Build candidates from added/changed blocks
        candidates = []
        def mk_candidate(title: str, text: str, selector: str, change_type: str) -> Dict[str, str]:
            ev = text.strip()
            ev = ev[:140] + ("…" if len(ev) > 140 else "")
            return {"title": title[:80] + ("…" if len(title) > 80 else ""), "evidence": ev, "selector": selector, "change_type": change_type}

        for ent in d.get("added", [])[:8]:
            candidates.append(mk_candidate(ent.get("title", "New content"), ent.get("text", ""), ent.get("selector", ""), "added"))
        # changed
        for ent in d.get("changed", [])[: max(0, 8 - len(candidates))]:
            candidates.append(mk_candidate(ent.get("title", "Updated content"), ent.get("text", ""), ent.get("selector", ""), "modified"))

        result["candidates"] = candidates
        state["scrape"] = result
        state["change_hash"] = h
        return {"scrape": result, "change_hash": h, "status": "OK"}
    except Exception as e:
        err = str(e)
        errs = state.get("errors", [])
        errs.append(err)
        return {"scrape": result, "status": "ERROR", "errors": errs}
