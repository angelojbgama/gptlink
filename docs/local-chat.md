# Local chat mode

## Architecture

`gptlink local --chat` is independent of GPTLink's remote Gateway path:

```text
ChatGPT Web
    <-> ChatGPT-Web2API (external process, loopback HTTP)
    <-> ChatBackend / bounded action loop
    <-> LocalRuntime
    <-> existing AgentDispatcher and executors
    <-> frozen current-directory workspace
```

`LocalRuntime` reuses `SandboxPaths`, `FilesystemOperations`, `GitReadService`,
the platform process reader, `JobManager`, and the existing Windows/Linux shell
executors. It creates only an ephemeral in-memory envelope identity required by
the dispatcher; it does not create or persist a remote Device. Gateway, lease,
pairing, WSS, and remote permission behavior are unchanged.

## Separate environments

ChatGPT-Web2API is optional, external, and unofficial. Its browser integration
can change independently of GPTLink. GPTLink never imports it and exchanges no
browser cookies, ChatGPT tokens, or authorization headers with it.

Use separate virtual environments because the current packages have incompatible
MCP requirements:

```text
GPTLink:             mcp >=2.2,<3
ChatGPT-Web2API 0.2: mcp >=1.26,<2.0
```

Example PowerShell setup:

```powershell
py -3.12 -m venv C:\venvs\chatgpt-web2api
C:\venvs\chatgpt-web2api\Scripts\python -m pip install chatgpt-web2api
C:\venvs\chatgpt-web2api\Scripts\chatgpt-web2api --port 8081
```

Complete ChatGPT login in the browser opened by that service. GPTLink does not
start Chrome or manage the Web2API process.

## Starting GPTLink local

Change to the directory that should become the workspace and attach an existing
conversation:

```powershell
cd "C:\Users\Angelo\Documents\projetos\Undre världen"
gptlink local --chat --conversation UUID
```

Configuration equivalents are:

```text
GPTLINK_CHAT_BACKEND=web2api
GPTLINK_WEB2API_URL=http://127.0.0.1:8081
```

The CLI also accepts `--chat-backend web2api` and `--chat-url`. Only plain HTTP
loopback hosts (`127.0.0.1`, `localhost`, and `::1`) are accepted in this MVP.
Credentials in URLs, remote hosts, URL paths, queries, and fragments are
rejected.

If health checking fails, start the external service first:

```text
Chat backend unavailable.

Start ChatGPT-Web2API first:

chatgpt-web2api --port 8081
```

## Selecting an existing conversation

The backend abstraction supports listing and retrieving conversations. The
verified ChatGPT-Web2API 0.2.0 REST server, however, exposes only `/health`,
`/v1/models`, `/v1/projects`, and `POST /v1/chat/completions`. Its conversation
list/history operations exist only in its separate MCP server, not on the REST
port. GPTLink therefore does not invent undocumented REST routes:

- `--conversation UUID` attaches directly;
- without the option, 0.2.0 asks for a UUID interactively;
- a backend version that implements REST listing can present recent chats;
- GPTLink cannot preflight existence with 0.2.0, but rejects a completion that
  returns a different `conversation_id`;
- every action result is sent back with the supplied `conversation_id`.

## Workspace sandbox and initial context

At startup, `Path.cwd()` is resolved, canonicalized, validated as a non-root
directory, and frozen for the process. Relative paths are joined to that root
and then passed through the existing canonical sandbox. Parent escapes,
absolute paths outside the root, UNC paths outside it, symlinks, and Windows
junctions resolving outside it are rejected. Command working directories use
the same resolution.

The initial message contains bounded operational context only: workspace name
and path, a bounded top-level listing, small recognized README/AGENTS/config
files, and bounded Git status when available. GPTLink does not upload or walk
the entire directory. Further content is read only when the model emits a valid
action request.

## Action protocol

Free-form model text is never executed. One response may contain at most one
strict block:

```xml
<gptlink_action>
{"id":"action-123","tool":"filesystem_read","arguments":{"path":"README.md"}}
</gptlink_action>
```

GPTLink validates the tags, JSON object, ID, known tool, exact argument schema,
extra fields, permission, approval, and sandbox before execution. Results are
correlated and JSON-escaped so file content cannot close the XML block:

```xml
<gptlink_result id="action-123">
{"ok":true,"result":{"content":"..."}}
</gptlink_result>
```

The model must wait for a result before claiming success. The action loop stops
normally on a response without an action and stops forcibly after 20 actions by
default (`GPTLINK_LOCAL_MAX_ACTIONS_PER_TURN`). Duplicate action IDs are not
executed twice in one turn.

## Permissions and approval

Default `READ_ONLY` permits:

```text
filesystem_list, filesystem_read, filesystem_search
git_status, git_diff, process_list
command_status, command_output, command_cancel
```

`filesystem_write` and `command_start` are blocked before prompting. With
`--write`, they become available but still display the conversation, workspace,
action, target path or full command, shell, and working directory. `Y` approves
once, `A` approves that tool only for the current process, and `N` denies it.
There is no persistent or global approval in this MVP.

Commands must select an explicit shell. Windows uses the existing PowerShell or
CMD executor and Job Objects; Linux uses the existing Bash executor. WSL remains
conditional on the existing Agent capabilities and is not enabled by the local
MVP assembly.

No delete, reboot, shutdown, process-kill, Git mutation, disk formatting, or
registry tool is exposed.

## Bounds and audit

Existing limits bound file reads/writes, searches, directory/process results,
Git output, concurrent jobs, timeouts, and command output. Collection responses
include `truncated`; command status includes `output_truncated`.

Local audit events default to `~/.gptlink/local-audit.jsonl` and contain only:

```text
timestamp, conversation_id, workspace, action, result status,
duration_ms, request_id/action_id
```

Arguments, commands, file contents, chat messages, output, cookies, passwords,
authorization headers, Web2API credentials, browser state, and Device tokens are
not logged.

## Limitations

- ChatGPT-Web2API 0.2.0 cannot list or fetch conversations over its REST port.
- Browser-originated message watching is not implemented; this controlled MVP
  reads user messages from the attached terminal session.
- The backend is unofficial and UI changes can break it independently.
- Approval grants last only for the current GPTLink process.
- There is no automatic Chrome/Web2API lifecycle management.
