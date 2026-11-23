# Design Técnico — SQLite v4, Leitura O(1), Política v2 e Preflight `doctor`

Documento técnico complementar a [proposal.md](proposal.md). Nenhum código de produto é alterado por este arquivo.

---

## 1. DDL exato do SQLite v4 (`PRAGMA user_version = 4`)

Todos os objetos abaixo foram validados em ensaio `:memory:` no SQLite `3.46.1` (criação direta e migração `v3 → v4` com `PRAGMA foreign_keys = ON` e `PRAGMA legacy_alter_table = OFF`), confirmando correspondência literal em `sqlite_master.sql`, `PRAGMA quick_check = ok`, `PRAGMA foreign_key_check = []` e uso de índices B-tree nos planos de consulta (`EXPLAIN QUERY PLAN`).

```sql
CREATE TABLE "runs" (
    id TEXT PRIMARY KEY NOT NULL,
    request_id TEXT NOT NULL UNIQUE CHECK (length(trim(request_id, char(9)||char(10)||char(11)||char(12)||char(13)||' ')) > 0),
    spec_id TEXT NOT NULL CHECK (length(trim(spec_id, char(9)||char(10)||char(11)||char(12)||char(13)||' ')) > 0),
    spec_version TEXT NOT NULL CHECK (length(trim(spec_version, char(9)||char(10)||char(11)||char(12)||char(13)||' ')) > 0),
    ticket_id TEXT CHECK (ticket_id IS NULL OR length(trim(ticket_id, char(9)||char(10)||char(11)||char(12)||char(13)||' ')) > 0),
    mode TEXT NOT NULL CHECK (mode IN ('FAST', 'STANDARD', 'DEEP')),
    state TEXT NOT NULL CHECK (state IN ('PENDING', 'IN_PROGRESS', 'BLOCKED', 'DONE')),
    created_at TEXT NOT NULL CHECK (length(created_at) >= 20 AND substr(created_at, -1) = 'Z' AND julianday(created_at) IS NOT NULL),
    updated_at TEXT NOT NULL CHECK (length(updated_at) >= 20 AND substr(updated_at, -1) = 'Z' AND julianday(updated_at) IS NOT NULL),
    revision INTEGER NOT NULL CHECK (typeof(revision) = 'integer' AND revision >= 0)
);

CREATE TABLE "evidence_refs" (
    evidence_id TEXT PRIMARY KEY NOT NULL,
    run_id TEXT NOT NULL REFERENCES "runs"(id),
    request_id TEXT NOT NULL UNIQUE CHECK (length(trim(request_id, char(9)||char(10)||char(11)||char(12)||char(13)||' ')) > 0),
    kind TEXT NOT NULL CHECK (kind IN ('AT_RESULT', 'SMOKE_RESULT', 'POC_RESULT', 'REVIEW_REPORT', 'ARTIFACT_PROMOTION')),
    verdict TEXT NOT NULL CHECK (verdict IN ('PASS', 'FAIL', 'ERROR', 'STALE')),
    artifact_digest TEXT NOT NULL CHECK (length(artifact_digest) = 64 AND artifact_digest NOT GLOB '*[^0-9a-f]*'),
    evidence_sha256 TEXT NOT NULL CHECK (length(evidence_sha256) = 64 AND evidence_sha256 NOT GLOB '*[^0-9a-f]*'),
    at_version TEXT CHECK (at_version IS NULL OR length(trim(at_version, char(9)||char(10)||char(11)||char(12)||char(13)||' ')) > 0),
    commit_sha TEXT CHECK (commit_sha IS NULL OR (length(commit_sha) = 40 AND commit_sha NOT GLOB '*[^0-9a-f]*')),
    collector_route TEXT NOT NULL CHECK (collector_route IN ('TRUSTED_EXECUTOR', 'OPERATOR_LOCAL')),
    summary_ref TEXT NOT NULL CHECK (length(trim(summary_ref, char(9)||char(10)||char(11)||char(12)||char(13)||' ')) > 0),
    created_at TEXT NOT NULL CHECK (length(created_at) >= 20 AND substr(created_at, -1) = 'Z' AND julianday(created_at) IS NOT NULL)
);

CREATE TABLE "transitions" (
    sequence INTEGER PRIMARY KEY CHECK (sequence > 0),
    run_id TEXT NOT NULL REFERENCES "runs"(id),
    revision INTEGER NOT NULL CHECK (typeof(revision) = 'integer' AND revision >= 0),
    operation TEXT NOT NULL CHECK (operation IN ('CREATE_RUN', 'START_RUN', 'BLOCK_RUN', 'UNBLOCK_RUN', 'COMPLETE_RUN')),
    kind TEXT NOT NULL CHECK (kind IN ('RUN_CREATED', 'RUN_STARTED', 'RUN_BLOCKED', 'RUN_UNBLOCKED', 'RUN_COMPLETED')),
    from_state TEXT CHECK (from_state IS NULL OR from_state IN ('PENDING', 'IN_PROGRESS', 'BLOCKED')),
    to_state TEXT NOT NULL CHECK (to_state IN ('PENDING', 'IN_PROGRESS', 'BLOCKED', 'DONE')),
    request_id TEXT NOT NULL CHECK (length(trim(request_id, char(9)||char(10)||char(11)||char(12)||char(13)||' ')) > 0),
    created_at TEXT NOT NULL CHECK (length(created_at) >= 20 AND substr(created_at, -1) = 'Z' AND julianday(created_at) IS NOT NULL),
    reason TEXT CHECK (reason IS NULL OR length(trim(reason, char(9)||char(10)||char(11)||char(12)||char(13)||' ')) > 0),
    reason_code TEXT CHECK (reason_code IS NULL OR length(trim(reason_code, char(9)||char(10)||char(11)||char(12)||char(13)||' ')) > 0),
    block_category TEXT CHECK (block_category IS NULL OR block_category IN ('POLICY_VIOLATION', 'FUNCTIONAL_FAILURE', 'ADMIN_PAUSE')),
    next_action TEXT CHECK (next_action IS NULL OR length(trim(next_action, char(9)||char(10)||char(11)||char(12)||char(13)||' ')) > 0),
    authorization_id TEXT UNIQUE,
    actor_route TEXT NOT NULL CHECK (actor_route IN ('CORE_LOCAL', 'OPERATOR_LOCAL', 'TRUSTED_EXECUTOR')),
    evidence_id TEXT REFERENCES "evidence_refs"(evidence_id),
    legacy_meta_json TEXT,
    CHECK ((operation = 'CREATE_RUN' AND kind = 'RUN_CREATED' AND from_state IS NULL AND to_state = 'PENDING' AND revision = 0 AND block_category IS NULL AND evidence_id IS NULL)
        OR (operation = 'START_RUN' AND kind = 'RUN_STARTED' AND from_state = 'PENDING' AND to_state = 'IN_PROGRESS' AND revision > 0 AND block_category IS NULL AND evidence_id IS NULL)
        OR (operation = 'BLOCK_RUN' AND kind = 'RUN_BLOCKED' AND from_state IN ('PENDING', 'IN_PROGRESS') AND to_state = 'BLOCKED' AND revision > 0 AND block_category IS NOT NULL AND reason IS NOT NULL AND reason_code IS NOT NULL AND next_action IS NOT NULL AND evidence_id IS NULL)
        OR (operation = 'UNBLOCK_RUN' AND kind = 'RUN_UNBLOCKED' AND from_state = 'BLOCKED' AND to_state = 'PENDING' AND revision > 0 AND reason IS NOT NULL AND block_category IS NULL AND evidence_id IS NULL)
        OR (operation = 'COMPLETE_RUN' AND kind = 'RUN_COMPLETED' AND from_state = 'IN_PROGRESS' AND to_state = 'DONE' AND revision > 0 AND block_category IS NULL AND evidence_id IS NOT NULL)),
    UNIQUE (run_id, revision),
    UNIQUE (operation, request_id)
);

CREATE TABLE "checkpoints" (
    checkpoint_id TEXT PRIMARY KEY NOT NULL,
    run_id TEXT NOT NULL REFERENCES "runs"(id),
    run_revision INTEGER NOT NULL CHECK (typeof(run_revision) = 'integer' AND run_revision >= 0),
    request_id TEXT NOT NULL UNIQUE CHECK (length(trim(request_id, char(9)||char(10)||char(11)||char(12)||char(13)||' ')) > 0),
    last_confirmed_step TEXT NOT NULL CHECK (length(trim(last_confirmed_step, char(9)||char(10)||char(11)||char(12)||char(13)||' ')) > 0),
    base_commit_sha TEXT CHECK (base_commit_sha IS NULL OR (length(base_commit_sha) = 40 AND base_commit_sha NOT GLOB '*[^0-9a-f]*')),
    head_commit_sha TEXT CHECK (head_commit_sha IS NULL OR (length(head_commit_sha) = 40 AND head_commit_sha NOT GLOB '*[^0-9a-f]*')),
    worktree_snapshot_sha256 TEXT CHECK (worktree_snapshot_sha256 IS NULL OR (length(worktree_snapshot_sha256) = 64 AND worktree_snapshot_sha256 NOT GLOB '*[^0-9a-f]*')),
    evidence_hashes_json TEXT NOT NULL,
    external_idempotency_keys_json TEXT NOT NULL,
    unknown_pendencies_json TEXT NOT NULL,
    created_at TEXT NOT NULL CHECK (length(created_at) >= 20 AND substr(created_at, -1) = 'Z' AND julianday(created_at) IS NOT NULL)
);

CREATE INDEX "idx_evidence_refs_run_id" ON "evidence_refs" (run_id, created_at);
CREATE INDEX "idx_checkpoints_run_id" ON "checkpoints" (run_id, run_revision, created_at);
```

