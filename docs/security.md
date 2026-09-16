# GPTLink — threat model e segurança

## Limites de confiança

O Gateway autentica callers MCP e devices separadamente. O Agent é a última
linha de defesa: verifica capabilities, nível de permissão, roots canônicos,
limites e timeouts localmente. Um arquivo, README, log ou resposta de comando
é sempre dado não confiável; prompt injection nunca concede autorização.

## Ameaças e controles

| Ameaça | Controle v0.1 |
| --- | --- |
| Gateway comprometido | Policy local, roots explícitos, capabilities e READ_ONLY no Agent |
| Agent comprometido | Revogação no Gateway, credencial isolada e auditoria; comprometimento total do host não é ocultado |
| Token de Agent roubado | Token aleatório de 256 bits, hash salted no banco, revogação e WSS em produção |
| Pairing code roubado | Locator + segredo temporário Argon2id, expiração de 10 min, uso único e tentativas limitadas |
| Path traversal/symlink/junction | `Path.resolve()` e containment após resolução; roots `/` e `C:\\` proibidos |
| Command injection | Shell explicitamente selecionado; executor não interpola argumentos fora do comando solicitado |
| Dois callers concorrentes | Lease exclusiva para mutações e request IDs/replay guard |
| Replay/reconnect | UUID único por request, claim durável e token reutilizado apenas para reconnect autenticado |
| Secret em log | Redaction, repr sem token, erros genéricos e headers nunca auditados |
| DoS de output/jobs | Limites de output, jobs concorrentes, comando/tamanho/search bounds e timeout |
| Cliente MCP malicioso | Bearer separado, schemas estritos, capabilities, policy Agent e auditoria |

Operações destrutivas (delete, kill via MCP, shutdown, reset/clean Git,
registry/firewall/users) não são expostas no MVP. Produção exige HTTPS para MCP
e WSS para Agents; HTTP/WS local é uma escolha explícita de desenvolvimento.

## Dados sensíveis

O banco contém somente hashes de tokens/codes e metadata operacional. Não
persistimos Authorization headers, API keys, senhas, conteúdo completo de
secrets ou saída ilimitada. O conteúdo de arquivos pode conter instruções
maliciosas e deve ser tratado como dado ao ser apresentado a um modelo.

## Resposta operacional

Revogue imediatamente devices suspeitos, rotacione o bearer MCP e preserve
AuditEvent/redacted logs. Um vazamento do banco não permite autenticação direta
sem quebrar o hash do token; um Gateway comprometido continua limitado pela
policy instalada no Agent.
