# Tarefas — Change 2 (`vertical-runtime-pi-gateway-poc`)

**Pré-requisito obrigatório:** confirmação do plano revisado (F-02) e aprovação explícita antes de iniciar a tarefa T1.  
**Branch dedicada:** `feat/change2-vertical-runtime-pi-gateway-poc`.

---

## Sequência de implementação (pós-aprovação)

- [x] **T1 — Artefatos de runtime e Imagem OCI local dedicada por digest** (`runtime/Containerfile`, `runtime/build_image.py`, `runtime/bridge.mjs`, `runtime/at_runner.py`, `runtime/image-manifest.json`)
  - Criar `runtime/bridge.mjs` endurecida (loopback `127.0.0.1` → `/ipc/gateway.sock`, validação defensiva de método/path/framing/finalidade/timeouts).
  - Criar `runtime/at_runner.py` F-04 (`PR_SET_DUMPABLE=0`, leitura do `nonce` exclusivamente via `stdin` fechado antes de tocar `/artifact`, inventário AST de `/sealed-at`, execução dos testes em processo worker filho `python3 -I -B` com `stdin=DEVNULL` e sem `nonce`, logs em `stderr` e envelope oficial único em `stdout`).
  - Criar `runtime/Containerfile` e `runtime/build_image.py` (stdlib-only, staging temporário via `--build-context` com Node v22 + Pi 1.2.0 + Python 3.13 + stdlib + fechamento transitivo `ldd` inclusive `lib-dynload/`, smoke check dentro da imagem construída e gravação do digest real em `runtime/image-manifest.json`). Se houver impedimento offline, parar e consultar o usuário antes de qualquer rede.

- [x] **T2 — Runtime Manager rootless (`developer` e `artifact`)** (`udh/runtime.py`)
  - Implementar carregamento/validação fail-closed de `runtime/image-manifest.json` e verificação local por `@sha256:...` (`--pull=never`).
  - Implementar perfis `developer` e `artifact` com `--network=none`, `--read-only`, `--read-only-tmpfs=false`, `--cap-drop=ALL`, `--security-opt=no-new-privileges`, `--userns=keep-id`, `--user uid:gid`, `--mount` (rejeitando `:`, `,`, `\n`, `\x00` e mounts proibidos/sobrepostos incluindo `$HOME`), limites cgroup v2, `stdin_bytes` fechado imediatamente, drenagem não-bloqueante com teto de bytes/timeout, callback `on_started`, `podman kill` + `podman inspect` e cleanup verificado.

- [x] **T3 — Gateway de Inferência stdlib endurecido** (`udh/gateway.py`)
  - Implementar servidor UDS em diretório curto `0700` sob `/tmp` (`<= 100` bytes, sem `os.chdir()`), socket `0600`, `SO_PEERCRED` (`uid == os.getuid()`), token efêmero (`hmac.compare_digest`), leitor HTTP/1.1 direto sobre `socket.recv()` com `settimeout(remaining)` por chamada e deadlines absolutos (`header_deadline`, `body_deadline`, teto `16 KiB` de cabeçalhos), HTTP `503` em saturação de concorrência e `429` em orçamento.
  - Implementar auditoria JSONL append-only fora do workspace e redação de segredos.
  - Implementar backend `stub` (SSE determinístico offline + modo de corte) e backend `openrouter_free` (reconstrução allowlist do payload, TLS verificado sem redirects 3xx, validação de todos os campos de `pricing == 0` com `Decimal` finito, `max_price=0`, checagem de `usage.cost == 0` e janela deslizante contra vazamento de segredo).

- [x] **T4 — Adaptador de agente Pi 1.2.0** (`udh/adapters/__init__.py`, `udh/adapters/pi.py`)
  - Definir contrato declarativo `AdapterCapabilities` e `AgentExecutionResult`.
  - Implementar `PiAdapter` (`conformance` e `developer_python_stdlib`) com `/udh/agent-config` (proteção E4 + conferência SHA-256 pré/pós) e `/run/udh-scratch` fora de `/workspace`, além de parser JSONL estrito (`finals[-1]['stopReason'] == 'stop'`, sem qualquer evento `error`/`aborted`, `returncode == 0` e `outcome is None`).

