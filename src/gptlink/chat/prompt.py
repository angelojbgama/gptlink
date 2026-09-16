"""Compact session prompt and bounded operational workspace context."""

from __future__ import annotations

import json
from pathlib import Path

from gptlink.chat.protocol import ChatAction, PathArguments
from gptlink.common.types import PermissionLevel
from gptlink.local.runtime import LocalActionError, LocalRuntime

_CONTEXT_FILES = {
    "README.md",
    "README.rst",
    "README.txt",
    "AGENTS.md",
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "go.mod",
}
_MAX_CONTEXT_FILE_BYTES = 32_768


async def collect_workspace_context(
    runtime: LocalRuntime,
    *,
    conversation_id: str,
    conversation_title: str,
) -> dict[str, object]:
    """Collect only a top-level listing, small key files, and bounded Git status."""
    context: dict[str, object] = {
        "workspace": str(runtime.workspace.root),
        "name": runtime.workspace.root.name,
    }
    listing_action = ChatAction("context-list", "filesystem_list", PathArguments(path="."))
    try:
        listing_result = await runtime.execute(
            listing_action,
            conversation_id=conversation_id,
            conversation_title=conversation_title,
        )
    except LocalActionError:
        listing_result = {"items": [], "truncated": False}
    context["top_level"] = listing_result

    items = listing_result.get("items", []) if isinstance(listing_result, dict) else []
    files: dict[str, str] = {}
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if (
            name not in _CONTEXT_FILES
            or item.get("kind") != "file"
            or not isinstance(item.get("size"), int)
            or item["size"] > _MAX_CONTEXT_FILE_BYTES
        ):
            continue
        action = ChatAction(
            f"context-read-{len(files)}", "filesystem_read", PathArguments(path=name)
        )
        try:
            result = await runtime.execute(
                action,
                conversation_id=conversation_id,
                conversation_title=conversation_title,
            )
        except LocalActionError:
            continue
        if isinstance(result, dict) and isinstance(result.get("content"), str):
            files[str(name)] = result["content"]
    context["key_files"] = files

    try:
        git = await runtime.execute(
            ChatAction("context-git-status", "git_status", PathArguments(path=".")),
            conversation_id=conversation_id,
            conversation_title=conversation_title,
        )
    except LocalActionError:
        pass
    else:
        context["git_status"] = git
    return context


def build_session_prompt(
    root: Path,
    permission: PermissionLevel,
    context: dict[str, object],
) -> str:
    tools = [
        "filesystem_list",
        "filesystem_read",
        "filesystem_search",
        "git_status",
        "git_diff",
        "process_list",
        "command_status",
        "command_output",
        "command_cancel",
    ]
    if permission is PermissionLevel.READ_WRITE:
        tools.extend(["filesystem_write", "command_start"])
    return (
        "GPTLink local workspace is active.\n\n"
        f"Workspace root:\n{root}\n\n"
        f"Permission mode: {permission.value}\n\n"
        "Request at most one operation per response using exactly:\n"
        '<gptlink_action>\n{"id":"action-unique","tool":"filesystem_list",'
        '"arguments":{"path":"."}}\n</gptlink_action>\n\n'
        f"Available tools: {', '.join(tools)}\n"
        "Do not invent tool results. Do not claim success until a correlated "
        "gptlink_result is returned. Paths must stay inside the workspace. "
        "Prefer reads before writes or commands. Write and command requests require "
        "local operator approval.\n\n"
        "Bounded initial workspace context:\n" + json.dumps(context, ensure_ascii=False, indent=2)
    )
