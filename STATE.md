# Estado do projeto

Atualizado: 22/10/2026. Fase: fundação da Slice 1.

Este arquivo orienta a retomada; não aprova requisitos nem substitui OpenSpec, SQLite, Git ou evidências. Consulte [AGENTS.md](AGENTS.md) para regras e [MEMORY.md](MEMORY.md) para contexto curto.

## Funciona

- Core modular: criação e consulta de execuções; SQLite guarda estado, Git guarda código.
- Consulta determinística de policy: leitura permitida; execução, transição, aceite e entrega não são autorizados por essa consulta.
- Isolamento experimental em Podman, AT independentes e selos v1/v2/v3 identificados e preservados.
- Migração SQLite explícita v1→v2 e leitura v2 implementadas no candidato `96bd4dc`; compatibilidade v1 preservada nas verificações executadas.
- Nesse candidato: 28 complementares, 10 AT v1, 11 AT de policy, I1–I7/T1–T8 e smoke de leitura v2 com 10 chamadas PASS. Revisão independente de migração/leitura PASS.

## Pendente

- Concluir a validação de interrupções da migração: o AT M3 apresentou dois erros de infraestrutura; os pontos passaram isoladamente. Há uma corrida reproduzível na publicação dos arquivos de reconhecimento do observador; sua relação com os erros iniciais é inferência, pois o bridge v3 não preservou a causa bruta.
- Corrigir o transporte instrumentado e repetir M3 com evidência completa. O resultado do conjunto novo ainda é parcial; não declarar a migração totalmente aceita.
- Implementar e verificar PENDING→BLOCKED somente depois de concluir a migração. A operação block continua indisponível.
- Coordenação automática de agentes, fluxo completo até entrega e interface guiada ainda não implementados. OpenSpec é usado documentalmente; sua CLI e o harness fast/full não estão disponíveis.
- Instalação operacional protegida e aceite oficial do produto não demonstrados. Slice 1 continua em construção.

## Próxima tarefa

Corrigir a publicação dos arquivos IPC da instrumentação, preservar o selo v3, revisar/versionar a correção e repetir os testes de interrupção e regressão no mesmo candidato de migração. Não habilitar block nem agentes neste incremento.

## Verificação

- Complementares: `PYTHONDONTWRITEBYTECODE=1 TMPDIR="$PWD/bootstrap/slice1" python3 -m unittest discover -s tests -v`.
- Regressores de isolamento: `PYTHONDONTWRITEBYTECODE=1 python3 bootstrap/slice1/integrate_acceptance.py`.
- [Contrato e design da migração](openspec/changes/slice1-block-run/proposal.md), [preparo e evidências anteriores](openspec/changes/slice1-block-run/verification.md), [revisão do candidato](openspec/changes/slice1-block-run/migration-review.json).
- [AT atuais: resultados parciais](bootstrap/slice1/evidence/migration-candidate.json), [regressões](bootstrap/slice1/evidence/migration-regression.json), [smoke CLI v2](bootstrap/slice1/evidence/migration-read-smoke.json) e [diagnóstico IPC](bootstrap/slice1/evidence/migration-ipc-race.json).
- Atualizar este estado e a memória com os resultados efetivos ao terminar a próxima tarefa; testes anteriores não equivalem a testes de uma sessão futura.
