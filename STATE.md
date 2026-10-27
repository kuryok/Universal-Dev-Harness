# Estado do projeto

Atualizado: 27/10/2026. Fase: fundação da Slice 1. Checkpoint e candidato v3 registrados; aceite completo de unblock pendente.

Retomada: [AGENTS.md](AGENTS.md), este estado e [MEMORY.md](MEMORY.md). O [baseline rastreado](Universal-Dev-Harness-v2_5-BASELINE-NORMATIVO-CONSOLIDADO-FINAL.md) foi restaurado exatamente; v2.5.1 é nota editorial derivada, sem precedência normativa. Estados históricos do baseline não substituem as evidências atuais.

## Funciona

- Core modular SQLite v1/v2: init/create/show/policy, migração explícita e bloqueio administrativo atômico/idempotente. Policy não autoriza execução, aceite ou entrega.
- Codec e coleta append-only implementados; resumos novos schema2 distinguem métodos com falha de entradas/subtestes. Leitura schema1 e bytes históricos preservados, sem alterar resultados.
- 94 complementares PASS; AT protegidos v1 10/10, policy 11/11 e bloqueio v5 13/13 no candidato 23a4fbb. Coletas I/T não reexecutadas.
- Selos v1–v6 publicados; v7 não publicado. [Alinhamento e resolução A1–A10](openspec/changes/slice1-evidence-storage/alignment.md).

## Pendente

- Migração/leitura v3 no candidato 23a4fbb: AT v6 de migração, fontes inválidas, concorrência e interrupções PASS. Unblock permanece pendente. Executor de agentes e entrega ponta a ponta indisponíveis; Slice 1 incompleta.
- Bootstrap geral INCOMPLETE: exportação offline, AT de transporte e regressões completas pendentes. A checagem complementar do protótipo usa mínimo histórico e não comprova toda a suíte corrente; corrigir esse vínculo antes de publicar. Sem liberação de publicação ou relaxamento dos critérios.
- POCs de produto seguem NOT_RUN. Store de bootstrap não é store oficial protegida contra proprietário do host; nenhum waiver ou certificação de segurança foi criado.
- OpenSpec é usado como fluxo documental adaptado, descrito em [project.md](openspec/project.md), sem CLI/canonicalização automatizada. Backup remoto não configurado; nenhuma inexistência de outras cópias foi presumida.

## Próxima tarefa

Revisão independente e incremento de unblock conforme [contrato](openspec/changes/slice1-unblock-run/proposal.md). AT v6 completo: 8 métodos PASS e 8 FAIL ligados a unblock ainda ausente; leitura U7 depende de preparar unblock. A [reanálise do próximo incremento](openspec/changes/slice1-unblock-run/next-step-review.md) confirma esse recorte. Os AT devem usar o SHA do candidato, não o baseline negativo fixo; detalhes em [relatório v6](openspec/changes/slice1-unblock-run/migration-v3-candidate-report.json) , [regressões v1/v2](openspec/changes/slice1-unblock-run/migration-v3-legacy-report.json) e [v5](openspec/changes/slice1-unblock-run/migration-v3-block-v5-report.json). Bootstrap continua limitado a correções até verificar v3/unblock.

## Verificação

- `PYTHONDONTWRITEBYTECODE=1 TMPDIR="$PWD/bootstrap/slice1" python3 -m unittest discover -s tests`.
- [README](README.md) para entrada e [diagnóstico reproduzido](openspec/changes/slice1-evidence-storage/report-audit.md) para histórico da auditoria; a resolução posterior está no alinhamento.
- Não abrir credenciais, limpar o workspace do spike, publicar selo ou declarar segurança/entrega sem escopo e prova próprios.
