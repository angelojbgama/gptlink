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

O `Dockerfile`/Compose será adicionado quando a imagem do Gateway estiver
fechada. O container deverá rodar sem root, montar somente o volume de dados e
expor healthcheck; Docker não está instalado no VPS de desenvolvimento atual.

## Cloudflare Tunnel (futuro)

Crie um único tunnel persistente e associe um hostname estável, por exemplo
`gptlink.example.com`, apontando para `http://127.0.0.1:8000`. O tunnel é
outbound e independente dos Agents. Não gere tunnel por conexão de PC, não
coloque token no Git e não altere DNS, firewall ou SSH automaticamente.

Em produção defina `GPTLINK_ENV=production`, use `https://` para MCP e `wss://`
para Agents. O endpoint local não precisa escutar em uma porta pública quando o
Tunnel estiver ativo.
