# Migração SQLite e leitura v2 — resultado de 22/10/2026

Incremento de produto concluído no candidato `96bd4dcb4e792941f8846d8446f01d90426b3c5d`: migração explícita e leitura v2, mantendo v1. Bloqueio continua indisponível. Oito módulos, 592 linhas no total, maior módulo com 189 linhas; nenhuma dependência nova.

## Mudança

`migrations.py` define o esquema v2 e a reconstrução transacional. `store.py` preserva o esquema v1, valida v2 e recupera os dados sem normalizar literais. `domain.py` representa as duas versões e valida journal de BLOCKED já existente, sem criar uma operação de bloqueio. `service.py` delega a migração; `operator.py` expõe somente migrate no contexto do launcher protegido. Contexto não é uma credencial; a proteção efetiva é o isolamento do banco/configuração pelo controlador.

init continua criando v1. Migração não é implícita, não cria banco ausente e não concede autorização de execução. create/show/policy sobre v1 preservam seus formatos; v2 inclui schema_version/revision no envelope de execução. Migração é idempotente, mantém foreign_keys ON e preserva UUIDs, sequências, referências e datas, inclusive espaços/literais.

## Evidências finais

- 30 testes complementares PASS; inclui oito complementares de migração/leitura e testes de IPC. Comando: `PYTHONDONTWRITEBYTECODE=1 TMPDIR="$PWD/bootstrap/slice1" python3 -m unittest discover -s tests -v`.
- AT independentes no selo v5, contra o mesmo candidato: 13 métodos executados; 8 PASS, 5 FAIL da operação block ainda não implementada, zero erros/skips. Todos os seis métodos de migração/compatibilidade e B2 PASS; B5 também PASS por preservar negações. Não é full PASS do recorte de bloqueio.
- M3 PASS: concorrência e onze checkpoints de interrupção antes das gravações e após commit. A execução completa produziu 140 chamadas auditadas, todas limpas e classificadas. Interrupção de processo instrumentada, sem alegar queda de energia/hardware ou resistência a código que evite a instrumentação.
- Smoke da CLI real v2 PASS em dez chamadas: preservação de dados legados, criação/leitura/idempotência v2, policy e repetição de migração. Executado pelo controlador, sem importar Core no host.
- Regressões contra o mesmo candidato: dez AT v1, onze AT policy v2 e I1–I7/T1–T8 PASS.
- Revisão independente do produto PASS em migration-review.json. Duas revisões independentes do IPC PASS nos bytes publicados. Hashes dos oito módulos correspondem ao candidato, revisão, smoke e AT. Selos v1–v5 verificados; AT v5 idênticos ao v3.

Resumo: migration-verification.json. Relatórios detalhados: `bootstrap/slice1/evidence/migration-candidate-v5.json`, `migration-read-smoke.json`, `migration-regression*.json`.

## Falhas encontradas e corrigidas

A primeira execução v3 teve dois INFRA_ERROR em subcasos M3; diagnósticos isolados passaram. Foi reproduzida a possibilidade de ACK/done vazio ficar visível durante write_text; corrigida publicação por arquivo temporário fechado e replace. Causa histórica exata não foi preservada pelo bridge v3: não atribuir certeza causal a esses erros.

O v4 preservou o erro bruto e revelou corrida distinta: iterdir observava um nome .pending que desaparecia por rename antes de lstat. O v5 tolera exclusivamente temporários de publicação já desaparecidos; mantém recusa de nomes finais ausentes/symlinks e validação independente de conteúdo/nonce/contêiner. Complementares determinísticos e revisão conferiram essa distinção. M3 passou com esses mesmos AT e o Core inalterado. Tentativas v3/v4 e diagnósticos permanecem nos arquivos migration-candidate.json, migration-candidate-v4.json, migration-fault-diagnostic.json e migration-ipc-race.json.

As correções foram versionadas, sem editar AT ou bytes dos selos publicados: v4 `49a07d7f…`, v5 `182d2ba7…`. Não houve relaxamento de timeout, xfail, skip ou conversão de erro de infraestrutura em sucesso.

## Reprodução dos AT

O comando abaixo executa o conjunto completo e mostra também as cinco falhas esperadas de block; não altera AT nem suprime resultados:

```python
from pathlib import Path
import sys
sys.path.insert(0, str(Path('bootstrap/slice1').resolve()))
from block_seal import verify_seal
from integrate_block import run_suite
seal = Path('bootstrap/slice1/seals/v5').resolve()
manifest = verify_seal(seal, 'sha256:182d2ba72a7b5dfb5648e650577dcc3bbbbd67048c72a3bf959406ce7dd89ac6')
result = run_suite(seal, manifest, '96bd4dcb4e792941f8846d8446f01d90426b3c5d')
print(result['outcomes'], result['errors'])
```

## Limites e próximo passo

Não implementados: escrita PENDING→BLOCKED, desbloqueio, executor de agentes, instalação operacional fora do projeto e entrega oficial. O banco administrativo deste bootstrap é sintético, protegido do contêiner adversário; não de quem pode escrever no host. Revisão/AT não concedem ACCEPTED/DELIVERED oficial. Slice 1 incompleta. Harness fast/full e CLI OpenSpec não executados porque ausentes.

Próximo incremento: implementar PENDING→BLOCKED com pedido/autorização/evento atômicos, revisão otimista, replay e concorrência; então exigir os cinco AT ainda pendentes e B4 reais. Não liberar agentes.
