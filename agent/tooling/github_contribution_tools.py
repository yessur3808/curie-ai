"""Registered tools for Curie's preview-first GitHub contribution workflow."""

from __future__ import annotations

import asyncio
from typing import Any, Mapping

from agent.tooling.contracts import ToolContext, ToolResult
from services.action_previews import render_preview
from services.open_source_contributions import open_source_contributions


class GitHubContributionScanTool:
    name = "github_contribution_scan"
    read_only = True

    async def execute(
        self, params: Mapping[str, Any], context: ToolContext
    ) -> ToolResult:
        preview = await asyncio.to_thread(
            open_source_contributions.prepare,
            context.internal_id,
            dict(context.profile),
            topic=str(params.get("topic") or ""),
        )
        text = (
            f"{render_preview(preview)}\n\n"
            "Nothing has been published. If you want this contribution sent, reply "
            f"`/opensource publish {preview['id']}`. I will create a fresh external-action approval before pushing."
        )
        return ToolResult(
            text,
            {
                "proposal_id": preview["id"],
                "preview_hash": preview["payload_hash"],
                "status": "awaiting_review",
            },
            source="github",
        )


class GitHubContributionPublishTool:
    name = "github_contribution_publish"
    read_only = False

    async def execute(
        self, params: Mapping[str, Any], context: ToolContext
    ) -> ToolResult:
        result = await asyncio.to_thread(
            open_source_contributions.publish,
            context.internal_id,
            str(params.get("preview_id") or ""),
            str(params.get("preview_hash") or ""),
        )
        if result["status"] == "pull_request_created":
            text = f"Published the reviewed branch and opened the pull request: {result['url']}"
        elif result["status"] == "suggestion_only":
            text = f"{result['suggestion']}\n\nRelated issue: {result['url']}"
        else:
            text = f"{result['suggestion']}\n\nBranch: {result['url']}"
        return ToolResult(text, result, source="github")
