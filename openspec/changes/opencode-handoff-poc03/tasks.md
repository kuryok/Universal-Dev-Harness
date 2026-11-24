# Tarefas — Change 3 (`opencode-handoff-poc03`)

**Pré-requisito obrigatório:** revisão independente (subagente `research`) com veredito `PASS` sobre o contrato OpenSpec (`proposal.md` + `tasks.md`) antes de iniciar a tarefa T1.  
**Branch dedicada:** `feat/change3-opencode-handoff-poc03`.

---

## Sequência de implementação

- [ ] **T1 — Imagem OCI dedicada e separada para OpenCode 1.18.35 por digest** (`runtime/Containerfile.opencode`, `runtime/build_image.py`, `runtime/opencode-image-manifest.json`, `udh/runtime.py`)
  - Criar `runtime/Containerfile.opencode` preservando `runtime/Containerfile`, `runtime/image-manifest.json` e `runtime/at_runner.py` (Pi/`artifact`) intocados.
  - Estender `runtime/build_image.py` com `--target pi|opencode|all` (100% offline, `--pull=never --network=none`), empacotando OpenCode 1.18.35 + Node v22 + Python 3 stdlib + fechamento transitivo `ldd` (`glibc` + `lib-dynload`) + `/opt/udh/bridge.mjs` + mountpoints vazios fora do workspace, rodando smoke checks com `--memory=768m --memory-swap=768m` (`opencode --version`, `python3 -I`, `node --check /opt/udh/bridge.mjs`) e gravando `runtime/opencode-image-manifest.json` com digest real `sha256:...` e `bridge_sha256`.
  - Adicionar `DEFAULT_OPENCODE_MANIFEST_PATH`, `OpenCodeImageManifest` e `load_opencode_manifest(..., verify_local_sources=True)` (incluindo verificação de `bridge_sha256` → `IMAGE_DIGEST_STALE`) em `udh/runtime.py` mantendo `ImageManifest` / `load_manifest()` 100% retrocompatíveis.

- [ ] **T2 — Gateway com `allowed_tool_names` fail-closed e turnos `stub` determinísticos** (`udh/gateway.py`)
  - Adicionar `allowed_tool_names: tuple[str, ...] = ()` em `GatewayConfig` com validação fail-closed: rejeitar `functions` legado (`403 tools_denied`), rejeitar `tools` não-lista (`400 tools_invalid`), aceitar `tools` ausente/`[]`, rejeitar `tools` não-vazio quando `allow_tools=False` ou `allowed_tool_names` vazia ou contendo tool fora da allowlist (`403 tools_denied`) ou entrada malformada (`400 tools_invalid`).
  - Registrar `tool_names` na auditoria JSONL (sem argumentos/segredos) e atualizar `build_openrouter_payload(..., allowed_tool_names=())` para validar estritamente sem strip silencioso (levantando `GatewayError` em violação e reconstruindo apenas `type`/`function:{name,description,parameters}`).
  - Adicionar suporte opcional a `stub_turns` sob lock no backend `stub` com matching determinístico por `has_tools` (separando chamadas auxiliares `has_tools=False` de turnos `tool_calls` com `has_tools=True`).

- [ ] **T3 — Segundo adaptador de agente: OpenCode 1.18.35 (E3 honesto + E4 + parser JSONL) e ajuste restrito no Pi** (`udh/adapters/__init__.py`, `udh/adapters/opencode.py`, `udh/adapters/pi.py`)
  - Estender `AdapterCapabilities` com `supports_zero_tools_conformance: bool = True` (retrocompatível).
  - Atualizar `udh/adapters/pi.py` em escopo mínimo (`allowed_tools` opcional em `developer_python_stdlib` preservando default atual e `conformance` intocado, `conformance_tool_mode='zero_tools'`, e eventual ajuste mínimo para tool calling real no Pi 1.2.0).
  - Implementar `OpenCodeAdapter` na imagem dedicada (`768 MiB`, `--pure`, sem `--auto`, `--network=none`, `--read-only`, `--cap-drop=ALL`, `no-new-privileges`, `userns=keep-id`), com `/run/udh-scratch` fora de `/workspace`, configuração RO em `/udh/agent-config/opencode.json` (mountpoint RO + SHA-256 pré/pós) e bloqueio explícito contra `/workspace/opencode.json` hostil (`OPENCODE_DISABLE_PROJECT_CONFIG=true` / precedência validada em `debug config`).
  - Verificar experimentalmente o achado E3 no binário OpenCode 1.18.35: tentar supressão total com `allow_tools=False`; se built-ins residuais somente-leitura persistirem, declarar `supports_zero_tools_conformance=False` e operar conformance sob `conformance_readonly_tools` com teto fechado somente-leitura no Gateway (sem strip silencioso; falha fechada B1.5 se vazar escrita/shell/rede/subagente), além de `developer_python_stdlib` com allowlist explícita de workspace.
  - Implementar `parse_opencode_jsonl` estrito (nunca confiar apenas em `exit 0`; detectar erro de transporte/ponte/corte de stream).

