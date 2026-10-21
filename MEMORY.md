# UDH — ponto de retomada

Atualizado em **21/10/2026**. Código/experimentos de referência: commit **518d9b0**.

Esta nota ajuda a reconstruir contexto. Não é requisito, autorização, estado operacional ou evidência de aceite. Se estiver desatualizada, conferir os arquivos e o Git antes de confiar nela. Requisitos continuam no OpenSpec; o futuro estado operacional pertence ao Core/SQLite. Nenhum serviço de memória foi instalado.

## Para abrir uma nova sessão

Prompt sugerido ao trocar de agente/modelo:

> Leia AGENTS.md e MEMORY.md, confira o estado atual do Git e retome a partir das fontes indicadas. Primeiro apresente o estado confirmado e a próxima tarefa; não implemente uma spec ainda não aprovada.

O nome MEMORY.md não garante carregamento automático por todos os agentes. AGENTS.md ainda não contém referência a este arquivo e sua seção de fase continua dizendo “spike da Slice 0”; o relatório abaixo registra o avanço posterior. Essa diferença documental precisa ser reconciliada ao preparar a próxima tarefa, sem presumir que a fase antiga impede consultar seus resultados.

## Onde paramos

- Slice 0 encerrada **no escopo experimental VM Linux/API gratuita**. Recomendação registrada: especificar a Slice 1; não é certificação de produto, perfil Developer completo, Windows/WSL2 ou assinatura.
- Pi e OpenCode receberam resposta REAL do modelo `nvidia/nemotron-3-ultra-550b-a55b:free`, via OpenRouter. Agentes com `network=none`; chave somente no gateway externo, resolvida pelo CLI oficial do Pi em modo somente leitura/no-refresh.
- Três chamadas normais da rodada final tiveram custo reportado zero. Uma quarta chamada foi interrompida deliberadamente e não tem recibo final de uso.
- Não há Core de produto, banco operacional SQLite, harness/gates ou testes de aceite implementados. Os roteiros em `spike/slice0/` são descartáveis.
- Próxima ação sugerida: **especificar uma primeira tarefa pequena de Core + SQLite no OpenSpec e obter aprovação antes de implementar**. Ainda não existe spec aprovada da Slice 1. Não deduzir esquema de banco, APIs ou critérios de aceite desta nota.

## Fontes para recuperar o contexto

| Assunto | Fonte |
|---|---|
| Instruções do projeto | [AGENTS.md](AGENTS.md) |
| Desenho consolidado | [Arquitetura v2.5.1](UDH-v2.5-ARQUITETURA-CONSOLIDADA-v2.5.1.md) — arquivo ainda fora do Git, ver pendências abaixo |
| Perguntas e critérios do spike | [SPIKE-SLICE0-PERGUNTAS.md](SPIKE-SLICE0-PERGUNTAS.md) |
| Resultado, limites e próximo passo | [SPIKE-SLICE0-RESULTADOS.md](SPIKE-SLICE0-RESULTADOS.md) |
| Escopos e aprovações do spike | [Mudança OpenSpec](openspec/changes/spike-slice0-isolamento/proposal.md) |
| Prova real dos agentes e corte | [free-models-probe.json](spike/slice0/evidence/free-models-probe.json) |
| Recibos do gateway real | [free-gateway-audit.jsonl](spike/slice0/evidence/free-gateway-audit.jsonl) |
| Proteção da chave nos artefatos examinados | [free-secret-scan.json](spike/slice0/evidence/free-secret-scan.json) |
| Isolamento e persistência sintéticos | [results.json](spike/slice0/evidence/results.json), [f-probe.json](spike/slice0/evidence/f-probe.json) |

## Achados que o próximo agente não deve perder

