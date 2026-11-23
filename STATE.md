# Estado do projeto

Atualizado: 23/11/2026. Fase: **Change 1 (`v4-state-ledger-and-preflight`) concluído e verificado (`9f84150`; publicado em `origin/main` em `5889bce`, com docs locais atualizados)**; pronto para especificar e executar o **Change 2** (Runtime Manager, Gateway, Pi 1.2.0, Executor F-04 e `POC-01`/`02`/`04`).

Retomada: [AGENTS.md](AGENTS.md), este estado e [MEMORY.md](MEMORY.md). Fonte normativa: [baseline rastreado](Universal-Dev-Harness-v2_5-BASELINE-NORMATIVO-CONSOLIDADO-FINAL.md) + ratificação em [openspec/project.md](openspec/project.md); [v2.5.1](UDH-v2.5-ARQUITETURA-CONSOLIDADA-v2.5.1.md) é nota derivada.

## Funciona

- **Core SQLite v1–v4 (`9f84150`, stdlib-only):** ledger unificado `transitions` (`PENDING ↔ IN_PROGRESS ↔ BLOCKED → DONE` repetível), `evidence_refs` (SQL + domínio bloqueiam `DONE` sem evidência oficial `PASS`), `checkpoints` F-03 (pendência `UNKNOWN` falha fechado), leitura `O(1)` via índices B-tree sem `quick_check`/full-scan em `show`/`policy` v4, migração atômica `v3 → v4` e `python3 -m udh.lifecycle`.
- **Política v2 + Preflight `doctor` (`udh/doctor.py`):** `POLICY_VERSION = 2` sensível a estado/evidência/pendência em v4 (mantendo `POLICY_VERSION = 1` congelada em v1–v3); `python3 -m udh doctor` validado ao vivo (`READY_WITH_LIMITS`: Linux nativo x86_64, cgroup v2, userns/subid e Podman 5.4.2 rootless `READY`; gateway/adaptadores `LIMITED`).
- **Verificação completa em `9f84150`:** **110/110** complementares PASS (11,6 s, incluindo Podman); **50/50** AT selados congelados PASS em Podman rootless (`v6` 16/16, `v5` 13/13, `v2` 11/11, `v1` 10/10; 714 chamadas auditadas, 0 erros/infra); revisão independente R2/F-02 PASS sem achados abertos. Resumo: [verification-summary.json](openspec/changes/v4-state-ledger-and-preflight/verification-summary.json). `udh/operator.py` e `bootstrap/slice1/` intocados.

## Pendente

- **Change 2 (próximo marco):** Runtime Manager rootless com imagem OCI dedicada por digest (Node + Pi 1.2.0 + Python 3 stdlib), Gateway de inferência stdlib endurecido (UDS/`SO_PEERCRED`, dublê determinístico + OpenRouter `:free` com `max_price=0`), adaptador Pi 1.2.0, store protegida do Core + Executor Confiável F-04 e execução de `POC-01`, `POC-02` e `POC-04`.
- **Restante do produto:** `POC-03` + 2º/3º agentes, `DISCOVER`/`SPEC`/`PLAN` + multi-ticket DAG (`TICKET-01/02`), perfil `provision` (B2.4), preview web (B3.6), interface guiada, Memorix, Judge consultivo e instalador. `POC-01`–`POC-04` e `DOC`/`SKILL`/`DISC`/`TICKET` seguem `NOT_RUN`.

## Próxima tarefa

Especificar o OpenSpec do **Change 2 (`vertical-runtime-pi-gateway-poc`)** conforme decisões já alinhadas no `/grill-me` (imagem OCI local por digest, Gateway stdlib endurecido, stack Python 3 stdlib, store protegida + Executor F-04) para aprovação antes de implementar.

## Verificação

- `PYTHONDONTWRITEBYTECODE=1 TMPDIR="$PWD/bootstrap/slice1" python3 -m unittest discover -s tests` (110 PASS).
- `python3 -m udh doctor` (`READY_WITH_LIMITS`).
- [README](README.md) para entrada. Não abrir credenciais, não limpar `spike/slice0/workspace/` sem autorização nem declarar entrega/segurança sem POCs reais.
