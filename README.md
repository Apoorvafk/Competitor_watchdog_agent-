Competitor Watch Dog

AI-powered competitor monitoring agent built with LangGraph & Gemini 2.5 Flash

Overview

Competitor Watch Dog is a LangGraph-based AI backend that automatically monitors competitor websites for new information (feature launches, pricing changes, updates, etc.), drafts a recommended response plan, and posts it to a Discord channel for human approval.

The project implements a two-agent workflow:

Scraping Agent – scrapes competitor sites, detects changes, and computes diffs.

Response Agent – interprets those changes, drafts an actionable plan, and posts it to Discord.

You can run it directly from your terminal — no frontend required yet.
Approved actions are logged as structured JSON payloads for downstream workflows (e.g., blog tasks, marketing actions, CRM updates).

Architecture
Agent Workflow (LangGraph)
[Scrape Node] → [Conditional Node] → [Respond Node] → [Terminal Node]

Node	Description
Scraping Agent	Fetches target site via Playwright, extracts normalized text blocks, and computes a SHA256 content hash. If unchanged from last snapshot (in SQLite), returns NO_CHANGE.
Response Agent	Scores differences by relevance, drafts concise bullet-style actions (≤1200 chars), and sends an approval message to Discord via bot webhook.
Orchestrator	Manages state transitions, database persistence, and conditional edges in LangGraph.
🔧 Tech Stack

Framework: LangGraph

LLM: Gemini 2.5 Flash

Scraper: Playwright + BeautifulSoup / Selectolax

Database: SQLite (snapshot store)

Notifications: Discord Bot (approval workflow)

Language: Python 3.10+
