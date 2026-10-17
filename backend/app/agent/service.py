from functools import lru_cache

from langchain_core.messages import AIMessage, HumanMessage

from app.agent.graph import build_graph
from app.agent.llm import get_llm


@lru_cache
def _get_graph():
    return build_graph(get_llm())


def generate_reply(history: list[dict]) -> str:
    """history: [{"role": "user" | "assistant", "content": str}, ...], ending with the new user message."""
    messages = [
        HumanMessage(m["content"]) if m["role"] == "user" else AIMessage(m["content"])
        for m in history
    ]
    result = _get_graph().invoke({"messages": messages})
    return result["messages"][-1].content