### Objetos esperados em `sqlite_master` (v4)
Exatamente 4 tabelas (`runs`, `evidence_refs`, `transitions`, `checkpoints`) e 2 índices explícitos (`idx_evidence_refs_run_id`, `idx_checkpoints_run_id`), além de índices automáticos `sqlite_*`. Qualquer trigger, tabela extra ou SQL divergente é recusado.

---

## 2. Migração `v3 → v4` em transação única

1. Abrir banco existente (`mode=rw`), `PRAGMA foreign_keys = ON`, `PRAGMA legacy_alter_table = OFF`, `BEGIN IMMEDIATE`.
2. Validar esquema/versão atual:
   - Se `user_version == 4`: validar esquema v4 + `check_integrity_v4()`, fazer `commit` e retornar `{"schema_version": 4, "migrated": false}`.
   - Se `user_version != 3`: recusar sem modificar o banco (não encadeia v1/v2 silenciosamente).
   - Validar v3 integral (`validate_schema(con, 3)` + `check_integrity(con, store, 3)`).
3. Capturar snapshots ordenados de `runs`, `events`, `block_requests`, `unblock_requests`.
4. Criar `runs_v4`, `evidence_refs`, `transitions`, `checkpoints` (referenciando `"runs_v4"`).
5. Migrar linhas preservando sequências exatas de `events.sequence`, timestamps, motivos, UUIDs e metadados legados em `legacy_meta_json` (JSON canônico `sort_keys=True, separators=(',',':')`):
   - `RUN_CREATED` → `operation='CREATE_RUN'`, `revision=0`, `from_state=NULL`, `to_state='PENDING'`, `request_id=run.request_id`, `actor_route='CORE_LOCAL'`.
   - `RUN_BLOCKED` → `operation='BLOCK_RUN'`, `revision=1`, `from_state='PENDING'`, `to_state='BLOCKED'`, `block_category='ADMIN_PAUSE'`, `reason_code='ADMIN_PAUSE'`, `next_action='Retire a pausa administrativa quando for seguro continuar.'`, `legacy_meta_json` com `core_manifest_sha256`, `control_sha256`, `authorization_rule_version`, `result_schema_version`.
   - `RUN_UNBLOCKED` → `operation='UNBLOCK_RUN'`, `revision=2`, `from_state='BLOCKED'`, `to_state='PENDING'`, `legacy_meta_json` com `core_manifest_sha256`, `control_sha256`, `authorization_rule_version`, `blocked_event_sequence`.
