# Tarefas — Change 1 (`v4-state-ledger-and-preflight`)

**Pré-requisito obrigatório:** aprovação explícita do usuário sobre [proposal.md](proposal.md) e [design.md](design.md) antes de iniciar a tarefa T1.

---

## Sequência de implementação (pós-aprovação)

- [ ] **T1 — Domínio e requisições v4** (`udh/domain.py`, `udh/transitions.py`)
  - Adicionar `Transition`, `EvidenceRef`, `Checkpoint` e suporte a `schema_version = 4` em `Run` (com `ticket_id`, `mode`, `updated_at`, `transitions`, `evidence_refs`, `checkpoints`).
  - Adicionar validadores de requisição v4 (`StartRequest`, `BlockV4Request`, `UnblockV4Request`, `CompleteRequest`, `EvidenceRequest`, `CheckpointRequest`), preservando v1–v3 intactos.

- [ ] **T2 — Esquema v4, criação direta e migração `v3 → v4`** (`udh/migrations.py`)
  - Implementar constantes DDL exatas de [design.md](design.md), `init_v4`, `validate_schema_v4`, `check_integrity_v4` e `migrate_v4` em transação única `BEGIN IMMEDIATE`.
  - Preservar integralmente `RUNS_SQL`, `EVENTS_SQL`, `BLOCK_REQUESTS_SQL`, `*_V3_SQL`, `validate_schema`, `check_integrity`, `migrate` e `migrate_v3`.

- [ ] **T3 — Persistência `O(1)` e transições atômicas v4** (`udh/store.py`)
  - Implementar caminho quente v4 (`_load_v4`, `get`, `create` v4) sem `quick_check`/`foreign_key_check`/scan global.
  - Implementar mutações atômicas com CAS e replay durável (`start`, `block_v4`, `unblock_v4`, `complete`, `record_evidence`, `record_checkpoint`) e método `deep_check()` para `doctor`.
  - Preservar integralmente os caminhos v1–v3 exigidos pelos selos `v1–v6`.

- [ ] **T4 — Política v2 e Preflight `doctor`** (`udh/policy.py`, `udh/doctor.py`)
  - Implementar `POLICY_VERSION = 2` para execuções v4 mantendo `POLICY_VERSION = 1` e seu `policy_sha256` bit-a-bit idênticos em v1–v3.
  - Implementar `udh/doctor.py` (plataforma Linux/WSL2, cgroups v2, userns/`subuid`/`subgid`, Podman rootless, integridade profunda SQLite opcional e status honesto `READY_WITH_LIMITS` / `NOT_READY`).

- [ ] **T5 — Serviço e CLIs** (`udh/service.py`, `udh/__main__.py`, `udh/lifecycle.py`)
  - Expor casos de uso v4 em `CoreService`.
  - Estender `udh/__main__.py` com `init --schema-version {1,4}`, `create --ticket-id/--mode` (v4) e `doctor`, sem adicionar `migrate`/`block`/`unblock` ao CLI comum.
  - Criar `udh/lifecycle.py` com os subcomandos autoritativos v4 e códigos de saída padronizados (`0/1/2/3/4`).
  - Confirmar que `udh/operator.py` não foi modificado.

- [ ] **T6 — Testes complementares v4, Política v2 e `doctor`** (`tests/test_v4_ledger.py`, `tests/test_policy_v2_and_doctor.py`)
  - Cobrir `V4-01` a `V4-07`, incluindo prova automatizada de leitura `O(1)` via `set_trace_callback` e `EXPLAIN QUERY PLAN`, bloqueio SQL/domínio de `DONE` sem evidência `PASS`, pendências `UNKNOWN` (F-03), concorrência e cenários negativos de `doctor`.

- [ ] **T7 — Verificação completa, regressão selada e revisão independente (R1/R2)**
  - Rodar toda a suíte complementar (`python3 -m unittest discover -s tests`).
  - Rodar os **50 AT protegidos selados (`v1`, `v2`, `v5`, `v6`)** em Podman rootless, confirmando 50/50 PASS e selos intocados.
  - Executar revisão independente (F-02 / R2) em contexto separado (`research`) sobre o diff candidato.
  - Gravar resumo compacto verificável (`<= 32 KiB`) em `openspec/changes/v4-state-ledger-and-preflight/verification-summary.json`, atualizar `STATE.md` e `MEMORY.md`, e preparar commit pequeno em português.
