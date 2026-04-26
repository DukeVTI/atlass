import os
import httpx
import logging
from bs4 import BeautifulSoup
from typing import Any
from tools.base import Tool

logger = logging.getLogger("atlas.tools.web_search")

SERPAPI_KEY = os.getenv("SERPAPI_KEY")

class WebSearchTool(Tool):
    name = "web_search"
    description = "Searches the web for real-time information or specific queries using SerpAPI and scrapes the top results for context."
    is_destructive = False  # Research is safe

    schema = {
        "name": "web_search",
        "description": "Perform live web search to answer factual queries, find news, or research a topic.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The specific query to search for."
                }
            },
            "required": ["query"]
        }
    }

    async def run(self, query: str, **kwargs) -> Any:
        if not SERPAPI_KEY:
            return "SYSTEM WARNING: SERPAPI_KEY is missing from environment variables."

        logger.info(f"Executing web search for: '{query}'")

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    "https://serpapi.com/search",
                    params={
                        "q": query,
                        "engine": "google"
                    },
                    headers={
                        "X-API-KEY": SERPAPI_KEY
                    }
                )
                response.raise_for_status()
                data = response.json()
        except Exception as exc:
            logger.error(f"Failed to query SerpAPI: {exc}")
            return f"Error: The search provider failed to respond. {exc}"

        organic_results = data.get("organic_results", [])
        if not organic_results:
            return f"No useful web search results found for query: '{query}'"

        # Prepare a concise digest of top 3 results
        digest = [f"Search Results for '{query}':\n"]
        
        for idx, result in enumerate(organic_results[:3]):
            title = result.get("title", 'Unknown Title')
            link = result.get("link", '')
            snippet = result.get("snippet", '')
            
            digest.append(f"{idx + 1}. {title}\nURL: {link}\nSnippet: {snippet}\n")

            # Basic HTTPX Scraping to attempt fetching deeper article context
            if link:
                try:
                    async with httpx.AsyncClient(timeout=5.0) as scraper:
                        async with scraper.stream("GET", link) as dl_response:
                            dl_response.raise_for_status()
                            content_chunks = []
                            total_bytes = 0
                            async for chunk in dl_response.aiter_bytes():
                                content_chunks.append(chunk)
                                total_bytes += len(chunk)
                                if total_bytes > 2 * 1024 * 1024:  # 2MB limit
                                    break
                                    
                        page_text = b"".join(content_chunks).decode('utf-8', errors='ignore')
                        soup = BeautifulSoup(page_text, "html.parser")
                        
                        # Extract paragraphs and truncate to roughly 2000 chars safely
                        paragraphs = " ".join([p.text for p in soup.find_all("p")])
                        safe_content = paragraphs[:2000]
                        if len(paragraphs) > 2000:
                            # Ensure we don't brutally slice words by snapping to nearest period
                            last_period = safe_content.rfind(".")
                            if last_period > 0:
                                safe_content = safe_content[:last_period + 1] + "..."
                            else:
                                safe_content += "..."
                                
                        if safe_content.strip():
                            digest.append(f"Scraped Article Context:\n{safe_content}\n")
                except Exception as inner_exc:
                    logger.debug(f"Failed to scrape deeper context from {link}: {inner_exc}")
                    digest.append("Scraped Article Context: Extraction failed or blocked by site.\n")

        return "\n".join(digest)