6. Remover tabelas antigas de filhas para pais: `DROP TABLE "unblock_requests"`, `DROP TABLE "block_requests"`, `DROP TABLE "events"`, `DROP TABLE "runs"`.
7. Renomear `ALTER TABLE "runs_v4" RENAME TO "runs"` (SQLite reescreve as FKs de `evidence_refs`, `transitions` e `checkpoints` para `"runs"` mantendo aspas duplas).
8. Criar índices `idx_evidence_refs_run_id` e `idx_checkpoints_run_id`.
9. Validar `validate_schema_v4`, `check_integrity_v4`, contagens e igualdade dos registros migrados.
10. Executar `PRAGMA user_version = 4` e único `commit` (qualquer exceção executa `rollback` integral para v3).

---

## 3. Leitura `O(1)` e invariantes do leitor v4

### Caminho quente (`show`, `policy`, operações de 1 execução em v4)
- Executa **apenas**:
  1. `PRAGMA user_version` (`== 4`) + `validate_schema_v4(connection)` (leitura constante de `sqlite_master`, independente do número de execuções).
  2. `SELECT * FROM "runs" WHERE id = ?` (PRIMARY KEY).
  3. `SELECT * FROM "transitions" WHERE run_id = ? ORDER BY revision` (`SEARCH transitions USING INDEX sqlite_autoindex_transitions_2 (run_id=?)`).
  4. `SELECT * FROM "evidence_refs" WHERE run_id = ? ORDER BY created_at, evidence_id` (`SEARCH evidence_refs USING INDEX idx_evidence_refs_run_id (run_id=?)`).
  5. `SELECT * FROM "checkpoints" WHERE run_id = ? ORDER BY run_revision, created_at, checkpoint_id` (`SEARCH checkpoints USING INDEX idx_checkpoints_run_id (run_id=?)`).
