# Estado do projeto

Atualizado: 27/10/2026. Fase: fundação da Slice 1. Checkpoint de alinhamento registrado; candidato v3 ainda sem AT protegidos.

Retomada: [AGENTS.md](AGENTS.md), este estado e [MEMORY.md](MEMORY.md). O [baseline rastreado](Universal-Dev-Harness-v2_5-BASELINE-NORMATIVO-CONSOLIDADO-FINAL.md) foi restaurado exatamente; v2.5.1 é nota editorial derivada, sem precedência normativa. Estados históricos do baseline não substituem as evidências atuais.

## Funciona

- Core modular SQLite v1/v2: init/create/show/policy, migração explícita e bloqueio administrativo atômico/idempotente. Policy não autoriza execução, aceite ou entrega.
- Codec e coleta append-only implementados; resumos novos schema2 distinguem métodos com falha de entradas/subtestes. Leitura schema1 e bytes históricos preservados, sem alterar resultados.
- 94 complementares PASS na execução local de 27/10, incluindo migração/leitura v3; testes anteriores preservados. AT e coletas I/T não reexecutados neste incremento.
- Selos v1–v6 publicados; v7 não publicado. [Alinhamento e resolução A1–A10](openspec/changes/slice1-evidence-storage/alignment.md).

## Pendente

- Migração/leitura v3 implementadas em candidato local ainda sem AT protegidos; unblock permanece pendente, ambos já especificados/aprovados. Executor de agentes e entrega ponta a ponta indisponíveis; Slice 1 incompleta.
- Bootstrap geral INCOMPLETE: exportação offline, AT de transporte e regressões completas pendentes. A checagem complementar do protótipo usa mínimo histórico e não comprova toda a suíte corrente; corrigir esse vínculo antes de publicar. Sem liberação de publicação ou relaxamento dos critérios.
- POCs de produto seguem NOT_RUN. Store de bootstrap não é store oficial protegida contra proprietário do host; nenhum waiver ou certificação de segurança foi criado.
- OpenSpec é usado como fluxo documental adaptado, descrito em [project.md](openspec/project.md), sem CLI/canonicalização automatizada. Backup remoto não configurado; nenhuma inexistência de outras cópias foi presumida.

## Próxima tarefa

Verificar migração SQLite v2→v3 e leitura v3 do change de [desbloqueio](openspec/changes/slice1-unblock-run/proposal.md) nos AT protegidos antes de implementar a transição. A [reanálise do próximo incremento](openspec/changes/slice1-unblock-run/next-step-review.md) confirma esse recorte. Executar os AT sobre o SHA do novo candidato, não sobre o baseline negativo fixo. Bootstrap continua limitado a correções até verificar v3/unblock.

## Verificação

- `PYTHONDONTWRITEBYTECODE=1 TMPDIR="$PWD/bootstrap/slice1" python3 -m unittest discover -s tests`.
- [README](README.md) para entrada e [diagnóstico reproduzido](openspec/changes/slice1-evidence-storage/report-audit.md) para histórico da auditoria; a resolução posterior está no alinhamento.
- Não abrir credenciais, limpar o workspace do spike, publicar selo ou declarar segurança/entrega sem escopo e prova próprios.