- **Pi pode retornar exit0 após falha de inferência.** O corte real produziu erro estruturado e texto parcial, sem timeout. O futuro adaptador precisa interpretar eventos e evidências; só o código de saída não basta.
- **Arquivos RO dentro de diretório renomeável não bastam.** O bypass foi reproduzido. No perfil corrigido, o diretório é mountpoint fixo e os arquivos são binds RO; locks ficam no workspace.
- **Pi precisa criar locks ao ler configuração/autenticação.** Tornar o diretório inteiro RO impediu o início; os arquivos protegidos e o espaço para locks foram separados no experimento.
- **OpenCode excedeu 256 MiB ao listar modelos; passou em 768 MiB.** Pi passou no perfil de 384 MiB. Não foi medido mínimo exato nem benchmark comparável.
- **O gateway real só encaminha um prompt fixo de teste.** Não encaminha contexto/código/ferramentas recebidos do agente e não deve virar componente de produto sem nova especificação e verificação.
- **Free não significa qualquer modelo servir a qualquer teste.** O roteador genérico escolheu um classificador; a rodada final fixou um modelo de conversa com preço zero confirmado.
- **As recusas HTTP403 dos testes públicos OpenCode não invalidam o terminal do usuário.** O usuário demonstrou DeepSeek V4.1 Flash Free funcionando. O acesso HTTP público isolado tinha perfil distinto; a prova bem-sucedida dos dois agentes usou OpenRouter.
- **A evidência é limitada ao ambiente e aos casos registrados.** A3/B5 continuam parciais; D3 e Windows/WSL2 não executados. Os arquivos de evidência no host são editáveis, não uma store oficial selada do produto.

## Ambiente e preferências confirmados

- Trabalho em VM Debian 13 do usuário, com Podman 5.4.2 rootless dentro dela. Não atribuir restrições da VM/sessão à máquina física sem reprodução.
- Binários existentes no Debian foram montados RO; não foram instaladas outras cópias nos contêineres nem atualizados os agentes. Versões testadas: Pi 1.2.0, OpenCode 1.18.35, Node 22.23.3. Conferir novamente se forem relevantes à próxima tarefa.
- O usuário pediu **não usar skills globais neste projeto** e pediu informar o próximo passo ao terminar uma tarefa.
- O usuário autorizou auxiliares em paralelo para os experimentos. Isso não adicionou segundo/terceiro agente ao produto nem concede automaticamente permissões futuras de rede, credenciais ou instalação.
- A autorização de usar a chave OpenRouter no gateway não permite exibi-la, montá-la no agente, guardá-la no Git ou usar modelos pagos. Não abrir auth stores ou imprimir ambiente para “recuperar contexto”.

## Pendências locais importantes

No fim da sessão, havia duas alterações preexistentes, preservadas fora dos commits do spike:

- Exclusão de `Universal-Dev-Harness-v2_5-BASELINE-NORMATIVO-CONSOLIDADO-FINAL.md`.
- Novo `UDH-v2.5-ARQUITETURA-CONSOLIDADA-v2.5.1.md`, ainda não rastreado. SHA-256 observado: `52532bc38f999e1f054d0f84afe006cacf204f9cc9b1171d87081441a1535460`.

Uma cópia obtida só pelo Git pode não conter a arquitetura consolidada. Conferir e resolver seu versionamento com o usuário; não restaurar o baseline excluído nem incorporar alterações preexistentes silenciosamente. A emenda ADR-018 A continua sem ratificação formal.

## Conferência sem novas chamadas de modelo

```bash
git status --short
git log -5 --oneline
python3 spike/slice0/free_models_probe.py --verify-only
```

O último comando reavalia artefatos existentes, sem nova inferência externa; cria fixtures locais de execução. Não é rerun da prova nem atestação de integridade. Não executar novamente as chamadas reais só para “lembrar”: elas usam serviço externo e quota de modelos gratuitos.

Na última rodada documentada, verificações de sintaxe, evidências e whitespace passaram. Não confundir esse histórico com testes executados na nova sessão. `tools/harness.py` e CLI OpenSpec não estavam disponíveis; gates fast/full e validação pela CLI não foram executados.

## Manutenção desta nota

Ao pausar, registrar aqui somente mudanças de contexto confirmadas, referência ao último commit de trabalho, evidências relevantes, impedimentos e a referência à próxima tarefa no OpenSpec. Preferir substituir fatos obsoletos a acumular transcrições. Manter curta; detalhes ficam nas fontes indicadas. Nenhuma chave, token, conteúdo de auth store ou autorização inventada deve entrar neste arquivo.

Memorix e ai-memory foram considerados para apoiar a construção do UDH, mas não instalados nem escolhidos como nova dependência. Uma futura adoção deve preservar a autoridade de OpenSpec/Git/Core e evitar outro orquestrador. A escolha de memória auxiliar prevista no desenho do produto não foi alterada por esta nota.
