# GPTLink

GPTLink is self-hosted infrastructure for policy-enforced operations on an
outbound-connected Agent. The Gateway never opens an inbound connection to a
controlled machine.

## Development

Use Python 3.12 or newer. Install the project with its development tools:

```bash
python -m pip install -e '.[dev]'
pytest -q
ruff check src tests
```

Copy `.env.example` to `.env` only for local development. Production requires
an HTTPS or WSS Gateway URL and Agents must use explicitly configured roots.

Start the Gateway and pair a development Agent:

```bash
gptlink db init
gptlink gateway run
# in another terminal
gptlink pairing create
gptlink-agent pair --gateway http://127.0.0.1:8000 --code XXXX-XXXX-XXXX
gptlink-agent run
gptlink devices list
```

The Agent always opens the outbound WebSocket. The current Linux development
slice includes pairing, heartbeat/reconnect, canonical filesystem sandboxing,
and bounded Bash jobs. MCP, Git/process tools, Docker and Windows executors
remain subsequent MVP phases; see [architecture](docs/architecture.md),
[security](docs/security.md), and [deployment](docs/deployment.md).
