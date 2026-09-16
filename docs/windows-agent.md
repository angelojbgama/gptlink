# Agent Windows

O core de protocolo, reconnect, heartbeat, dispatcher, jobs e policy é
compartilhado. Os executores de plataforma ficam separados:

- `PowerShellExecutor`: usa `pwsh.exe`, quando disponível, ou
  `powershell.exe`, sempre com `-NoProfile -NonInteractive -Command`.
- `CmdExecutor`: usa `cmd.exe /d /s /c`; não converte comandos Bash.
- `WslExecutor`: executa WSL com cwd `/mnt/c/...` somente quando fornecido nesse
  namespace; não converte caminhos automaticamente. O WSL só é anunciado
  quando há uma distribuição instalada e `GPTLINK_AGENT_WSL_ROOTS` contém ao
  menos uma raiz Linux específica. O caminho é canonicalizado dentro do WSL
  antes da execução para bloquear escapes por symlink.

Filesystem Windows usa roots canônicos, comparação case-insensitive fornecida
por `pathlib` no Windows e rejeita escapes por `..`, junction/symlink, outra
unidade e UNC fora das raízes. Não configure `C:\` como raiz.

Cada job Windows é associado a um Job Object com
`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`. Cancelamento e timeout chamam
`TerminateJobObject`, encerrando o processo e seus descendentes sem construir
um comando `taskkill`. A associação ocorre imediatamente após a criação do
processo; comandos não confiáveis continuam sujeitos ao modelo de permissão,
capability, lease e auditoria do Gateway.

Exemplo de configuração (listas seguem o formato JSON do Pydantic Settings):

```powershell
$env:GPTLINK_AGENT_ROOTS='["C:\\Users\\me\\workspace"]'
$env:GPTLINK_AGENT_WSL_ROOTS='["/mnt/c/Users/me/workspace"]'
$env:GPTLINK_AGENT_PERMISSION_LEVEL='READ_WRITE'
python -m gptlink.agent run
```

Os shells são sempre explícitos (`powershell`, `cmd` ou `wsl`). A presença de
`command.start` sozinha não autoriza um shell: o Agent anuncia também
`shell.powershell`, `shell.cmd` e, se disponível e configurado, `shell.wsl`.

O modo de execução do MVP é `python -m gptlink.agent`/`gptlink-agent`.
PyInstaller, serviço Windows, auto-start e instalador permanecem fora do MVP.

O arquivo local de credenciais não tenta emular permissões POSIX no Windows.
Uma evolução futura poderá usar DPAPI ou Windows Credential Manager. Em POSIX,
o arquivo continua protegido com modo `0600`.
