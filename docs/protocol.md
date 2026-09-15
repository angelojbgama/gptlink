# GPTLink protocol v1

GPTLink Agent and Gateway messages are strict JSON envelopes. Every envelope
must explicitly contain the real integer `protocol_version: 1`, a dotted
`type`, UUID `request_id` and `device_id`, and a typed `payload`. No envelope
inherits a default protocol version; booleans, floats, strings, and other
versions are rejected. Command messages that refer to an allocated job also
carry a UUID `job_id`.

The v1 codec accepts exactly these wire types:

| Type | Direction | Typed payload |
| --- | --- | --- |
| `agent.hello` | Agent → Gateway | token, `versions: [1]`, capabilities |
| `agent.welcome` | Gateway → Agent | selected version `1` |
| `heartbeat.ping` | Agent → Gateway | timezone-aware timestamp |
| `heartbeat.pong` | Gateway → Agent | timezone-aware timestamp |
| `command.start` | Gateway → Agent | command, explicit shell, cwd |
| `command.started` | Agent → Gateway | process ID; envelope has `job_id` |
| `command.output` | Agent → Gateway | non-negative `sequence_number`, stream, timezone-aware timestamp, data; envelope has `job_id` |
| `command.finished` | Agent → Gateway | terminal status and optional exit code; envelope has `job_id` |
| `command.cancel` | Gateway → Agent | cancellation reason; envelope has `job_id` |
| `filesystem.list` | Gateway → Agent | path |
| `filesystem.read` | Gateway → Agent | path |
| `filesystem.write` | Gateway → Agent | path and data |
| `filesystem.search` | Gateway → Agent | path and query |
| `git.status` | Gateway → Agent | repository path |
| `git.diff` | Gateway → Agent | repository path and revision |
| `process.list` | Gateway → Agent | empty |
| `error` | Either | code, message, retryable flag |

`encode_message(message)` serializes an already validated envelope.
`decode_message(data)` validates JSON through a discriminated `type` union.
Unknown types, missing/invalid IDs, undocumented fields, missing versions, and
invalid payload values are rejected with Pydantic validation errors. Envelopes
and payloads are immutable after construction.
