from __future__ import annotations

from typing import Any, Dict

from langgraph.graph import StateGraph, END

from .state import WatchDogState
from .scrape_node import scrape_node
from .respond_node import respond_node


def _cond_edge(state: Dict[str, Any]) -> str:
    # If scraping found no change, end; else go to respond_node
    status = state.get("status") or state.get("scrape", {}).get("status")
    if status == "NO_CHANGE":
        return END
    return "respond_node"


def build_graph():
    sg = StateGraph(dict)

    sg.add_node("scrape_node", scrape_node)
    sg.add_node("respond_node", respond_node)

    sg.set_entry_point("scrape_node")
    sg.add_conditional_edges("scrape_node", _cond_edge, {"respond_node": "respond_node", END: END})
    sg.add_edge("respond_node", END)
    return sg.compile()

