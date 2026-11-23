# Change 1 — Ledger SQLite v4, Ciclo de Vida Completo, Leitura O(1), Política v2 e Preflight `doctor`

**Estado:** APROVADO, IMPLEMENTADO E VERIFICADO EM 23/11/2026 (`9f84150`; resumo em [verification-summary.json](verification-summary.json)).
**Data:** 23/11/2026.  
**Base verificada:** `f138cff` (`0bd458d` no Core v1–v3; 98/98 testes complementares PASS; 50/50 AT selados `v1–v6` PASS; revisão independente R2/F-02 PASS).  
**Referências normativas:** [baseline normativo rastreado](../../../Universal-Dev-Harness-v2_5-BASELINE-NORMATIVO-CONSOLIDADO-FINAL.md) (`ADR-002`, `ADR-005`, `ADR-011`, `ADR-013`, `ADR-016`, `ADR-023`, `ADR-025`, `ADR-028`, `B1.1`, `B1.3`, `UX-01`, `F-03`) e ratificação registrada em [openspec/project.md](../../project.md).

---

## 1. Objetivo e limites

Fechar a fundação operacional de estado, política e diagnóstico do **UDH Core** para sustentar o marco vertical de produto (Change 2), resolvendo quatro lacunas concretas do Core v1–v3:

1. **Ledger unificado SQLite v4 (`PRAGMA user_version = 4`):** substituir tabelas rígidas por operação (`block_requests`/`unblock_requests`) e revisões fixas (`0/1/2`) por um ledger append-only tipado (`transitions`) com ciclos repetíveis `PENDING ↔ IN_PROGRESS ↔ BLOCKED → DONE`, `revision >= 0` monotônica, `ticket_id` e modo adaptativo `FAST | STANDARD | DEEP` (ADR-023), além de tabelas dedicadas para **referências de evidência oficial** (`evidence_refs`, ADR-005/016/B4.9) e **checkpoints consistentes** (`checkpoints`, F-03).
2. **Leitura `O(1)` desacoplada da verificação global:** eliminar `PRAGMA quick_check`, `PRAGMA foreign_key_check` e full-table scan (`SELECT id FROM runs`) do caminho quente (`show`/`policy`) em bancos v4, mantendo checagem por execução via índices B-tree e movendo a integridade global profunda para `doctor` e `migrate --to 4`. Bancos v1–v3 mantêm integralmente o comportamento exigido pelos 50 AT congelados `v1–v6`.
3. **Política determinística v2 (`POLICY_VERSION = 2`):** avaliar ações sobre o estado real e fatos verificados de bancos v4, mantendo `fail-closed` para execução de agente (`EXECUTE_AGENT = DENY`) e entrega (`DELIVER_ARTIFACT = DENY`) até o Change 2. Em bancos v1–v3, `POLICY_VERSION = 1` e seu `policy_sha256` permanecem bit-a-bit idênticos.
4. **Preflight `doctor` (B1.3 parcial):** inspecionar integridade profunda do SQLite (quando `--db` é informado) e pré-requisitos reais do host Linux/Podman rootless (kernel, cgroup v2, user namespaces, `subuid`/`subgid`, Podman rootless), retornando `READY | READY_WITH_LIMITS | NOT_READY` com códigos estruturados ADR-025 em português — reportando explicitamente gateway e adaptadores de agente como `NOT_CONFIGURED` (`fail-closed`) até o Change 2.

### O que **NÃO** faz parte deste change

- **Não implementa** Runtime Manager rootless, imagem OCI de agente, Gateway de inferência, adaptador Pi 1.2.0, Executor Confiável (F-04) nem `POC-01`–`POC-04` (escopo do **Change 2**).
- **Não implementa** tabela multi-ticket/DAG (`tickets`, `TICKET-01/02`) nem `DISCOVER`/`SPEC`/`PLAN`; o modelo v4 permite adicionar `tickets` de forma puramente aditiva sem reconstruir `runs`/`transitions`.
- **Não modifica** `bootstrap/slice1/`, selos `v1–v6`, AT protegidos, `udh/operator.py`, `spike/slice0/` nem cria selo `v7`.
- **Não adiciona** dependências Python externas (mantém 100% biblioteca padrão), UI web, Memorix, Judge ou rede aberta.
- **Não declara** produto entregue, segurança certificada nem `POC-01`–`POC-04` executadas.

---

## 2. Ciclo de vida, invariantes e autoridade

### Estados e transições permitidas no SQLite v4

