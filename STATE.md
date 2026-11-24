# Estado do projeto

Atualizado: 24/11/2026. Fase: **Change 4 (`artifact-preview-and-guided-ui`) concluído, verificado (`20150ac`) e integrado em `main`** — imagem OCI dedicada `artifact-only` sem agentes (`B3.1`), transporte `/preview-ipc/preview.sock` anti-TOCTOU + `SO_PEERCRED`/netns + proxy isolado `p-<id>.localhost` + CSP (`B3.6`), smoke test oficial com persistência pós-reinício (`B3.4`) e derivação `ADR-028` (`PENDING→DELIVERED`), e interface guiada mínima local (`B1.7`, `UX-01`, `UX-03`, `ADR-025`, `python3 -m udh.web`, 100% Python stdlib).

Retomada: [AGENTS.md](AGENTS.md), este estado e [MEMORY.md](MEMORY.md). Fonte normativa: [baseline rastreado](Universal-Dev-Harness-v2_5-BASELINE-NORMATIVO-CONSOLIDADO-FINAL.md) + ratificação em [openspec/project.md](openspec/project.md); [v2.5.1](UDH-v2.5-ARQUITETURA-CONSOLIDADA-v2.5.1.md) é nota derivada.

## Funciona

- **Core SQLite v1–v4 (stdlib-only):** ledger `transitions`, `evidence_refs`, checkpoints F-03, `list_runs`/`count_runs_by_state`, leitura `O(1)` em v4, Política v2 e derivação/entrega `ADR-028` (`promote_run_artifact`, `delivery_summary`, `deliver_run`).
- **3 imagens OCI separadas por digest (`udh/runtime.py`, `runtime/`):** Pi 1.2.0 (`localhost/udh-runtime@sha256:bed9a71a...`), OpenCode 1.18.35 (`localhost/udh-runtime-opencode@sha256:7af7a5d4...`) e **`artifact-only` sem agentes** (`localhost/udh-runtime-artifact@sha256:0eda5898...`, Python 3 stdlib + `at_runner.py` + `preview_bridge.py`).
- **Gateway + 2 adaptadores conformes + Handoff F-03 (`udh/gateway.py`, `udh/adapters/`, `udh/handoff.py`):** `doctor --conformance` **`READY`** (`['pi', 'opencode']`, ambos `zero_tools`) e **4/4 POCs fundacionais (`POC-01`–`POC-04`) PASS**.
- **Preview isolado `B3.6` + Smoke `B3.4` + Interface guiada `B1.7` (`udh/preview.py`, `udh/smoke.py`, `udh/explanations.py`, `udh/web_ui.py`, `udh/web.py`):** UDS `/preview-ipc/preview.sock` anti-TOCTOU (`O_PATH|O_NOFOLLOW` + `/proc/self/fd` + `SO_PEERCRED` + `ns/net != host`), subdomínio único `p-<24hex>.localhost`, grant one-time → cookie host-only `HttpOnly; SameSite=Lax`, CSP `style-src 'self'`, `POST /api/previews/<pid>/grant` (fallback iframe→aba), UI em `127.0.0.1` com `X-UDH-Token` obrigatório em `/api/*` (zero cookies, zero `innerHTML`), cartões `ADR-025` (4 campos, zero bypass) e checagem offline em **Mozilla Firefox 153.7.0esr** (`PASS`).
- **Verificação completa em `20150ac`:** **157/157** complementares PASS (79,8 s); **50/50** AT selados congelados PASS (`v6` 16/16, `v5` 13/13, `v2` 11/11, `v1` 10/10; 714 chamadas, 0 infra); **`POC-01`–`POC-04` 4/4 PASS** (21,4 s); revisão independente R2/F-02 **PASS** ([resumo](openspec/changes/artifact-preview-and-guided-ui/verification-summary.json)). `udh/operator.py`, `udh/policy.py`, `udh/__main__.py`, `bootstrap/slice1/`, `runtime/image-manifest.json` e `runtime/opencode-image-manifest.json` intocados.

## Pendente

- **Change 5 em andamento:** T2 valida contratos e dependências (SQLite v8 opt-in). T3 filtra perguntas, acompanha lacunas com confirmação humana em memória e valida snapshots com OpenSpec 2.1.1. Aceite oficial de `TICKET-01`, `DISC-01` e `TICKET-02` pendentes.
- **Restante do produto:** perfil `provision` (`B2.4`), execução protegida persistente (`B3.8`) e exportação (`B3.9`), `DOC-01/02`, `SKILL-01/02`, `UX-04`, terceiro agente (Codex), WSL2 (`B1.1b`), Chromium/Chrome, Memorix, Judge consultivo e instalador. Ainda não é MVP liberável completo.

## Próxima tarefa

Na branch `feat/change5-discover-plan-tickets`, qualificar a inferência offline e aprovar [T3b.2b](openspec/changes/discover-plan-tickets/t3-discovery-and-spec-handoff.md#t3b2b--integrar-o-agente-e-executar-o-encaminhamento-real). A adaptação `grill-with-docs-udh` foi conferida. T3-org adiada.

## Verificação

- `UDH_TEST_OPENSPEC="$(command -v openspec)" PYTHONDONTWRITEBYTECODE=1 TMPDIR="$PWD/bootstrap/slice1" python3 -m unittest discover -s tests` (210 PASS, incluindo CLI real; AT selados e R2/F-02 pendentes para o fechamento).
- `PYTHONDONTWRITEBYTECODE=1 python3 -m udh doctor --conformance` (`READY`: `['pi', 'opencode']`), `PYTHONDONTWRITEBYTECODE=1 python3 -m udh.poc` (4/4 PASS) e `python3 -m udh.web --db <path> --store <path>`.
- [README](README.md) para entrada. Não abrir credenciais nem limpar `spike/slice0/workspace/` sem autorização.
