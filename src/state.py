from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, TypedDict


Status = Literal["OK", "NO_CHANGE", "ERROR"]


class CrawlPolicy(TypedDict, total=False):
    depth: int
    allow_subpaths: bool
    allowed_paths: List[str]


class BusinessContext(TypedDict, total=False):
    products: List[str]
    keywords: List[str]
    tone: Literal["neutral", "challenger", "friendly"]


class DiffResult(TypedDict):
    added: List[Dict[str, Any]]
    removed: List[Dict[str, Any]]
    changed: List[Dict[str, Any]]


class Block(TypedDict):
    selector: str
    text: str


class Candidate(TypedDict):
    title: str
    evidence: str
    selector: str
    change_type: Literal["added", "modified", "removed"]


class ScrapeResult(TypedDict, total=False):
    url: str
    http: Dict[str, Any]
    hash: str
    blocks: List[Block]
    diff: DiffResult
    candidates: List[Candidate]


class Approval(TypedDict, total=False):
    state: Literal["pending", "approved", "rejected"]
    by: str
    reason: str


@dataclass
class WatchDogState:
    url: str = ""
    crawl_policy: CrawlPolicy = field(
        default_factory=lambda: {"depth": 0, "allow_subpaths": False, "allowed_paths": []}
    )
    business_context: BusinessContext = field(
        default_factory=lambda: {"products": [], "keywords": [], "tone": "neutral"}
    )
    last_snapshot_hash: Optional[str] = None
    scrape: Optional[ScrapeResult] = None
    change_hash: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    errors: List[str] = field(default_factory=list)


def empty_output(url: str) -> Dict[str, Any]:
    return {
        "status": "ERROR",
        "url": url,
        "change_hash": None,
        "highlights": [],
        "significance": "low",
        "draft_response": "",
        "next_actions": [],
        "discord_message_id": None,
        "approval": {"state": "pending"},
        "errors": [],
    }
