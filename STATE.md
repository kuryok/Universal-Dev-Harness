# Estado do projeto

Atualizado: 24/11/2026. Fase: **Change 3 (`opencode-handoff-poc03`) concluído, verificado (`5164e26`) e integrado em `main`** — imagem OCI dedicada do OpenCode 1.18.35 por digest, Gateway com `allowed_tool_names` fail-closed, adaptador OpenCode (E3 `zero_tools` + E4 `/udh/agent-config:ro`), handoff F-03 com `git_dir` externo, seleção ADR-026, `doctor --conformance` multi-agente `READY` (`['pi', 'opencode']`) e **4/4 POCs fundacionais (`POC-01`–`POC-04`) PASS** em Linux x86_64 rootless (modo offline `stub`).

Retomada: [AGENTS.md](AGENTS.md), este estado e [MEMORY.md](MEMORY.md). Fonte normativa: [baseline rastreado](Universal-Dev-Harness-v2_5-BASELINE-NORMATIVO-CONSOLIDADO-FINAL.md) + ratificação em [openspec/project.md](openspec/project.md); [v2.5.1](UDH-v2.5-ARQUITETURA-CONSOLIDADA-v2.5.1.md) é nota derivada.

## Funciona

- **Core SQLite v1–v4 (stdlib-only):** ledger unificado `transitions`, `evidence_refs`, checkpoints F-03, leitura `O(1)` em v4, Política v2 (`conformant_adapters` em `EXECUTE_AGENT`) e `python3 -m udh.lifecycle`.
- **Runtime Manager + 2 imagens OCI separadas por digest (`udh/runtime.py`, `runtime/`):** `localhost/udh-runtime@sha256:bed9a71a...` (Pi 1.2.0 + Python 3 + `at_runner.py`, intocada) e `localhost/udh-runtime-opencode@sha256:7af7a5d4...` (OpenCode 1.18.35 + Python 3 + Node v22 + `bridge.mjs`).
- **Gateway stdlib + 2 adaptadores conformes (`udh/gateway.py`, `udh/adapters/`):** UDS `0700`/`0600`, `SO_PEERCRED`, anti-slowloris (`408`), `allowed_tool_names` fail-closed (`403`/`400`), `stub_turns` e `openrouter_free`; Pi 1.2.0 e OpenCode 1.18.35 ambos `zero_tools` (`allow_tools=False`), E4 verificado (config RO + `/workspace/opencode.json` hostil ignorado) e `doctor --conformance` **`READY`**.
- **Handoff F-03 + ADR-026 + Executor F-04 (`udh/handoff.py`, `udh/trust_store.py`, `udh/executor.py`, `udh/poc.py`):** `git_dir` externo sanitizado, varredura física `O_NOFOLLOW` 1:1 com `git ls-files`, bloqueio `UNKNOWN_PENDENCY_ACTIVE`, verificação física de evidências na `TrustStore`, `HandoffPacket` sem histórico conversacional e `at_runner.py` F-04.
- **Verificação completa em `5164e26`:** **140/140** complementares PASS (73,2 s); **50/50** AT selados congelados PASS (`v6` 16/16, `v5` 13/13, `v2` 11/11, `v1` 10/10; 714 chamadas, 0 infra); **`POC-01`–`POC-04` 4/4 PASS** (24,6 s); revisão independente R2/F-02 **PASS** sem achados abertos ([resumo](openspec/changes/opencode-handoff-poc03/verification-summary.json)). `udh/operator.py`, `udh/policy.py`, `bootstrap/slice1/` e `runtime/image-manifest.json` intocados.

## Pendente

- **Change 4 (próximo marco):** perfil `artifact` com preview web local isolado (`B3.6`) + interface guiada mínima (`B1.7`), conforme priorização aprovada no próximo OpenSpec.
- **Restante do produto:** `DISCOVER`/`SPEC`/`PLAN` + multi-ticket DAG (`TICKET-01/02`), terceiro agente (Codex), perfil `provision` (`B2.4`), `DOC-01/02`, `SKILL-01/02`, `DISC-01`, `UX-04`, WSL2 (`B1.1b`), Memorix, Judge consultivo e instalador. Ainda não é MVP liberável para iniciantes.

## Próxima tarefa

Abrir branch dedicada e especificar o OpenSpec do **Change 4** (preview web local isolado `B3.6` + interface guiada mínima `B1.7`, ou outra prioridade definida pelo usuário) antes de implementar.

## Verificação

- `PYTHONDONTWRITEBYTECODE=1 TMPDIR="$PWD/bootstrap/slice1" python3 -m unittest discover -s tests` (140 PASS).
- `python3 -m udh doctor --conformance` (`READY`: `['pi', 'opencode']`) e `python3 -m udh.poc` (4/4 PASS).
- [README](README.md) para entrada. Não abrir credenciais nem limpar `spike/slice0/workspace/` sem autorização.
