# Estado do projeto

Atualizado: 22/10/2026. Fase: fundação da Slice 1.

Este arquivo orienta a retomada; não aprova requisitos nem substitui OpenSpec, SQLite, Git ou evidências. Consulte [AGENTS.md](AGENTS.md) para regras e [MEMORY.md](MEMORY.md) para contexto curto.

## Funciona

- Core modular: SQLite guarda o estado; Git guarda código; specs ficam em OpenSpec.
- Consulta determinística de policy, sem conceder execução, transição, aceite ou entrega.
- Migração explícita v1→v2 e leitura v2 implementadas/verificadas no candidato `96bd4dc`, preservando formatos v1, dados literais e sequências. Oito módulos pequenos; block permanece indisponível.
- 30 complementares, dez AT v1, onze AT policy, I1–I7/T1–T8 e smoke CLI v2 de dez chamadas PASS. Revisão independente do produto PASS.
- Migração e fronteira: sete métodos independentes PASS, incluindo concorrência e onze pontos de interrupção. Conjunto completo de 13 métodos: oito PASS, cinco FAIL de block não implementado, zero erros/skips; 140 chamadas auditadas.
- IPC instrumentado corrigido e revisado; selos v1–v5 preservados/verificados. AT v5 idênticos ao v3. Falhas históricas e correções estão documentadas.

## Pendente

- Implementar PENDING→BLOCKED pela entrada administrativa protegida, com journal/autorização atômicos, revisão otimista e idempotência; executar os cinco AT de bloqueio ainda falhando.
- Executor de agentes, coordenação automática até entrega e interface guiada ainda não implementados. OpenSpec é usado documentalmente; sua CLI e o harness fast/full não estão disponíveis.
- Instalação operacional protegida e aceite oficial do produto não demonstrados. O bootstrap usa dados sintéticos e não protege de quem escreve no host. Slice 1 incompleta.

## Próxima tarefa

Implementar e verificar o bloqueio já especificado, preservando migração/leitura v2 e contratos v1. Não habilitar agentes ou declarar entrega oficial.

## Verificação

- Complementares: `PYTHONDONTWRITEBYTECODE=1 TMPDIR="$PWD/bootstrap/slice1" python3 -m unittest discover -s tests -v`.
- Isolamento: `PYTHONDONTWRITEBYTECODE=1 python3 bootstrap/slice1/integrate_acceptance.py`.
- [Contrato](openspec/changes/slice1-block-run/proposal.md), [resultado e reprodução](openspec/changes/slice1-block-run/migration-verification.md), [resumo verificável](openspec/changes/slice1-block-run/migration-verification.json).
- [AT v5](bootstrap/slice1/evidence/migration-candidate-v5.json) e [revisão](openspec/changes/slice1-block-run/migration-review.json).
- Atualizar este estado e a memória com os resultados efetivos ao encerrar a próxima tarefa; resultados anteriores não equivalem a testes de uma sessão futura.
