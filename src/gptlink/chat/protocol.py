"""Strict, non-executable action protocol embedded in chat text."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError

ToolName = Literal[
    "filesystem_list",
    "filesystem_read",
    "filesystem_search",
    "filesystem_write",
    "git_status",
    "git_diff",
    "process_list",
    "command_start",
    "command_status",
    "command_output",
    "command_cancel",
]

_BLOCK = re.compile(r"<gptlink_action>\s*(.*?)\s*</gptlink_action>", re.DOTALL)
_ACTION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")


class ActionProtocolError(ValueError):
    """A model-produced action block is malformed or unsupported."""


class _Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class PathArguments(_Arguments):
    path: str = Field(min_length=1, max_length=32_768)


class SearchArguments(PathArguments):
    query: str = Field(min_length=1, max_length=4_096)


class WriteArguments(PathArguments):
    data: str


class GitDiffArguments(PathArguments):
    revision: str | None = Field(default=None, min_length=1, max_length=200)


class EmptyArguments(_Arguments):
    pass


class CommandStartArguments(_Arguments):
    command: str = Field(min_length=1, max_length=32_768)
    shell: Literal["bash", "powershell", "cmd", "wsl"]
    cwd: str = Field(default=".", min_length=1, max_length=32_768)
    timeout: float = Field(default=30, gt=0, le=86_400, allow_inf_nan=False)


class JobArguments(_Arguments):
    job_id: str = Field(min_length=1, max_length=100)


class CommandOutputArguments(JobArguments):
    after_sequence: int = Field(default=-1, ge=-1)


class CommandCancelArguments(JobArguments):
    reason: str = Field(default="cancelled by local chat", min_length=1, max_length=500)


class _ActionEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    id: str = Field(min_length=1, max_length=200)
    tool: ToolName
    arguments: dict[str, object]


ActionArguments = (
    PathArguments
    | SearchArguments
    | WriteArguments
    | GitDiffArguments
    | EmptyArguments
    | CommandStartArguments
    | JobArguments
    | CommandOutputArguments
    | CommandCancelArguments
)


@dataclass(frozen=True)
class ChatAction:
    id: str
    tool: ToolName
    arguments: ActionArguments


_SCHEMAS: dict[str, type[_Arguments]] = {
    "filesystem_list": PathArguments,
    "filesystem_read": PathArguments,
    "filesystem_search": SearchArguments,
    "filesystem_write": WriteArguments,
    "git_status": PathArguments,
    "git_diff": GitDiffArguments,
    "process_list": EmptyArguments,
    "command_start": CommandStartArguments,
    "command_status": JobArguments,
    "command_output": CommandOutputArguments,
    "command_cancel": CommandCancelArguments,
}


def parse_action(content: str) -> ChatAction | None:
    """Return one validated action, no action, or reject an ambiguous block."""
    matches = _BLOCK.findall(content)
    tag_count = content.count("<gptlink_action>") + content.count("</gptlink_action>")
    if not matches:
        if tag_count or "<gptlink_action" in content or "</gptlink_action" in content:
            raise ActionProtocolError("malformed gptlink_action block")
        return None
    if len(matches) != 1 or tag_count != 2:
        raise ActionProtocolError("exactly one well-formed gptlink_action block is allowed")
    try:
        raw = json.loads(matches[0])
        envelope = _ActionEnvelope.model_validate(raw)
        if _ACTION_ID.fullmatch(envelope.id) is None:
            raise ActionProtocolError("action id contains unsupported characters")
        arguments = _SCHEMAS[envelope.tool].model_validate(envelope.arguments)
    except (json.JSONDecodeError, ValidationError, KeyError, TypeError) as error:
        raise ActionProtocolError("invalid gptlink_action payload") from error
    return ChatAction(envelope.id, envelope.tool, cast(ActionArguments, arguments))


def render_result(
    action_id: str,
    *,
    result: object | None = None,
    error_type: str | None = None,
    error_message: str | None = None,
) -> str:
    """Render injection-resistant JSON inside a correlated result block."""
    if _ACTION_ID.fullmatch(action_id) is None:
        raise ActionProtocolError("invalid result action id")
    payload: dict[str, object]
    if error_type is None:
        payload = {"ok": True, "result": result}
    else:
        payload = {
            "ok": False,
            "error": {"type": error_type, "message": error_message or "action failed"},
        }
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    encoded = encoded.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    return f'<gptlink_result id="{action_id}">\n{encoded}\n</gptlink_result>'
