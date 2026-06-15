import os
import json
import asyncio
import re
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions
from claude_agent_sdk.types import McpStdioServerConfig, ToolUseBlock, ThinkingBlock, TextBlock, AssistantMessage

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

# Running the Agent with real-time thought logs
async def run_job_finder_agent(query: str, log_callback=None, session_id=None, is_resume=False):
    """Initializes and executes the job finder agent using the claude-agent-sdk.
    
    Args:
        query: Search criteria, e.g. "C2C Data Engineer"
        log_callback: Async function to stream thoughts/logs to (receives strings)
        session_id: A valid UUID string.
        is_resume: Whether the session_id is for an existing session to resume.
    """
    
    if log_callback:
        await log_callback(f"[Agent] Starting Job Search for query: '{query}'...\n")

    # MCP Server configuration
    mcp_servers = {
        "puppeteer": McpStdioServerConfig(
            command="npx",
            args=["-y", "@modelcontextprotocol/server-puppeteer"],
        ),
        "job_finder_tools": McpStdioServerConfig(
            command="uv",
            args=["run", "python", os.path.join(os.path.dirname(__file__), "mcp_server.py")],
        )
    }

    # Agent config
    import uuid
    options = ClaudeAgentOptions(
        session_id=None if is_resume else (session_id or str(uuid.uuid4())),
        resume=session_id if is_resume else None,
        mcp_servers=mcp_servers,
        output_format=JobList.model_json_schema(),
        permission_mode="bypassPermissions",
        system_prompt=(
            "You are a professional Job Finder agent specializing in finding C2C (Corp-to-Corp) "
            "Data Engineer roles. You have access to custom Python search tools via the `job_finder_tools` MCP server "
            "(`web_search`, `fetch_webpage_content`) and a headless browser via the `puppeteer` MCP server. "
            "Use the `web_search` tool for quick web searches, but if you need to bypass blocks or interact with complex sites, "
            "use the MCP browser tools (e.g., puppeteer_navigate) to search for jobs specifically on LinkedIn, Monster, and Dice. "
            "Analyze results and extract structured jobs. Only return jobs that are recently posted and match the C2C criteria or where C2C/Corp-to-Corp is "
            "explicitly mentioned or very likely. Highlight the C2C viability in the structured response. "
            "CRITICAL: You MUST output your final answer as valid JSON matching the schema provided. Do not return conversational markdown."
        )
    )

    prompt = (
        f"Search for and compile a list of as many C2C Data Engineer job postings as possible (aim for at least 20 to 30) matching "
        f"the query '{query}'. Actively search specifically across LinkedIn, Monster, and Dice. "
        f"Ensure that you ONLY include jobs that were recently posted (e.g., within the last 7 to 14 days). "
        f"Use targeted search queries like 'C2C Data Engineer site:linkedin.com', "
        f"'Contract Data Engineer C2C site:monster.com', or 'Data Engineer C2C site:dice.com'. "
        f"Filter out jobs that are strictly W2 or do not allow contract terms. "
        f"\n\nCRITICAL: When you are done, you MUST return the final list of jobs as ONLY a valid JSON object wrapped in ```json ... ``` blocks. "
        f"The JSON object must have a single key 'jobs' containing a list of job objects. Each job object must match this schema:\n"
        f"{json.dumps(JobList.model_json_schema())}\n"
        f"Do not include any other markdown tables or conversational text in your final response."
    )
        
    async with ClaudeSDKClient(options) as client:
        # We start the query
        await client.query(prompt)
        
        data = None
        # Process the response stream manually to extract tool execution logs and thinking
        async for msg in client.receive_response():
            if log_callback:
                # If it's an assistant message, we can get thought process and tool execution
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, ThinkingBlock):
                            # Emit thought
                            await log_callback(block.thinking)
                        elif isinstance(block, ToolUseBlock):
                            # Emit tool use
                            tool_msg = f"\n[Tool Call] Running tool '{block.name}' with arguments: {block.input}\n"
                            await log_callback(tool_msg)
                        elif isinstance(block, TextBlock):
                            await log_callback(block.text)

            # When the generator completes the task, it outputs a ResultMessage
            if type(msg).__name__ == "ResultMessage":
                if hasattr(msg, "structured_output") and msg.structured_output:
                    data = msg.structured_output
                elif hasattr(msg, "result") and msg.result:
                    # Fallback to parse json directly
                    try:
                        # First try to find markdown json block
                        match = re.search(r'```json\s*(\{.*?\})\s*```', msg.result, re.DOTALL | re.IGNORECASE)
                        if not match:
                            # Fallback to finding just the outer brackets
                            match = re.search(r'(\{.*\})', msg.result, re.DOTALL)
                        if match:
                            data = json.loads(match.group(1))
                    except Exception as e:
                        print("Failed to parse JSON fallback:", e)
                break

        if log_callback:
            await log_callback("\n[Agent] Search complete. Parsing results...\n")
            
        return data