- [x] **T5 — Store protegida + Executor Confiável F-04** (`udh/trust_store.py`, `udh/executor.py`)
  - Implementar `TrustStore` (`0700` fora do workspace, padrão `$XDG_DATA_HOME/udh/trust_store`, diretórios `0o755`/arquivos `0o444`): selo de AT (`at_version` + manifesto SHA-256), promoção imutável por `artifact_digest` com varredura recursiva `os.walk(..., followlinks=False)` + `O_NOFOLLOW` rejeitando symlinks/especiais e sombreamentos (`sitecustomize*`, `usercustomize*`, `unittest*`, `udh*`, `*.pth`), evidências oficiais e cache de conformance por `(policy_sha256, image_digest)`.
  - Implementar `TrustedExecutor` F-04 orquestrando `/opt/udh/at_runner.py` com `nonce` exclusivo via `stdin`, validação cruzada do envelope/inventário AST/exit code, rejeição de forja/tampering e registro em `CoreService.record_evidence(..., collector_route='TRUSTED_EXECUTOR')`.

- [x] **T6 — Evolução controlada de `doctor`, Política v2 e CLI** (`udh/doctor.py`, `udh/policy.py`, `udh/service.py`, `udh/__main__.py`)
  - Atualizar `udh/doctor.py` para checar imagem por digest, cache `(policy_sha256, image_digest)` em `store_dir` e modo `--conformance` (fail-closed `NOT_READY` exit `3` se não atingir `READY`).
  - Atualizar `udh/policy.py` (v2) exigindo `conformant_adapters` explícito contendo o adaptador requisitado para `EXECUTE_AGENT = ALLOW`, mantendo Política v1 bit-a-bit idêntica em v1–v3.
  - Expor `--conformance` e `--store` em `python3 -m udh doctor`, mantendo `policy` e recusa de `migrate`/`block`/`unblock` intactos. Confirmar ausência de arquivos não-`.py` sob `udh/`.

- [x] **T7 — Orquestrador das POCs e testes complementares** (`udh/poc.py`, `tests/test_runtime_and_gateway.py`, `tests/test_trust_store_and_executor.py`, `tests/test_pocs_change2.py`, `tests/test_policy_v2_and_doctor.py`)
  - Implementar `udh/poc.py` cobrindo integralmente `POC-01`, `POC-02` e `POC-04` sobre os componentes reais do Core.
  - Escrever testes unitários e de integração Podman rootless cobrindo `C2-01` a `C2-08` (incluindo slowloris 1 byte/s e ataques F-04 de `os._exit(0)` / `/proc`).

- [x] **T8 — Verificação completa (R1), revisão independente (R2/F-02), sincronização documental e merge em `main`**
  - Executar 100% da suíte complementar (`python3 -m unittest discover -s tests`) + `python3 -m udh doctor --conformance` + `python3 -m udh.poc`.
  - Executar regressão dos **50 AT selados congelados (`v1`, `v2`, `v5`, `v6`)** em Podman rootless confirmando 50/50 PASS e `bootstrap/slice1/`/`udh/operator.py` intocados.
  - Executar revisão independente R2/F-02 em contexto separado (`research`) sobre o diff candidato e resolver qualquer achado.
  - Gravar resumo compacto verificável (`<= 32 KiB`) em `openspec/changes/vertical-runtime-pi-gateway-poc/verification-summary.json`, sincronizar `STATE.md`, `MEMORY.md`, `README.md`, `AGENTS.md`, `openspec/project.md` e `UDH-v2.5-ARQUITETURA-CONSOLIDADA-v2.5.1.md`, e integrar em `main` somente com tudo verde.