| Operação | Evento (`kind`) | De → Para | Revisão | Requisitos determinísticos obrigatórios |
| --- | --- | --- | --- | --- |
| `CREATE_RUN` | `RUN_CREATED` | `NULL → PENDING` | `0` | `spec_id`, `spec_version`, `request_id` não vazios; `mode` ∈ `{FAST, STANDARD, DEEP}` (padrão `STANDARD`); `ticket_id` opcional não vazio. |
| `START_RUN` | `RUN_STARTED` | `PENDING → IN_PROGRESS` | `r → r + 1` | `expected_revision == r`; `state == 'PENDING'`; ausência de pendência `UNKNOWN` não reconciliada no último checkpoint. |
| `BLOCK_RUN` | `RUN_BLOCKED` | `PENDING \| IN_PROGRESS → BLOCKED` | `r → r + 1` | `expected_revision == r`; `block_category` ∈ `{POLICY_VIOLATION, FUNCTIONAL_FAILURE, ADMIN_PAUSE}`; `reason_code`, `reason` e `next_action` não vazios (ADR-025). |
| `UNBLOCK_RUN` | `RUN_UNBLOCKED` | `BLOCKED → PENDING` | `r → r + 1` | `expected_revision == r`; `reason` não vazio; ciclos `PENDING → IN_PROGRESS → BLOCKED → PENDING → IN_PROGRESS` repetíveis sem reescrever histórico. |
| `COMPLETE_RUN` | `RUN_COMPLETED` | `IN_PROGRESS → DONE` | `r → r + 1` | `expected_revision == r`; `evidence_id` válido pertencente ao **mesmo `run_id`**, com `verdict == 'PASS'` e `kind` ∈ `{AT_RESULT, SMOKE_RESULT, POC_RESULT}`; ausência de pendência `UNKNOWN` não reconciliada no último checkpoint. |

