# GPTLink v0.1 — arquitetura

## Escopo e decisões

GPTLink é uma infraestrutura self-hosted, single-user no MVP, que permite a
clientes autorizados solicitar operações em computadores que executam um
Agent. O Agent sempre inicia a conexão outbound. O Gateway nunca abre uma
conexão de entrada no computador controlado.

O MVP será entregue verticalmente nesta ordem: Gateway e protocolo próprio,
Agent Linux de desenvolvimento, sandbox de filesystem, jobs, Git/processos,
adaptador MCP, container e, por fim, executores Windows. A ausência de Windows
no VPS não bloqueia nem valida os executores Windows.

## Fronteiras

```text
MCP client --Streamable HTTP--> MCP adapter --services--> Gateway domain
                                                        |
                                                        | GPTLink protocol v1
                                                        | authenticated WSS
                                                        v
                                                  outbound Agent
                                                        |
                               local policy + filesystem/executor/git/process
```

- `gptlink.mcp` traduz tools MCP para serviços do Gateway. Não contém política
  de negócio e não é importado pelo Agent.
- `gptlink.protocol` contém envelopes Pydantic versionados e codec JSON. Não
  depende de FastAPI, banco ou SDK MCP.
- `gptlink.gateway` autentica callers/devices, mantém conexões online e
  coordena requests, jobs, leases e auditoria.
- `gptlink.persistence` contém modelos e repositórios SQLAlchemy; nenhum outro
  pacote espalha SQL.
- `gptlink.agent` aplica a política local e despacha operações para adapters de
  plataforma. Uma autorização do Gateway não ignora a política do Agent.
- IDs persistentes (`device_id`, `job_id`, `request_id`, `lease_id`) carregam
  todo estado entre chamadas; MCP permanece conceitualmente stateless.

## Transporte interno

O Agent conecta a `/agent/ws` e apresenta `agent.hello` com `device_id`, token,
versões e capabilities. O token persistente possui 32 bytes aleatórios e só o
hash com salt é persistido no Gateway. Depois de autenticar e negociar a versão
1, o Gateway responde `agent.welcome`. Heartbeats atualizam `last_seen`; queda
ou limiar sem heartbeat marca o device `OFFLINE`. Reconexão usa backoff
exponencial com jitter e reutiliza a credencial, independentemente de IP/NAT.

O Gateway envia `agent.welcome` antes de publicar a conexão para dispatch.
Commit de presença e publicação são protegidos contra cancelamento: uma
operação interrompida aguarda o resultado e limpa a presença confirmada antes
de terminar. Conexões substituídas são fechadas sem manter lock durante I/O.
Um novo registro autenticado promove estados não revogados a `ONLINE`.
Heartbeats preservam `BUSY` e `DEGRADED`, atualizam somente `last_seen` nesses
estados e podem promover `OFFLINE` conectado a `ONLINE`; nunca alteram
`REVOKED` nem devices com `revoked_at` preenchido.

O monitor verifica o banco mesmo sem conexões. Falhas tornam `/ready` 503;
um ciclo completo saudável restaura 200. O shutdown sempre fecha registry e
banco, mesmo após falha do monitor. Se o banco estiver indisponível durante
shutdown, os sockets ainda fecham, mas a persistência de `OFFLINE` é best effort.

Cada envelope leva `protocol_version`, `type`, `request_id`, `device_id` e
`payload`. Requests mutáveis têm `request_id` único persistido/auditado; uma
repetição retorna conflito sem repetir o efeito. Respostas e eventos carregam o
mesmo request ID; jobs e chunks carregam também `job_id`.

## Autorização e política

O Gateway valida o bearer token do caller MCP, a permissão do device, a
capability e, para mutações, um lease válido. O Agent repete a validação de
capability/nível e valida caminhos e limites locais. `READ_ONLY`, `READ_WRITE`
e `FULL_ACCESS` são níveis centrais; `FULL_ACCESS` não desativa roots, limites,
timeouts nem operações negadas.

Pairing codes têm curta validade, tentativas limitadas e uso único. Devices
revogados não autenticam novamente. TLS é obrigatório quando
`GPTLINK_ENV=production`; HTTP/WS só é aceito em desenvolvimento explícito.

`PairingService.create_code()` retorna `code` e `expires_at`. O display code
tem três grupos (`H7K2-P9QM-4X8D`): o primeiro é um locator público; os últimos
oito símbolos são um segredo de 40 bits gerado por `secrets`, usando um alfabeto
de 32 símbolos sem `0`, `1`, `I` ou `O`. Apenas o locator e um hash Argon2id com
salt aleatório são persistidos; a biblioteca `argon2-cffi` usa seu perfil
padrão de 64 MiB. A validade padrão é 10 minutos e o limite é cinco tentativas.
`redeem(code, DeviceMetadata)` localiza pelo primeiro grupo e incrementa a
tentativa com um UPDATE condicional, antes da verificação, mantendo a
transação até persistir o novo device e consumir o código. Falhas de código
confirmam o débito antes de lançar erro; falhas de persistência revertem tudo.
Isso também serializa disputas entre conexões SQLite independentes.