- **Proibido no caminho quente v4:** `PRAGMA quick_check`, `PRAGMA foreign_key_check` e `SELECT id FROM runs`.
- **Invariantes validadas em domínio para a execução carregada:**
  - `require_run_id(run.id) == run.id`; timestamps UTC RFC 3339 válidos; `mode` e `state` válidos.
  - `transitions` não vazia; `transitions[0].operation == 'CREATE_RUN'` e `revision == 0`; revisões contíguas `0, 1, ..., run.revision`; `sequence` estritamente crescente; cadeia de estados coerente (`transitions[i].from_state == transitions[i-1].to_state` para todo `i >= 1`); `transitions[-1].to_state == run.state`; `run.updated_at == transitions[-1].created_at`.
  - Se houver `COMPLETE_RUN`: `evidence_id` aponta para item em `evidence_refs` **da mesma execução**, com `verdict == 'PASS'` e `kind in ('AT_RESULT', 'SMOKE_RESULT', 'POC_RESULT')`.
  - Cada `checkpoint` valida JSON bem-formado de listas (`evidence_hashes_json`, `external_idempotency_keys_json`, `unknown_pendencies_json`), `run_revision <= run.revision`, UUIDs canônicos e hashes válidos.

### Caminho profundo (`doctor --db PATH` e `migrate --to 4`)
- Para v1–v3: reutiliza `validate_schema` + `check_integrity`.
- Para v4: executa `validate_schema_v4`, `PRAGMA quick_check`, `PRAGMA foreign_key_check`, auditoria completa de todas as linhas de `runs` via `_load_v4`, e verificação de órfãos/inconsistências entre tabelas.

---

## 4. Transações, idempotência e F-03 em v4

- **Compare-and-set + replay durável:** todas as mutações (`start`, `block_v4`, `unblock_v4`, `complete`, `record_evidence`, `record_checkpoint`) abrem `mode=rw` com `BEGIN IMMEDIATE`.
  - Antes de checar o estado atual, buscam `request_id` (em `transitions` por `(operation, request_id)`, ou em `evidence_refs`/`checkpoints` por `request_id`).
  - Se existir e o payload lógico (incluindo `run_id`, `expected_revision` quando aplicável, campos semânticos literais) for idêntico: devolvem o registro persistido **sem** gerar novo UUID/timestamp e **sem** mutar o banco.
  - Se existir com payload diferente: `ConflictError` (exit `4`).
  - Se novo: validam estado/revisão esperada, checam pré-condições F-03 (nenhuma pendência `UNKNOWN` não vazia no último checkpoint para `START_RUN`/`COMPLETE_RUN`), inserem o registro e atualizam `runs` com `WHERE id = ? AND state = ? AND revision = ?` (`rowcount == 1`).
- **Pendências `UNKNOWN` (F-03):**
  - `checkpoints.unknown_pendencies_json` armazena lista JSON de strings/objetos descrevendo efeitos externos de resultado desconhecido.
  - Um novo checkpoint com `unknown_pendencies_json = '[]'` (após reconciliação explícita) libera novamente o avanço.

---

## 5. Política determinística v2 (`udh/policy.py`)

