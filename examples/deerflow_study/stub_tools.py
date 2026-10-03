from langchain_core.tools import tool

@tool("web_search")
def web_search_tool(query: str) -> str:
    """Stub search: returns fixed text, no network."""
    return f"stub result for {query}"
