# GPTLink

GPTLink is self-hosted infrastructure for policy-enforced operations on an
outbound-connected Agent. The Gateway never opens an inbound connection to a
controlled machine.

## Development

Use Python 3.12 or newer. Install the project with its development tools:

```bash
python -m pip install -e '.[dev]'
pytest
ruff check
```

Copy `.env.example` to `.env` only for local development. Production requires
an HTTPS or WSS Gateway URL and Agents must use explicitly configured roots.

The public executables are `gptlink` and `gptlink-agent`; their operational
commands are added as the Gateway and Agent vertical slices are implemented.
