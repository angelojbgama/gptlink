# Deployment do GPTLink

## Desenvolvimento local no VPS

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e '.[dev]'
GPTLINK_DATABASE_URL=sqlite+aiosqlite:////srv/gptlink/gptlink.db \
  .venv/bin/gptlink gateway run
```

Em outro terminal, crie um código e faça pairing:

```bash
GPTLINK_DATABASE_URL=sqlite+aiosqlite:////srv/gptlink/gptlink.db \
  .venv/bin/gptlink pairing create
.venv/bin/gptlink-agent pair --gateway http://127.0.0.1:8000 \
  --code XXXX-XXXX-XXXX --path /srv/gptlink/agent.json
.venv/bin/gptlink-agent run --path /srv/gptlink/agent.json
.venv/bin/gptlink devices list
```

O Agent inicia a conexão; o computador controlado não precisa de IP público
nem de porta inbound. Configure `GPTLINK_AGENT_ROOTS` apenas com diretórios
específicos e nunca com `/` ou `C:\\`.

## Docker e banco

Defina secrets apenas no ambiente do operador e valide a configuração:

```bash
export GPTLINK_MCP_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
export GPTLINK_PUBLIC_URL=https://gptlink.example.com
docker compose config
docker compose build
docker compose up -d
docker compose ps
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/ready
```

O container `gptlink-gateway` roda como usuário sem privilégios, persiste apenas
`/data` e publica a porta somente em loopback. Não há montagem de Docker socket,
home, chaves SSH ou filesystem do host.

## Cloudflare Tunnel

Crie um único tunnel persistente e associe um hostname estável, por exemplo
`gptlink.example.com`, apontando para `http://127.0.0.1:8000`. O tunnel é
outbound e independente dos Agents. Não gere tunnel por conexão de PC, não
coloque token no Git e não altere DNS, firewall ou SSH automaticamente.

Em produção defina `GPTLINK_ENV=production`, use `https://` para MCP e `wss://`
para Agents. O endpoint local não precisa escutar em uma porta pública quando o
Tunnel estiver ativo.

Exemplo conceitual de ingress do `cloudflared` (não contém credenciais):

```yaml
ingress:
  - hostname: gptlink.example.com
    service: http://127.0.0.1:8000
  - service: http_status:404
```

O resultado público é `https://gptlink.example.com/mcp` para MCP e
`wss://gptlink.example.com/agent/ws` para Agents. O Tunnel não substitui bearer
MCP, autenticação do dispositivo, autorização/lease/replay no Gateway nem a
policy local do Agent. DNS e criação do tunnel permanecem ações manuais do
operador.
