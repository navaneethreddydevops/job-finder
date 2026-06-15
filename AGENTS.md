# C2C Job Finder Agent Documentation

This file documents the design, capabilities, and configurations of the AI Job Finder Agent developed using the **Google Antigravity SDK**.

## Overview

The Job Finder Agent is a specialized autonomous assistant that scours web portals for Corp-to-Corp (C2C) Data Engineer positions. It is designed to run asynchronously, utilize tools to perform searches and extract raw page data, evaluate findings against C2C criteria, and return structured JSON.

---

## Agent Configuration

The agent is instantiated locally using `LocalAgentConfig` with the following parameters:

* **Model**: Defaults to `claude-3-5-sonnet-latest` (provided by the Claude Agent SDK).
* **System Instructions**:
  > "You are a professional Job Finder agent specializing in finding C2C (Corp-to-Corp) Data Engineer roles. You must use the `web_search` tool to search for jobs specifically on LinkedIn, Monster, and Dice. Analyze search results, fetch specific details using `fetch_webpage_content` where necessary, and extract structured jobs. Only return jobs that are recently posted and match the C2C criteria or where C2C/Corp-to-Corp is explicitly mentioned or very likely. Highlight the C2C viability in the structured response. The goal is to pull as many relevant jobs as possible (aiming for at least 20-30)."

---

## Capabilities & Custom Tools

The agent is equipped with two custom Python functions registered as tools:

### 1. Web Search (`web_search`)
* **Purpose**: Query DuckDuckGo for search results.
* **Arguments**: `query: str`
* **Output**: Formatted titles, links, and snippet descriptions of the top 8 search hits.

### 2. Fetch Webpage Content (`fetch_webpage_content`)
* **Purpose**: Scrapes a specific target page URL, extracts visual text, strips unnecessary HTML boilerplate (scripts, headers, footers), and returns clean description content (capped at 4000 characters to prevent token overflow).
* **Arguments**: `url: str`
* **Output**: Cleaned raw text representation of the webpage.

---

## Lifecycle Event Hooks

To enable the live thought terminal inside the React UI, the agent registers two event hooks to capture execution stages:

* **Pre-Tool Call Hook (`pre_tool_hook`)**: Intercepts tool execution to log what tool is about to run and with what arguments.
* **Post-Tool Call Hook (`post_tool_hook`)**: Signals the client that tool execution has finished successfully.
* **Thought Stream Generator**: The server loops over `response.thoughts` to stream the agent's internal reasoning chunks directly to the UI through Server-Sent Events (SSE).

---

## Response Schema (Structured Output)

The agent is configured with `response_schema=JobList`, guaranteeing the response matches a strict JSON format matching the following Pydantic model:

```python
class JobItem(BaseModel):
    title: str               # The job title
    company: str             # The company name
    location: str            # Remote, City, State, or Hybrid
    url: str                 # The direct posting URL
    date_posted: str         # Found or posted timeline
    c2c_viability: str       # Confirmed C2C, Likely C2C, or Not Specified
    key_requirements: list   # List of technical skills
    contact_email: str       # Recruiter email (if found)
    contact_phone: str       # Recruiter phone (if found)
    source: str              # Portal source (e.g. LinkedIn, Dice)
    description: str         # C2C terms summary & description
```
