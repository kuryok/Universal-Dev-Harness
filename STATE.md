# Estado do projeto

Atualizado: 25/11/2026. Fase: **Change 4 (`artifact-preview-and-guided-ui`) integrado em `main` (`20150ac`)** — imagem `artifact-only` (`B3.1`), preview isolado `p-<id>.localhost` sobre UDS anti-TOCTOU + CSP (`B3.6`), smoke `B3.4`/`ADR-028` e UI local (`B1.7`, `UX-01`/`03`, `ADR-025`, `python3 -m udh.web`, stdlib).

Retomada: [AGENTS.md](AGENTS.md), este estado e [MEMORY.md](MEMORY.md). Normativo: [baseline](Universal-Dev-Harness-v2_5-BASELINE-NORMATIVO-CONSOLIDADO-FINAL.md) + [openspec/project.md](openspec/project.md); [v2.5.1](UDH-v2.5-ARQUITETURA-CONSOLIDADA-v2.5.1.md) é nota derivada.

## Funciona

- **Core SQLite v1–v4 (stdlib-only):** ledger `transitions`, `evidence_refs`, checkpoints F-03, `list_runs`/`count_runs_by_state`, leitura `O(1)` em v4, Política v2 e derivação/entrega `ADR-028` (`promote_run_artifact`, `delivery_summary`, `deliver_run`).
- **3 imagens OCI separadas por digest (`udh/runtime.py`, `runtime/`):** Pi 1.2.0 (`localhost/udh-runtime@sha256:bed9a71a...`), OpenCode 1.18.35 (`localhost/udh-runtime-opencode@sha256:7af7a5d4...`) e **`artifact-only` sem agentes** (`localhost/udh-runtime-artifact@sha256:0eda5898...`, Python 3 stdlib + `at_runner.py` + `preview_bridge.py`).
- **Gateway + 2 adaptadores conformes + Handoff F-03 (`udh/gateway.py`, `udh/adapters/`, `udh/handoff.py`):** `doctor --conformance` **`READY`** (`['pi', 'opencode']`, ambos `zero_tools`) e **4/4 POCs fundacionais (`POC-01`–`POC-04`) PASS**.
- **Preview isolado `B3.6` + Smoke `B3.4` + Interface guiada `B1.7` (`udh/preview.py`, `udh/smoke.py`, `udh/explanations.py`, `udh/web_ui.py`, `udh/web.py`):** UDS `/preview-ipc/preview.sock` anti-TOCTOU (`O_PATH|O_NOFOLLOW` + `/proc/self/fd` + `SO_PEERCRED` + `ns/net != host`), subdomínio único `p-<24hex>.localhost`, grant one-time → cookie host-only `HttpOnly; SameSite=Lax`, CSP `style-src 'self'`, `POST /api/previews/<pid>/grant` (fallback iframe→aba), UI em `127.0.0.1` com `X-UDH-Token` obrigatório em `/api/*` (zero cookies, zero `innerHTML`), cartões `ADR-025` (4 campos, zero bypass) e checagem offline em **Mozilla Firefox 153.7.0esr** (`PASS`).
- **Verificação em `20150ac`:** **157/157** complementares, **50/50** AT selados (`v1–v6`; 714 chamadas, 0 infra), **`POC-01`–`POC-04` 4/4** e R2/F-02 **PASS** ([resumo](openspec/changes/artifact-preview-and-guided-ui/verification-summary.json)). Arquivos protegidos e manifestos OCI intocados.

## Pendente

- **Change 5 (gates offline + R2/F-02 `PASS` em `089530d`):** T1–T4 verificados (`225/225` complementares, `doctor` `READY`, `4/4` POCs, `50/50` AT `v1–v6`, R2/F-02 `PASS`; [resumo](openspec/changes/discover-plan-tickets/verification-summary.json)). Merge em `main` aguarda ensaio ao vivo `DISC-01` (OpenCode Zen deu `HTTP 429` em `deepseek-v4.1-flash-free`; Ollama local pendente).
- **Restante do produto:** perfil `provision` (`B2.4`), execução protegida persistente (`B3.8`) e exportação (`B3.9`), `DOC-01/02`, `SKILL-01/02`, `UX-04`, terceiro agente (Codex), WSL2 (`B1.1b`), Chromium/Chrome, Memorix, Judge consultivo e instalador. Ainda não é MVP liberável completo.

## Próxima tarefa

Na branch `feat/change5-discover-plan-tickets`, destravar e executar o ensaio ao vivo de `DISC-01` (outro modelo `-free`/provedor de API no Gateway ou Ollama local) para fechar e integrar o Change 5 em `main`. T3-org adiada.

## Verificação

- Em `089530d`/`27b58a8`: `225/225` complementares (`UDH_TEST_DISCOVERY_PI=1` + OpenSpec 2.1.1), `doctor --conformance` (`READY`), `4/4` POCs, `50/50` AT `v1–v6` (714 chamadas, 0 infra) e R2/F-02 `PASS`. `DISC-01` ao vivo: bloqueado por `HTTP 429`.
- `PYTHONDONTWRITEBYTECODE=1 python3 -m udh doctor --conformance` (`READY`: `['pi', 'opencode']`), `PYTHONDONTWRITEBYTECODE=1 python3 -m udh.poc` (4/4 PASS) e `python3 -m udh.web --db <path> --store <path>`.
- [README](README.md) para entrada. Não abrir credenciais nem limpar `spike/slice0/workspace/` sem autorização.