Credenciais retornadas contêm `device_id` e `token`, emitido somente após o
commit; código e token são omitidos de `repr` e não são logados. O token é
gerado com 32 bytes aleatórios, codificado em base64url, e persistido como
SHA-256 com salt aleatório de 16 bytes. Autenticação por device ID compara
digests via `hmac.compare_digest` e rejeita tanto status `REVOKED` quanto
`revoked_at` preenchido. Metadados de pairing não aceitam identidade, token,
status ou nível de permissão fornecidos pelo Agent: novos devices iniciam
`OFFLINE` e `READ_ONLY`.

## Filesystem

Roots permitidos são explícitos e nunca incluem `/` ou `C:\\`. Cada operação
resolve o root e o alvo canonicamente. O alvo é aceito somente se permanece
dentro do root após resolução; pais de arquivos novos também são resolvidos.
Symlinks, junctions, traversal e namespaces Windows/WSL não são convertidos
automaticamente. Escrita usa arquivo temporário no mesmo diretório, flush,
`fsync` e `os.replace`. O MVP não oferece delete.

## Jobs

`command.start` cria um job e retorna rapidamente seu ID. O Agent inicia o
processo em grupo próprio, envia `command.started`, chunks incrementais com
sequência monotônica e `command.finished`. O Gateway persiste metadata e chunks
até o limite configurado; excesso é truncado de forma explícita.

No Linux, cancelamento envia SIGTERM ao process group, aguarda o grace period e
envia SIGKILL se necessário. Timeout usa o mesmo encerramento real. No Windows,
executores separados constroem PowerShell, CMD e WSL sem inferir shell; o
controle de árvore fica atrás de uma interface testável e os testes reais são
marcados para Windows.

## Persistência e concorrência

SQLite/aiosqlite é usado por SQLAlchemy 2 async com repositórios. As entidades
são Device, PairingCode, Job, JobOutput, Lease, Approval e AuditEvent. Datas são
UTC. Uma lease exclusiva por device protege operações mutáveis e expira por
tempo; leituras continuam concorrentes. Limites de jobs, requests, comando,
arquivo, busca e output são settings tipados e conservadores.

`RequestClaim` registra admissão de mutações com chave primária `request_id`.
`RequestReplayGuard.claim(request_id)` confirma essa reserva antes de dispatch;
repetições falham inclusive após reiniciar o Gateway. Uma falha após a reserva
não libera o ID: a garantia é admissão no máximo uma vez, e não transação
distribuída com o efeito remoto. Uma nova tentativa decidida pelo operador
precisa de outro ID. AuditEvent continua permitindo vários eventos por request.
`PairingRepository.get_available` é uma leitura sem reserva; não pode ser usado
para implementar consumo concorrente. O banco impõe unicidade do locator,
`0 <= attempts <= max_attempts`, limite positivo e expiry posterior à criação.

## MCP

O SDK Python oficial v2 atende MCP `2026-07-28` e compatibilidade legada. O
endpoint `/mcp` usa Streamable HTTP em modo stateless/JSON. O SDK, e não código
manual, implementa MCP. As tools somente validam schemas e chamam os mesmos
serviços usados por CLI/testes. Bearer auth do MCP é independente de device
auth e substituível por OAuth/OIDC sem alterar tools.

## Operação

FastAPI/Starlette hospeda `/health`, `/ready`, `/agent/ws`, pairing e o app MCP.
Uvicorn roda o Gateway. Docker usa usuário não-root, volume apenas para dados e
healthcheck; Cloudflare Tunnel futuro aponta um hostname estável ao bind local.
Nenhuma automação muda DNS, firewall ou SSH.

## Estratégia de testes

Unitários cobrem modelos, codec, policy, sandbox, backoff, executores e
redaction. Segurança cobre traversal, symlink, tokens, revogação, pairing,
replay, capability, READ_ONLY, timeout, cancelamento e output. Integração sobe
Gateway/Agent no processo, valida ONLINE/reconnect e realiza filesystem/job. O
cliente oficial MCP valida `tools/list` e chamadas reais. Ruff, mypy e pytest
formam o gate; Compose é configurado/construído/executado quando Docker existir.

## Fora do MVP

Não há dashboard, multi-tenant, filas externas, delete, process kill via MCP,
comandos administrativos, instalador Windows ou servidor OAuth caseiro.
