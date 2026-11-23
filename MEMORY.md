# UDH — retomada curta

Atualizado: 23/11/2026. Ler [AGENTS.md](AGENTS.md), [STATE.md](STATE.md) e conferir Git. Memória não aprova requisitos nem substitui evidências.

## Estado confirmado
- **Change 1 (`v4-state-ledger-and-preflight`) concluído e verificado em `9f84150`:** ledger unificado SQLite v4 (`PENDING ↔ IN_PROGRESS ↔ BLOCKED → DONE`), `evidence_refs`, `checkpoints` F-03, leitura `O(1)` em `show`/`policy` v4, Política v2 (v1 congelada em v1–v3), `python3 -m udh.lifecycle` e `python3 -m udh doctor` (ao vivo: `READY_WITH_LIMITS`, Podman 5.4.2 rootless OK).
- **Evidências em `9f84150`:** **110/110** complementares PASS, **50/50** AT selados congelados PASS (`v6` 16/16, `v5` 13/13, `v2` 11/11, `v1` 10/10; 714 chamadas, 0 infra) e revisão independente R2/F-02 **PASS** ([resumo](openspec/changes/v4-state-ledger-and-preflight/verification-summary.json)). `udh/operator.py` e `bootstrap/slice1/` intocados.
- Governança ratificada em [openspec/project.md](openspec/project.md) (B1–B4, Emenda ADR-018 A, congelamento arquitetural, `bootstrap/slice1/` congelado, resumos `<= 32 KiB`).

## Próximo passo
- Especificar o OpenSpec do **Change 2 (`vertical-runtime-pi-gateway-poc`)** já alinhado no `/grill-me`: Runtime Manager rootless com imagem OCI dedicada por digest (Node + Pi 1.2.0 + Python 3 stdlib), Gateway stdlib endurecido (dublê + OpenRouter `:free`), adaptador Pi 1.2.0, store protegida + Executor F-04 e `POC-01`/`02`/`04`.

## Cuidados
- `POC-01`–`POC-04`, `DOC`/`SKILL`/`DISC`/`TICKET` seguem `NOT_RUN`; Core v4 não certifica segurança nem entrega de produto.
- Não alterar selos `v1`–`v6`, AT protegidos nem `udh/operator.py`; não commitar JSONs crus grandes.
- Não abrir credenciais nem limpar `spike/slice0/workspace/` sem autorização. Regras em [AGENTS.md](AGENTS.md): 1 branch por change (a partir do Change 2, `main` sempre verde) e atualizar docs ativos a cada commit. Sem skills globais; commits em português.
