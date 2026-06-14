import os
import json
import asyncio
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from google.antigravity import Agent, LocalAgentConfig, types
from duckduckgo_search import DDGS

# Load environment variables
load_dotenv()

# Pydantic Schemas for Structured Output
class JobItem(BaseModel):
    title: str = Field(description="The job title")
    company: str = Field(description="The company name")
    location: str = Field(description="The location, e.g. 'Remote', 'City, State', or 'Hybrid'")
    url: str = Field(description="The direct job posting link or source URL")
    date_posted: str = Field(description="Date posted or found, e.g. '2 hours ago', 'June 12'")
    c2c_viability: str = Field(description="Confirmation of C2C viability: 'Confirmed C2C', 'Likely C2C' (if mentions C2C or corp-to-corp but not explicitly confirmed), or 'Not Specified'")
    key_requirements: list[str] = Field(description="List of key requirements/skills mentioned")
    contact_email: str | None = Field(None, description="Recruiter or contact email address if available")
    contact_phone: str | None = Field(None, description="Recruiter or contact phone number if available")
    source: str = Field(description="Source website/portal, e.g., LinkedIn, Indeed, Dice, etc.")
    description: str = Field(description="Short summary of the job description, C2C terms, and other details")

class JobList(BaseModel):
    jobs: list[JobItem] = Field(description="List of jobs found")

# Custom Tools
def web_search(query: str) -> str:
    """Searches the web for a query and returns search result snippets.
    
    Args:
        query: The search query, e.g. "C2C Data Engineer jobs linkedin".
    """
    try:
        print(f"[Agent Tool] Searching DDG for: {query}")
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=8))
            if not results:
                return "No search results found."
            formatted = []
            for r in results:
                formatted.append(f"Title: {r.get('title')}\nURL: {r.get('href')}\nSnippet: {r.get('body')}\n---")
            return "\n".join(formatted)
    except Exception as e:
        return f"Error performing search: {e}"

def fetch_webpage_content(url: str) -> str:
    """Fetches the text content of a webpage to extract detailed job requirements.
    
    Args:
        url: The absolute URL of the webpage to fetch.
    """
    try:
        print(f"[Agent Tool] Fetching URL: {url}")
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code >= 400:
            return f"Failed to fetch content, status: {resp.status_code}"
        
        soup = BeautifulSoup(resp.text, "html.parser")
        # Remove scripts, styles, headers, footers
        for element in soup(["script", "style", "meta", "noscript", "header", "footer", "nav"]):
            element.decompose()
        
        text = soup.get_text(separator=" ")
        lines = (line.strip() for line in text.splitlines())
        chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
        text = "\n".join(chunk for chunk in chunks if chunk)
        # Limit to 4000 characters to avoid context bloat
        return text[:4000]
    except Exception as e:
        return f"Error fetching webpage: {e}"

# Running the Agent with real-time thought logs
async def run_job_finder_agent(query: str, log_callback=None):
    """Initializes and executes the job finder agent using the Google Antigravity SDK.
    
    Args:
        query: Search criteria, e.g. "C2C Data Engineer"
        log_callback: Async function to stream thoughts/logs to (receives strings)
    """
    # Verify API key
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        err_msg = "Error: GEMINI_API_KEY is not set in your environment or .env file."
        if log_callback:
            await log_callback(f"{err_msg}\n")
        raise ValueError(err_msg)

    from google.antigravity.hooks import hooks

    # Local hooks to capture tool calls and stream them to the log callback
    @hooks.pre_tool_call_decide
    async def pre_tool_hook(data: types.ToolCall) -> types.HookResult:
        msg = f"[Tool Call] Running tool '{data.name}' with arguments: {data.arguments}\n"
        if log_callback:
            await log_callback(msg)
        return types.HookResult(allow=True)

    @hooks.post_tool_call
    async def post_tool_hook(data):
        msg = f"[Tool Complete] Finished running tool.\n"
        if log_callback:
            await log_callback(msg)

    # Agent config
    config = LocalAgentConfig(
        tools=[web_search, fetch_webpage_content],
        hooks=[pre_tool_hook, post_tool_hook],
        response_schema=JobList,
        system_instructions=(
            "You are a professional Job Finder agent specializing in finding C2C (Corp-to-Corp) "
            "Data Engineer roles. You must use the `web_search` tool to search for jobs on portals "
            "like LinkedIn, Indeed, Dice, and other tech job boards. Analyze search results, fetch "
            "specific details using `fetch_webpage_content` where necessary, and extract structured jobs. "
            "Only return jobs that match the C2C criteria or where C2C/Corp-to-Corp is explicitly mentioned "
            "or very likely. Highlight the C2C viability in the structured response."
        )
    )

    if log_callback:
        await log_callback(f"[Agent] Starting Job Search for query: '{query}'...\n")

    async with Agent(config) as agent:
        prompt = (
            f"Search for and compile a list of at least 5 to 10 C2C Data Engineer job postings matching "
            f"the query '{query}'. Use search queries like 'C2C Data Engineer jobs', 'Corp-to-Corp Data Engineer', "
            f"or 'Contract Data Engineer C2C'. Filter out jobs that are strictly W2 or do not allow contract terms."
        )
        
        response = await agent.chat(prompt)
        
        # Stream thoughts
        async for thought in response.thoughts:
            if log_callback:
                await log_callback(thought)
        
        # Get structured output
        data = await response.structured_output()
        
        if log_callback:
            await log_callback("\n[Agent] Search complete. Parsing results...\n")
            
        return data
