import requests
from bs4 import BeautifulSoup
from duckduckgo_search import DDGS
from fastmcp import FastMCP

mcp = FastMCP("job-finder-tools")


@mcp.tool()
def web_search(query: str) -> str:
    """Searches the web for a query and returns search result snippets.

    Args:
        query: The search query, e.g. "C2C Data Engineer jobs linkedin".
    """
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=8))
            if not results:
                return "No search results found."
            formatted = []
            for r in results:
                formatted.append(
                    f"Title: {r.get('title')}\nURL: {r.get('href')}\nSnippet: {r.get('body')}\n---"
                )
            return "\n".join(formatted)
    except Exception as e:
        return f"Error performing search: {e}"


@mcp.tool()
def fetch_webpage_content(url: str) -> str:
    """Fetches the text content of a webpage to extract detailed job requirements.

    Args:
        url: The absolute URL of the webpage to fetch.
    """
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code >= 400:
            return f"Failed to fetch content, status: {resp.status_code}"

        soup = BeautifulSoup(resp.text, "html.parser")
        # Remove scripts, styles, headers, footers
        for element in soup(
            ["script", "style", "meta", "noscript", "header", "footer", "nav"]
        ):
            element.decompose()

        text = soup.get_text(separator=" ")
        lines = (line.strip() for line in text.splitlines())
        chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
        text = "\n".join(chunk for chunk in chunks if chunk)
        # Limit to 4000 characters to avoid context bloat
        return text[:4000]
    except Exception as e:
        return f"Error fetching webpage: {e}"


if __name__ == "__main__":
    mcp.run()
