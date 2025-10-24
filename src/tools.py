from __future__ import annotations

import asyncio
import hashlib
import json
import os
import random
import re
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type


# Environment helpers
def getenv(name: str, default: Optional[str] = None) -> Optional[str]:
    v = os.getenv(name)
    return v if v is not None else default


# Hashing
def compute_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


# SQLite helpers
def _get_db_conn() -> sqlite3.Connection:
    db_path = getenv("SQLITE_PATH", "./watchdog.db")
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS snapshots (
            url TEXT PRIMARY KEY,
            hash TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def sqlite_read_snapshot(url: str) -> Optional[Dict[str, str]]:
    try:
        conn = _get_db_conn()
        cur = conn.cursor()
        cur.execute("SELECT hash FROM snapshots WHERE url = ?", (url,))
        row = cur.fetchone()
        conn.close()
        if row:
            return {"hash": row[0]}
        return None
    except Exception:
        return None


def sqlite_write_snapshot(url: str, snap_hash: str) -> bool:
    try:
        conn = _get_db_conn()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO snapshots(url, hash, updated_at) VALUES (?,?,datetime('now'))\n"
            "ON CONFLICT(url) DO UPDATE SET hash=excluded.hash, updated_at=datetime('now')",
            (url, snap_hash),
        )
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


# Diff at paragraph/section level
def diff(old_text: str, new_text: str) -> Dict[str, List[Dict[str, Any]]]:
    def split_paragraphs(t: str) -> List[str]:
        norm = re.sub(r"\s+", " ", t).strip()
        parts = [p.strip() for p in re.split(r"\n{2,}|\.|\!|\?", norm) if p.strip()]
        return parts

    old_ps = split_paragraphs(old_text)
    new_ps = split_paragraphs(new_text)

    old_set = set(old_ps)
    new_set = set(new_ps)

    added = [
        {"text": p, "selector": "", "title": p[:60] + ("…" if len(p) > 60 else "")}
        for p in (new_set - old_set)
    ]
    removed = [
        {"text": p, "selector": "", "title": p[:60] + ("…" if len(p) > 60 else "")}
        for p in (old_set - new_set)
    ]

    changed = []  # heuristic: treat as added/removed; full mapping would need LCS
    return {"added": added, "removed": removed, "changed": changed}


# HTML parsing helpers
def _build_selector(el) -> str:
    parts = []
    node = el
    while node is not None and getattr(node, "name", None) and node.name != "[document]":
        seg = node.name
        if node.get("id"):
            seg += f"#{node['id']}"
        if node.get("class"):
            classes = ".".join(node.get("class"))
            seg += f".{classes}"
        parts.append(seg)
        node = node.parent
    return " > ".join(reversed(parts))


def parse_html(html: str) -> List[Dict[str, str]]:
    soup = BeautifulSoup(html, "lxml")

    # Remove boilerplate
    for sel in [
        "nav", "footer", "header", "script", "style", "noscript",
        "[role='navigation']", ".cookie", "#cookie", "[aria-label*='cookie']",
    ]:
        for n in soup.select(sel):
            n.decompose()

    blocks: List[Dict[str, str]] = []

    # Headings
    for h in soup.select("h1, h2, h3"):
        text = re.sub(r"\s+", " ", h.get_text(separator=" ", strip=True))
        if text:
            blocks.append({"selector": _build_selector(h), "text": text})

    # Main/article content
    for main_sel in ["main", "article", "section[role='main']"]:
        for m in soup.select(main_sel):
            text = re.sub(r"\s+", " ", m.get_text(separator=" ", strip=True))
            if text and len(text) > 40:
                blocks.append({"selector": _build_selector(m), "text": text})

    # Pricing/features/changelog heuristics
    for sel in [
        "#pricing, .pricing, section[id*='pricing'], section[class*='pricing']",
        "#features, .features, section[id*='feature'], section[class*='feature']",
        "#changelog, .changelog, section[id*='changelog'], section[class*='changelog']",
        "ul li, ol li",
    ]:
        for n in soup.select(sel):
            text = re.sub(r"\s+", " ", n.get_text(separator=" ", strip=True))
            if text and len(text) > 20:
                blocks.append({"selector": _build_selector(n), "text": text})

    # Heuristic: sections whose heading mentions changelog/release
    for sec in soup.select("section"):
        h = sec.find(["h1", "h2", "h3"])  # type: ignore
        if not h:
            continue
        ht = (h.get_text(" ", strip=True) or "").lower()
        if any(k in ht for k in ["changelog", "release", "what's new", "updates", "release notes"]):
            text = re.sub(r"\s+", " ", sec.get_text(separator=" ", strip=True))
            if text and len(text) > 20:
                blocks.append({"selector": _build_selector(sec), "text": text})

    # De-duplicate by selector
    seen = set()
    uniq: List[Dict[str, str]] = []
    for b in blocks:
        if b["selector"] in seen:
            continue
        seen.add(b["selector"])
        uniq.append(b)
    return uniq