- `DONE` é terminal neste change (reabertura de trabalho aprovado exigirá contrato UX-02 de change request explícito).
- **Idempotência com namespace por operação:** `UNIQUE (operation, request_id)` garante replay durável sem regenerar UUID/timestamp e sem colisão entre operações distintas (`CREATE_RUN`, `START_RUN`, `BLOCK_RUN`, `UNBLOCK_RUN`, `COMPLETE_RUN`). Repetição com payload diferente retorna `ConflictError` (exit `4`) sem alterar estado.
- **Proteção contra autoafirmação (Regra #2 / ADR-005 / ADR-016):** restrição `CHECK` + `FOREIGN KEY` no próprio DDL impede `COMPLETE_RUN` sem `evidence_id`, e o serviço recusa evidência de outra execução, `FAIL`, `ERROR`, `STALE` ou tipo não verificador (ex.: `REVIEW_REPORT` sozinho nunca conclui uma execução).

---

## 3. Migração explícita v3 → v4 e criação direta v4

- **Criação inicial:**
  - `python3 -m udh init --db PATH` continua criando **SQLite v1** por padrão (preservando 100% dos AT congelados `v1–v6`).
  - `python3 -m udh init --db PATH --schema-version 4` inicializa banco vazio diretamente em **SQLite v4**. Reexecutar `init` em banco já v4 com `--schema-version 4` é idempotente; `init` padrão (v1) sobre banco v4 continua recusando sem downgrade.
- **Migração explícita `v3 → v4`:**
  - `python3 -m udh.lifecycle migrate --db PATH --to 4` aceita exclusivamente banco **v3 exato e íntegro** (migrando para v4 em transação única `BEGIN IMMEDIATE` com `foreign_keys = ON` e `legacy_alter_table = OFF`) ou banco **v4 exato e íntegro** (idempotente, `migrated: false`).
  - Recusa bancos ausentes, v0, v1, v2, versão futura ou corrompidos sem criar, reparar ou encadear migrações silenciosamente (para v1/v2, exigir migrações explícitas anteriores).
  - Preserva 100% dos valores legados: UUIDs, `request_id`, `spec_id`, `spec_version`, estados, revisões `0/1/2`, sequências exatas de eventos, motivos, datas, `authorization_id` e metadados de controle v2/v3 em `legacy_meta_json` (bloqueios legados recebem `block_category = 'ADMIN_PAUSE'`, `reason_code = 'ADMIN_PAUSE'`, `next_action` padrão; execuções legadas recebem `mode = 'STANDARD'`, `ticket_id = NULL`).

---

## 4. Superfície CLI e convivência com v1–v3

Para garantir compatibilidade absoluta com os 50 AT selados (`UnblockAcceptance.test_u8_common_cli_and_policy_limits` exige que `python3 -m udh` recuse `block`, `unblock` e `migrate` com exit `2`):

1. **`python3 -m udh`** ([udh/__main__.py](../../../udh/__main__.py)):
   - Preserva integralmente `init`, `create`, `show`, `policy` em bancos v1–v3.
   - Acrescenta `--schema-version {1,4}` em `init` (padrão `1`), `--ticket-id` e `--mode {FAST,STANDARD,DEEP}` opcionais em `create` (aceitos apenas em bancos v4; recusados em v1–v3), e o subcomando somente leitura **`doctor`** (`[--db PATH]`).
   - **Não** expõe `migrate`, `block` ou `unblock`.
2. **`python3 -m udh.operator`** ([udh/operator.py](../../../udh/operator.py)):
   - **Intocado** neste change; preserva os caminhos administrativos v1/v2/v3 dos selos congelados `v5` e `v6`.
3. **`python3 -m udh.lifecycle`** (`udh/lifecycle.py`, novo módulo do mesmo Core):
   - Exposição autoritativa das operações v4 sobre `CoreService`, sem acoplamento ao caminho fixo `/state/udh.sqlite` do bootstrap:
     - `migrate --db PATH --to 4`
     - `start --db PATH --run-id UUID --expected-revision N --request-id ID`
     - `block --db PATH --run-id UUID --expected-revision N --request-id ID --category CAT --reason-code CODE --reason TEXTO --next-action TEXTO`
     - `unblock --db PATH --run-id UUID --expected-revision N --request-id ID --reason TEXTO`
     - `record-evidence --db PATH --run-id UUID --request-id ID --kind KIND --verdict VERDICT --artifact-digest SHA256 --evidence-sha256 SHA256 --collector-route ROUTE --summary-ref REF [--at-version V] [--commit-sha SHA1]`
     - `complete --db PATH --run-id UUID --expected-revision N --request-id ID --evidence-id UUID`
     - `checkpoint --db PATH --run-id UUID --expected-revision N --request-id ID --last-step STEP [--base-commit SHA1] [--head-commit SHA1] [--worktree-sha256 SHA256] [--evidence-hashes-json JSON] [--external-keys-json JSON] [--unknown-pendencies-json JSON]`
   - Códigos de saída consistentes com o Core: `0` sucesso (JSON UTF-8 único em stdout, stderr vazio), `1` erro de domínio/IO, `2` argumentos inválidos, `3` negação (`DENY` em `policy` ou `NOT_READY` em `doctor`), `4` conflito de estado/revisão/idempotência (`ConflictError`). Sem traceback.

---

## 5. Arquivos permitidos na implementação (após aprovação)

### Código de produto (stdlib-only)
- [udh/domain.py](../../../udh/domain.py) — entidades v4 (`Transition`, `EvidenceRef`, `Checkpoint`, extensão de `Run` para `schema_version = 4`, preservando v1–v3).
- [udh/transitions.py](../../../udh/transitions.py) — contratos de requisição v4 (`StartRequest`, `BlockV4Request`, `UnblockV4Request`, `CompleteRequest`, `EvidenceRequest`, `CheckpointRequest`), preservando v1–v3.
- [udh/migrations.py](../../../udh/migrations.py) — constantes DDL v4, `init_v4`, `validate_schema_v4`, `check_integrity_v4`, `migrate_v4`, preservando v1–v3.
- [udh/store.py](../../../udh/store.py) — leitura `O(1)` em v4, operações transacionais v4 (`create` v4, `start`, `block_v4`, `unblock_v4`, `complete`, `record_evidence`, `record_checkpoint`, `deep_check`), preservando v1–v3.
- [udh/policy.py](../../../udh/policy.py) — `POLICY_VERSION = 2` para v4, preservando `POLICY_VERSION = 1` e hash exato para v1–v3.
- `udh/doctor.py` *(novo)* — diagnóstico de integridade SQLite profunda + preflight Linux/Podman rootless (B1.3).
- [udh/service.py](../../../udh/service.py) — casos de uso v4 e delegação a `doctor`, preservando assinaturas/comportamentos v1–v3.
- [udh/__main__.py](../../../udh/__main__.py) — suporte a `--schema-version`, `--ticket-id`/`--mode` e subcomando `doctor`, preservando recusa de `migrate`/`block`/`unblock`.
- `udh/lifecycle.py` *(novo)* — CLI autoritativa v4 sobre `CoreService`.

### Testes complementares e documentação de fechamento
- `tests/test_v4_ledger.py` *(novo)* — ciclo completo, migração v3→v4, concorrência, idempotência, `O(1)` (prova de ausência de scan global em `show`/`policy`), corrupção detectada em `doctor`, checkpoints F-03 e bloqueio de `DONE` sem evidência `PASS`.
- `tests/test_policy_v2_and_doctor.py` *(novo)* — Política v2 em todos os estados, invariância de Política v1 em v1–v3, e `doctor` (Linux/cgroups/userns/Podman/DB, casos `READY_WITH_LIMITS` e `NOT_READY`).
- Registro compacto de verificação (`<= 32 KiB`) em `openspec/changes/v4-state-ledger-and-preflight/verification-summary.json` + atualização de [STATE.md](../../../STATE.md) e [MEMORY.md](../../../MEMORY.md) ao encerrar.

### Arquivos explicitamente proibidos neste change
- `udh/operator.py` (permanece intocado).
- `bootstrap/slice1/**` (congelado; nenhum selo `v7`, nenhum AT alterado).
- `spike/slice0/**` (descartável/não tocado).
- `Universal-Dev-Harness-v2_5-BASELINE-NORMATIVO-CONSOLIDADO-FINAL.md` (baseline preservado).

---

## 6. Critérios de aceite e pronto do Change 1

| ID | Critério verificável |
| --- | --- |
| V4-01 | `init --schema-version 4` cria banco v4 íntegro; `migrate --to 4` converte v3 (vazio, `PENDING/0`, `BLOCKED/1`, `PENDING/2`) preservando 100% dos dados/sequências/metadados legados e é idempotente; recusa v1/v2/corrompido sem mutação. |
| V4-02 | Ciclo completo repetível `PENDING → IN_PROGRESS → BLOCKED → PENDING → IN_PROGRESS → DONE` com CAS por `expected_revision`, `BEGIN IMMEDIATE`, rollback atômico em falha e idempotência por `(operation, request_id)` (replay estável sem regenerar UUID/data; conflito exit `4` em payload distinto). |
| V4-03 | `BLOCK_RUN` exige categoria ADR-025 (`POLICY_VIOLATION \| FUNCTIONAL_FAILURE \| ADMIN_PAUSE`), `reason_code`, `reason` e `next_action`; `COMPLETE_RUN` exige `evidence_id` do mesmo `run_id` com `verdict='PASS'` e `kind` verificador, recusando `FAIL`/`ERROR`/`STALE`/`REVIEW_REPORT` ou ausência de evidência (tanto em domínio quanto por restrição SQL). |
| V4-04 | `checkpoint` (F-03) persiste commit-base/HEAD, snapshot hash, hashes de evidência, chaves externas e pendências `UNKNOWN`; pendência `UNKNOWN` não vazia bloqueia `START_RUN`, `COMPLETE_RUN` e `Policy v2` (`fail-closed`). |
| V4-05 | **Leitura `O(1)` comprovada em v4:** `show` e `policy` não executam `PRAGMA quick_check`, `PRAGMA foreign_key_check` nem scan em outras execuções (verificado via `set_trace_callback` e `EXPLAIN QUERY PLAN` + banco com centenas de runs). |
| V4-06 | `POLICY_VERSION = 2` em bancos v4 avalia estado/evidência/pendência deterministicamente com `EXECUTE_AGENT = DENY` e `DELIVER_ARTIFACT = DENY`; `POLICY_VERSION = 1` em bancos v1–v3 preserva saída e `policy_sha256` idênticos. |
| V4-07 | `doctor` executa verificação profunda de banco (`quick_check`, `foreign_key_check`, auditoria completa de runs/transitions/checkpoints/evidence_refs) e preflight Linux/Podman rootless, retornando JSON estruturado com `READY_WITH_LIMITS` (gateway/adaptador ausentes até Change 2) ou `NOT_READY` (exit `3`) em falha de host/DB. |
| R1 | Regressão completa: **98/98** testes complementares existentes + novos testes v4 PASS; **50/50** AT selados congelados (`v1`, `v2`, `v5`, `v6`) PASS em Podman rootless sem alterar nenhum selo/AT. |
| R2 | Revisão independente (F-02) em contexto separado (`research`) sobre o commit candidato, sem achados abertos; evidência registrada em resumo compacto (`<= 32 KiB`). |

---

## 7. Suposições explícitas

1. A tabela `tickets` (multi-ticket DAG de `PLAN/TICKET-01/02`) é adiada propositalmente para ser adicionada sem reconstrução de tabelas quando o fluxo `PLAN` for especificado; `runs.ticket_id` cobre integralmente o 1º ticket ponta a ponta do Change 2.
2. `doctor` neste change valida o domínio Linux/Podman rootless e o SQLite, mas **não** pode reportar `READY` pleno para execução de agentes porque o gateway e o adaptador Pi 1.2.0 só entram no Change 2; portanto o teto honesto de `doctor` neste change é `READY_WITH_LIMITS` (quando host e DB estão íntegros) ou `NOT_READY`.
3. Nenhuma implementação começa antes de sua aprovação explícita deste change.
