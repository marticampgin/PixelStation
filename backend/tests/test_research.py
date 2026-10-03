import asyncio

import pytest

from pixel_station.orchestration import Plan, PlanStep, Tool, ToolRegistry
from pixel_station.research import run_research, validate_research_plan


def plan():
    return Plan(
        steps=[
            PlanStep(id="s1", tool="web_search", args={"query": "first"}),
            PlanStep(id="s2", tool="web_search", args={"query": "second"}),
            PlanStep(id="f1", tool="web_fetch", args_from="s1", depends_on=["s1"]),
        ]
    )


async def test_dag_parallel_searches_dependency_evidence_and_budget():
    registry = ToolRegistry()
    both_started = asyncio.Event()
    started = []
    fetched = []

    async def search(query):
        started.append(query)
        if len(started) == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), 1)
        return [{"url": "https://example.com/" + query, "title": query, "snippet": "search result"}]

    async def fetch(url):
        assert both_started.is_set()
        fetched.append(url)
        return {"url": url, "title": "Article", "text": "Fetched actual text"}

    registry.register(
        Tool(
            "web_search",
            "Search",
            "Search",
            "web",
            {"type": "object", "required": ["query"], "properties": {"query": {"type": "string"}}},
            search,
        )
    )
    registry.register(
        Tool(
            "web_fetch",
            "Fetch",
            "Fetch",
            "web",
            {"type": "object", "required": ["url"], "properties": {"url": {"type": "string"}}},
            fetch,
        )
    )
    result = await run_research(plan(), registry, 3)
    assert fetched == ["https://example.com/first"]
    assert result["tool_steps"]["total"] == result["tool_steps"]["budget"] == 3
    assert any(source["fetched"] for source in result["sources"])
    assert "Fetched actual text" in result["content"]


def test_research_rejects_model_invented_urls_tools_and_excess_steps():
    with pytest.raises(ValueError):
        validate_research_plan(plan(), 2)
    bad = plan()
    bad.steps[-1].args = {"url": "https://invented.example/"}
    with pytest.raises(ValueError):
        validate_research_plan(bad, 3)
    bad = plan()
    bad.steps[-1].tool = "gmail_send"
    with pytest.raises(ValueError):
        validate_research_plan(bad, 3)
    bad = plan()
    bad.steps[-1].depends_on = []
    with pytest.raises(ValueError):
        validate_research_plan(bad, 3)


async def test_missing_results_skip_fetch_and_keep_budget_truthful():
    registry = ToolRegistry()

    async def search(query):
        return (
            []
            if query == "first"
            else [{"url": "https://example.com/second", "title": "Second", "snippet": "Evidence"}]
        )

    async def fetch(url):
        raise AssertionError("No source available; fetch must be skipped")

    registry.register(Tool("web_search", "Search", "Search", "web", {}, search))
    registry.register(Tool("web_fetch", "Fetch", "Fetch", "web", {}, fetch))
    result = await run_research(plan(), registry, 3)
    assert result["tool_steps"] == {"search": 2, "fetch": 0, "total": 2, "budget": 3}
    assert result["errors"]


async def test_fetched_source_survives_more_than_eight_search_results():
    registry = ToolRegistry()

    async def search(query):
        return [
            {
                "url": f"https://example.com/{query}/{index}",
                "title": f"{query} {index}",
                "snippet": "Snippet",
            }
            for index in range(6)
        ]

    async def fetch(url):
        return {"url": url, "title": "Fetched article", "text": "Essential full article evidence"}

    registry.register(Tool("web_search", "Search", "Search", "web", {}, search))
    registry.register(Tool("web_fetch", "Fetch", "Fetch", "web", {}, fetch))
    result = await run_research(plan(), registry, 3)
    assert len(result["sources"]) == 8
    assert result["sources"][0]["fetched"] is True
    assert "Essential full article evidence" in result["content"]