- Se `run.schema_version in (1, 2, 3)`: executa exatamente `POLICY_VERSION = 1` (corpo, regras e `policy_sha256` intocados).
- Se `run.schema_version == 4`: executa `POLICY_VERSION = 2` (pura, sem I/O):
  - Calcula `policy_sha256` sobre a tabela canônica de regras v2 (`sort_keys=True, separators=(',', ':')`).
  - Decisões por ação:
    - `READ_RUN`: `ALLOW` (`READ_ONLY_METADATA`).
    - `TRANSITION_RUN`:
      - `state == 'DONE'` → `DENY` (`RUN_TERMINAL_DONE`).
      - `has_unknown_pendencies` → `DENY` (`UNKNOWN_PENDENCY_ACTIVE`).
      - `state in ('PENDING', 'IN_PROGRESS', 'BLOCKED')` → `ALLOW` (`LIFECYCLE_TRANSITION_AVAILABLE`).
    - `EXECUTE_AGENT`:
      - `state != 'IN_PROGRESS'` → `DENY` (`RUN_NOT_IN_PROGRESS`).
      - `has_unknown_pendencies` → `DENY` (`UNKNOWN_PENDENCY_ACTIVE`).
      - `preflight_status == 'NOT_READY'` → `DENY` (`PREFLIGHT_NOT_READY`).
      - Caso contrário → `DENY` (`GATEWAY_AND_ADAPTER_NOT_READY`, mensagem clara indicando dependência do Change 2).
    - `ACCEPT_ARTIFACT`:
      - `state != 'IN_PROGRESS'` → `DENY` (`RUN_NOT_IN_PROGRESS`).
      - `has_unknown_pendencies` → `DENY` (`UNKNOWN_PENDENCY_ACTIVE`).
      - `has_passing_evidence` → `ALLOW` (`OFFICIAL_EVIDENCE_VERIFIED`).
      - Caso contrário → `DENY` (`MISSING_OFFICIAL_EVIDENCE`).
    - `DELIVER_ARTIFACT`: `DENY` (`DELIVERY_GATE_UNAVAILABLE`).
    - Outra ação: `DENY` (`UNKNOWN_ACTION`).

---

## 6. Contrato do preflight `doctor` (`udh/doctor.py`)

Comando: `python3 -m udh doctor [--db PATH]` (somente leitura; nunca cria nem modifica o banco).

Verificações determinísticas (stdlib-only):
1. `platform`: Linux x86_64, `platform.release()`, detecção de WSL2 via `/proc/version` (sem sugerir WSL2 em Linux nativo — B1.1(e)/B1.3).
2. `cgroups`: presença e legibilidade de `/sys/fs/cgroup/cgroup.controllers` (cgroup v2).
3. `userns`: leitura de `/proc/sys/kernel/unprivileged_userns_clone` (quando presente) e entradas do usuário atual em `/etc/subuid` e `/etc/subgid`.
4. `podman`: localização de `podman` no `PATH` e execução somente leitura `podman info --format json` (com timeout finito, ex.: 5 s) confirmando `host.security.rootless == true`. Falhas (ex.: permissão/ambiente restrito) geram `status: 'NOT_READY'` com `reason_code: 'PODMAN_ROOTLESS_UNAVAILABLE'` e ação segura em pt-BR.
5. `database` *(somente se `--db PATH` fornecido)*: executa validação profunda (`validate_schema`, `quick_check`, `foreign_key_check`, auditoria completa de integridade) em `mode=ro`. Ausência ou corrupção gera `status: 'NOT_READY'`, `reason_code: 'DATABASE_INTEGRITY_FAILED'`.
6. `runtime_components`: reporta `status: 'LIMITED'`, `reason_code: 'GATEWAY_AND_ADAPTER_NOT_CONFIGURED'` (explicitando que Runtime Manager, Gateway e adaptador Pi 1.2.0 pertencem ao Change 2).

Regra de agregação (`overall_status`):
- Se qualquer check obrigatório (`platform`, `cgroups`, `userns`, `podman`, ou `database` quando informado) estiver `NOT_READY` → `overall_status = 'NOT_READY'` (exit `3`).
- Caso contrário, se algum check estiver `LIMITED` (como `runtime_components` no Change 1) → `overall_status = 'READY_WITH_LIMITS'` (exit `0`).
- Caso contrário → `overall_status = 'READY'` (exit `0`).

---

## 7. Ensaio de preparo (SQLite 3.46.1 em `:memory:`)

Executado em 23/11/2026 sem alterar arquivos de produto:
- Criação direta v4 + ciclo completo `CREATE → START → BLOCK → UNBLOCK → START → COMPLETE` + rejeição de `COMPLETE_RUN` sem `evidence_id` por `IntegrityError`: **PASS**.
- Migração `v3 → v4` (banco vazio e banco populado com `PENDING/0`, `BLOCKED/1` e `PENDING/2`, inclusive reutilizando mesmo `request_id` em namespaces distintos): **PASS** (`quick_check = ok`, `foreign_key_check = []`, correspondência literal em `sqlite_master`).
- Planos de consulta verificados:
  - `SEARCH transitions USING INDEX sqlite_autoindex_transitions_2 (run_id=?)`
  - `SEARCH evidence_refs USING INDEX idx_evidence_refs_run_id (run_id=?)`
  - `SEARCH checkpoints USING INDEX idx_checkpoints_run_id (run_id=?)`