- [ ] **T4 — Seletor determinístico (ADR-026) e Módulo de Handoff F-03 com `git_dir` externo** (`udh/handoff.py`)
  - Implementar `select_adapter(...)` (ADR-026): seleção/fallback determinístico apenas entre `conformant_adapters`, com justificativa estruturada e falha fechada.
  - Implementar operações Git do Core com `git_dir` externo ao `/workspace` e ambiente sanitizado (`GIT_CONFIG_NOSYSTEM=1`, `GIT_CONFIG_GLOBAL=/dev/null`, `GIT_TERMINAL_PROMPT=0`, `-c core.hooksPath=/dev/null`, `-c core.fsmonitor=false`, `-c protocol.file.allow=never`, `--no-verify`), varredura física `O_NOFOLLOW` 1:1 com `git ls-files` (rejeitando `.git`, `.gitmodules`, `.gitattributes`, arquivos ocultos por `.gitignore`, symlinks, especiais, sombreamentos e modos Git `120000`/`160000`) e cálculo canônico de `worktree_snapshot_sha256`.
  - Implementar gravação de `Checkpoint` F-03 via `CoreService.record_checkpoint(...)` e montagem/validação fail-closed (`verify_handoff_packet`) do `HandoffPacket` JSON canônico efêmero (`packet_sha256`, checagem de `run.schema_version == 4`, `checkpoint.run_revision == run.revision`, campos Git não-`None`, `UNKNOWN_PENDENCY_ACTIVE`, `HEAD != checkpoint.head_commit_sha`, verificação física `O_NOFOLLOW` na `TrustStore` com `collector_route='TRUSTED_EXECUTOR'`, autorização `ALLOW` do `to_adapter`, e proibição programática de campos conversacionais).

- [ ] **T5 — Evolução multi-agente de `TrustStore`, `doctor`, `CoreService` e CLI** (`udh/trust_store.py`, `udh/doctor.py`, `udh/service.py`, `udh/__main__.py`)
  - Atualizar `TrustStore` com cache de conformance por adaptador (`conformance-<adapter>.json`, regex segura `^[a-z0-9][a-z0-9_-]{0,31}$` em `adapter_name`, conferência de `parsed['adapter_name']`, retrocompatível com `conformance.json` e default `adapter_name='pi'`).
  - Atualizar `udh/doctor.py`, `udh/service.py` e `udh/__main__.py` com suporte multi-agente (`pi` e `opencode`, `--adapter pi|opencode|all`, `opencode_manifest_path`), leitura RO `_read_conformance_readonly` por adaptador, tabela explícita de agregação B1.3 (`READY`/`LIMITED`/`NOT_READY`), ordem canônica `['pi', 'opencode']`, `details.adapters` (com `conformance_tool_mode`) e fachada `select_conformant_adapter(...)`, mantendo `udh/policy.py`, `policy` CLI e recusa de `migrate`/`block`/`unblock` intactos.

- [ ] **T6 — Orquestrador `POC-03` e testes complementares** (`udh/poc.py`, `tests/test_opencode_and_handoff.py`, `tests/test_poc03.py`, `tests/test_policy_v2_and_doctor.py`)
  - Implementar `run_poc_03(...)` e opções `--poc 01|02|03|04|all` e `--opencode-manifest` (mantendo `--manifest` retrocompatível) em `udh/poc.py`.
  - Escrever testes unitários e de integração Podman rootless cobrindo `C3-01` a `C3-06` (incluindo controle negativo `/workspace/opencode.json` hostil, `git_dir` externo + `.git`/`.gitignore` rejeitados, crash entre commit Git e gravação SQLite, e adulteração física na `TrustStore`).

- [ ] **T7 — Verificação completa (R1), revisão independente do diff (R2/F-02) e sincronização documental**
  - Executar `PYTHONDONTWRITEBYTECODE=1` em 100% da suíte complementar (`python3 -m unittest discover -s tests`), `python3 -m udh doctor --conformance` (multi-agente `READY`), `python3 -m udh.poc` (`POC-01`–`POC-04` 4/4 PASS) e `find udh -type f ! -name '*.py'` (saída vazia).
  - Executar regressão dos **50 AT selados congelados (`v1`, `v2`, `v5`, `v6`)** em Podman rootless confirmando 50/50 PASS e `bootstrap/slice1/`, `udh/operator.py`, `udh/policy.py`, `runtime/Containerfile`, `runtime/image-manifest.json` e `runtime/at_runner.py` intocados.
  - Executar revisão independente R2/F-02 em contexto separado (`research`) sobre o diff candidato e resolver qualquer achado.
  - Gravar resumo compacto verificável (`<= 32 KiB`) em `openspec/changes/opencode-handoff-poc03/verification-summary.json` e sincronizar `STATE.md`, `MEMORY.md`, `README.md`, `AGENTS.md`, `openspec/project.md` e `UDH-v2.5-ARQUITETURA-CONSOLIDADA-v2.5.1.md`.
