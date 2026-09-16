# Agent Windows — plano de compatibilidade

O core de protocolo, reconnect, heartbeat, dispatcher, jobs e policy é
compartilhado. Os executores de plataforma ficam separados:

- `PowerShellExecutor`: executa PowerShell explicitamente selecionado.
- `CmdExecutor`: executa CMD explicitamente selecionado.
- `WslExecutor`: executa WSL com cwd `/mnt/c/...` somente quando fornecido nesse
  namespace; não converte caminhos automaticamente.

Filesystem Windows usa roots canônicos e trata junctions/UNC como possíveis
escapes. Processos deverão usar criação de árvore apropriada ao Windows;
cancelamento não pode ser apenas mudança de status. Testes Windows reais são
marcados e não são declarados aprovados no VPS Linux.

O primeiro modo de execução será `python -m gptlink.agent`/`gptlink-agent`.
PyInstaller, serviço Windows, auto-start e instalador ficam para depois que os
executores unitários e integração em uma máquina Windows passarem.
