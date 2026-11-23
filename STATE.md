# Estado do projeto

Atualizado: 23/11/2026. Fase: **Change 2 (`vertical-runtime-pi-gateway-poc`) concluído, verificado (`beac057`) e integrado em `main`** — Runtime Manager rootless, imagem OCI dedicada por digest, Gateway stdlib endurecido, adaptador Pi 1.2.0, TrustStore + Executor Confiável F-04 e **`POC-01`, `POC-02` e `POC-04` PASS** em Linux x86_64 rootless (modo offline `stub`).

Retomada: [AGENTS.md](AGENTS.md), este estado e [MEMORY.md](MEMORY.md). Fonte normativa: [baseline rastreado](Universal-Dev-Harness-v2_5-BASELINE-NORMATIVO-CONSOLIDADO-FINAL.md) + ratificação em [openspec/project.md](openspec/project.md); [v2.5.1](UDH-v2.5-ARQUITETURA-CONSOLIDADA-v2.5.1.md) é nota derivada.

## Funciona

- **Core SQLite v1–v4 (stdlib-only):** ledger unificado `transitions`, `evidence_refs`, checkpoints F-03, leitura `O(1)` em v4, Política v2 com `conformant_adapters` explícito em `EXECUTE_AGENT` (Política v1 congelada em v1–v3) e `python3 -m udh.lifecycle`.
- **Runtime Manager + Imagem OCI dedicada (`udh/runtime.py`, `runtime/`):** imagem offline `localhost/udh-runtime@sha256:bed9a71a...` (Python 3 stdlib + Node v22 + Pi 1.2.0 + `bridge.mjs` + `at_runner.py`), perfis `developer` e `artifact` (`--pull=never`, `--network=none`, `--read-only`, `--cap-drop=ALL`, `no-new-privileges`, `userns=keep-id`, `--user uid:gid`, `--mount` normalizado, cgroup v2 e cleanup verificado).
- **Gateway stdlib + Adaptador Pi 1.2.0 (`udh/gateway.py`, `udh/adapters/pi.py`):** UDS curto `0700`/`0600`, `SO_PEERCRED`, leitor HTTP/1.1 direto contra slowloris (`408`), `503`/`429`, backends `stub` e `openrouter_free` (allowlist + `pricing == 0` + `usage.cost == 0` + janela deslizante); Pi 1.2.0 com `/udh/agent-config` (E4) e `/run/udh-scratch` fora de `/workspace`, parser JSONL estrito e `python3 -m udh doctor --conformance` **`READY`**.
- **TrustStore + Executor F-04 + POCs (`udh/trust_store.py`, `udh/executor.py`, `udh/poc.py`):** selo AT por `at_version`, promoção imutável `O_NOFOLLOW`, `at_runner.py` com `PR_SET_DUMPABLE=0` fail-closed no pai e no worker + `nonce` exclusivo via `stdin` + inventário AST duplo.
- **Verificação completa em `beac057`:** **125/125** complementares PASS (27,5 s); **50/50** AT selados congelados PASS (`v6` 16/16, `v5` 13/13, `v2` 11/11, `v1` 10/10; 714 chamadas, 0 infra); **`POC-01` / `POC-02` / `POC-04` PASS** (8,4 s); revisão independente R2/F-02 **PASS** sem achados abertos ([resumo](openspec/changes/vertical-runtime-pi-gateway-poc/verification-summary.json)). `udh/operator.py` e `bootstrap/slice1/` intocados.

## Pendente

- **Change 3 (próximo marco):** segundo agente (OpenCode) + **`POC-03`** (handoff reprodutível e recuperação F-03 entre Pi 1.2.0 e OpenCode) — ou perfil `artifact` com preview web isolado (`B3.6`) e interface guiada (`B1.7`), conforme priorização aprovada no próximo OpenSpec.
- **Restante do produto:** `DISCOVER`/`SPEC`/`PLAN` + multi-ticket DAG (`TICKET-01/02`), perfil `provision` (`B2.4`), `DOC-01/02`, `SKILL-01/02`, `DISC-01`, `UX-04`, WSL2 (`B1.1b`), Memorix, Judge consultivo e instalador. Não é MVP liberável ainda (B1.6 exige >= 2 agentes + `POC-03`).

## Próxima tarefa

Abrir branch dedicada e especificar o OpenSpec do **Change 3** (segundo agente + `POC-03` ou fluxo guiado/preview local `B3.6`/`B1.7`, conforme decisão do usuário) antes de implementar.

## Verificação

- `PYTHONDONTWRITEBYTECODE=1 TMPDIR="$PWD/bootstrap/slice1" python3 -m unittest discover -s tests` (125 PASS).
- `python3 -m udh doctor --conformance` (`READY` com imagem OCI local) e `python3 -m udh.poc` (`POC-01`/`02`/`04` PASS).
- [README](README.md) para entrada. Não abrir credenciais nem limpar `spike/slice0/workspace/` sem autorização.
