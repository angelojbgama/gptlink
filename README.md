# GPTLink

GPTLink provides policy-enforced computer operations in two independent modes.

## Remote mode

The remote architecture keeps the existing outbound-only connection model:

```text
MCP client -> Gateway VPS -> WSS -> Agent -> Computer
```

The Gateway never opens an inbound connection to a controlled machine. Start a
development Gateway and pair an Agent with:

```bash
gptlink db init
gptlink gateway run
# in another terminal
gptlink pairing create
gptlink-agent pair --gateway http://127.0.0.1:8000 --code XXXX-XXXX-XXXX
gptlink-agent run
gptlink devices list
```

Production requires an HTTPS or WSS Gateway URL. Agents can access only their
explicitly configured filesystem roots and capabilities.

## Local chat mode

Local mode attaches an existing ChatGPT conversation to the current directory
without a Gateway, VPS, WebSocket, pairing flow, or remote Device record:

```text
ChatGPT Web <-> ChatGPT-Web2API <-> GPTLink chat bridge
                                      |
                                      v
                             local Agent operations
                                      |
                                      v
                              current directory
```

From the directory to expose:

```powershell
gptlink local --chat --conversation YOUR-CONVERSATION-UUID
```

The current directory is resolved and frozen as the only workspace root for the
process. Local mode is `READ_ONLY` by default. Enable mutations with `--write`;
every filesystem write and command start still requires an explicit local
approval (`Y` once, `A` for this process, or `N` deny).

```powershell
gptlink local --chat --read-only --conversation UUID
gptlink local --chat --write --conversation UUID
gptlink local --chat --chat-url http://127.0.0.1:8081 --conversation UUID
gptlink local --help
```

See [Local chat mode](docs/local-chat.md) for setup, security, protocol, and
current backend limitations.

## Chat backend is external and optional

ChatGPT-Web2API is an optional, unofficial external integration. It is not
bundled with GPTLink, and GPTLink communicates with it only over loopback HTTP.
GPTLink does not import or depend on the `chatgpt-web2api` Python package.

Install it in a separate virtual environment. Do not install it in GPTLink's
environment: GPTLink requires `mcp>=2.2,<3`, while ChatGPT-Web2API 0.2.0 requires
`mcp>=1.26,<2.0`. Installing both into one environment makes pip replace one
project's compatible MCP version with the other's.

## Development

Use Python 3.12 or newer:

```bash
python -m pip install -e '.[dev]'
python -m ruff check src tests
python -m ruff format --check src tests
python -m mypy src
python -m pytest -q
python -m pip check
```

Copy `.env.example` to `.env` only for local development. More documentation is
available in [architecture](docs/architecture.md), [security](docs/security.md),
and [deployment](docs/deployment.md).