# Playwright fetch
class FetchError(Exception):
    pass


async def _ensure_playwright_browser():
    # Try to ensure the Chromium browser is installed
    try:
        from playwright.__main__ import main as pw_main

        # Best effort; ignore failures
        await asyncio.to_thread(pw_main, ["install", "chromium"])
    except Exception:
        pass


async def fetch_url(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    timeout: Optional[int] = None,
    wait_selector: Optional[str] = None,
) -> Dict[str, Any]:
    await _ensure_playwright_browser()

    from playwright.async_api import async_playwright
    import urllib.robotparser as robotparser

    # robots.txt
    try:
        rp = robotparser.RobotFileParser()
        origin = re.match(r"^(https?://[^/]+)/?", url)
        if origin:
            rp.set_url(origin.group(1) + "/robots.txt")
            await asyncio.get_event_loop().run_in_executor(None, rp.read)
            can_fetch = rp.can_fetch("*", url)
            if not can_fetch:
                raise FetchError("Disallowed by robots.txt")
    except Exception:
        # If robots parsing fails, proceed (best-effort)
        pass

    # Jitter per spec
    await asyncio.sleep(random.uniform(0.4, 1.2))

    ua = getenv("USER_AGENT", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36 WatchDog/1.0")
    to_ms = int(getenv("REQUEST_TIMEOUT_S", "20")) * 1000 if timeout is None else timeout * 1000

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(user_agent=ua)
        page = await ctx.new_page()
        http_info: Dict[str, Any] = {"status": None, "etag": None, "last_modified": None}

        try:
            resp = await page.goto(url, timeout=to_ms, wait_until="networkidle")
            if resp:
                http_info["status"] = resp.status
                http_info["etag"] = resp.headers.get("etag") if hasattr(resp, "headers") else None
                http_info["last_modified"] = resp.headers.get("last-modified") if hasattr(resp, "headers") else None
            if wait_selector:
                try:
                    await page.wait_for_selector(wait_selector, timeout=to_ms)
                except Exception:
                    pass
            html = await page.content()
            text = await page.inner_text("body")
        finally:
            await ctx.close()
            await browser.close()

    max_bytes = int(getenv("SCRAPE_MAX_BYTES", str(1_500_000)))
    if len(html.encode("utf-8")) > max_bytes:
        # Truncate safely
        html = html[: max_bytes // 2]
    if len(text.encode("utf-8")) > max_bytes:
        text = text[: max_bytes // 2]

    return {"status": http_info.get("status", 200), "headers": http_info, "html": html, "text": text}


# Discord utils
class DiscordClient:
    def __init__(self, token: str, channel_id: int) -> None:
        import discord

        intents = discord.Intents.none()
        self.client = discord.Client(intents=intents)
        self.token = token
        self.channel_id = channel_id
        self._ready = asyncio.Event()
        self._last_message_id: Optional[int] = None

        @self.client.event
        async def on_ready():
            self._ready.set()

    async def __aenter__(self):
        self._task = asyncio.create_task(self.client.start(self.token))
        await self._ready.wait()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.client.close()
        if self._task:
            try:
                await self._task
            except Exception:
                pass

    async def post(self, markdown: str, approve_id: str, reject_id: str) -> str:
        import discord

        channel = self.client.get_channel(self.channel_id)
        if channel is None:
            raise RuntimeError("Discord channel not found or bot lacks access")

        view = discord.ui.View(timeout=None)
        view.add_item(discord.ui.Button(label="Approve ✅", style=discord.ButtonStyle.success, custom_id=approve_id))
        view.add_item(discord.ui.Button(label="Reject ❌", style=discord.ButtonStyle.danger, custom_id=reject_id))

        msg = await channel.send(markdown, view=view)
        self._last_message_id = msg.id
        return str(msg.id)

    async def await_interaction(self, message_id: str, timeout_s: int) -> Dict[str, Any]:
        import discord

        def check(interaction: discord.Interaction):
            return (
                interaction.data is not None
                and interaction.message is not None
                and str(interaction.message.id) == message_id
                and interaction.type == discord.InteractionType.component
            )

        try:
            interaction = await self.client.wait_for("interaction", timeout=timeout_s, check=check)
        except asyncio.TimeoutError:
            return {"state": "pending"}

        custom_id = interaction.data.get("custom_id") if interaction.data else ""
        user = str(interaction.user) if interaction.user else ""

        # Acknowledge the interaction
        try:
            await interaction.response.send_message("Thanks! Recorded your decision.", ephemeral=True)
        except Exception:
            pass

        if custom_id and custom_id.startswith("approve:"):
            return {"state": "approved", "by": user}
        elif custom_id and custom_id.startswith("reject:"):
            # Try reading an optional reason if present in the message thread in the future
            return {"state": "rejected", "by": user, "reason": ""}
        return {"state": "pending"}


async def post_discord(markdown: str, buttons: List[str], url_hash: str, change_hash: str) -> Dict[str, Any]:
    token = getenv("DISCORD_BOT_TOKEN")
    channel_id = getenv("DISCORD_CHANNEL_ID")
    if not token or not channel_id:
        raise RuntimeError("Missing DISCORD_BOT_TOKEN or DISCORD_CHANNEL_ID")
    approve_id = f"approve:{url_hash}:{change_hash}"
    reject_id = f"reject:{url_hash}:{change_hash}"
    async with DiscordClient(token, int(channel_id)) as dc:
        msg_id = await dc.post(markdown, approve_id, reject_id)
        return {"message_id": msg_id}


async def await_discord(message_id: str, timeout_s: int, url_hash: str, change_hash: str) -> Dict[str, Any]:
    # We need a running DiscordClient to await interaction; create a temporary session
    token = getenv("DISCORD_BOT_TOKEN")
    channel_id = getenv("DISCORD_CHANNEL_ID")
    if not token or not channel_id:
        return {"state": "pending"}
    async with DiscordClient(token, int(channel_id)) as dc:
        result = await dc.await_interaction(message_id, timeout_s)
        return result


# Gemini client
class GeminiClient:
    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None) -> None:
        import google.generativeai as genai

        key = api_key or getenv("GEMINI_API_KEY")
        if not key:
            raise RuntimeError("Missing GEMINI_API_KEY")
        genai.configure(api_key=key)
        self.model_name = model or getenv("GEMINI_MODEL", "gemini-1.5-flash")
        self._genai = genai

    async def generate(self, system_prompt: str, user_payload: Dict[str, Any]) -> str:
        # Run in threadpool because google-generativeai is sync
        def _call():
            model = self._genai.GenerativeModel(self.model_name)
            content = [
                {"role": "system", "parts": [{"text": system_prompt}]},
                {"role": "user", "parts": [{"text": json.dumps(user_payload)}]},
            ]
            resp = model.generate_content(content)
            return resp.text or ""

        return await asyncio.to_thread(_call)
