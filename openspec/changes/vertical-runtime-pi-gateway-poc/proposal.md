# Change 2 — Marco Vertical de Produto: Runtime Manager Rootless, Imagem OCI por Digest, Gateway Stdlib, Adaptador Pi 1.2.0, Store Protegida + Executor F-04 e POC-01/02/04

**Estado:** APROVADO, IMPLEMENTADO E VERIFICADO EM 23/11/2026 (`beac057`; resumo em [verification-summary.json](verification-summary.json)).
**Data:** 23/11/2026.  
**Branch dedicada:** `feat/change2-vertical-runtime-pi-gateway-poc` (a partir de `main` @ `df96201`, com Core v4 verificado em `9f84150`: 110/110 complementares PASS, 50/50 AT selados `v1–v6` PASS, revisão R2/F-02 PASS).  
**Referências normativas:** [baseline normativo rastreado](../../../Universal-Dev-Harness-v2_5-BASELINE-NORMATIVO-CONSOLIDADO-FINAL.md) (`ADR-001`–`ADR-005`, `ADR-007`, `ADR-008`, `ADR-010`, `ADR-013`–`ADR-018`, `ADR-018 A`, `ADR-020`, `ADR-021`, `ADR-024`, `ADR-025`, `ADR-028`, `B1.1`–`B1.6`, `B2.1`–`B2.3`, `B3.1`–`B3.3`, `B4.1`, `B4.5`–`B4.9`, `F-02`, `F-04`) e ratificação registrada em [openspec/project.md](../../project.md).

---

## 1. Objetivo e limites

Construir e comprovar experimentalmente a **fatia vertical executável de produto** sobre o Core SQLite v4 — mantendo 100% Python 3 stdlib no Core, um único Core autoritativo e `bootstrap/slice1/` congelado — entregando seis capacidades integradas:

1. **Runtime Manager rootless + Imagem OCI dedicada por digest (`udh/runtime.py`, `runtime/Containerfile`, `runtime/build_image.py`):**
   - Gerenciar execução isolada via Podman rootless sob contrato declarativo de capacidades com **fail-closed** (ADR-001/002/017): `--pull=never`, imagem exclusivamente por `@sha256:<digest>`, `--network=none`, `--read-only`, `--read-only-tmpfs=false`, `--cap-drop=ALL`, `--security-opt=no-new-privileges`, `--userns=keep-id`, `--user <uid>:<gid>`, limites explícitos de memória/swap/PIDs/CPU, mounts validados via `--mount` (sem `:`, `,`, `\n`, `\x00`), drenagem não-bloqueante de stdout/stderr com teto de bytes, timeout absoluto, `podman kill` + `podman inspect` e limpeza verificada de contêineres.
   - Dois perfis distintos no mesmo Runtime Manager:
     - **`developer`**: executa o agente (Pi 1.2.0) com `/workspace:rw`, configuração protegida em mountpoint fora do workspace (`/udh/agent-config` + arquivos `:ro`, resistente a rename-shadowing e pre-shadowing), scratch separado (`/run/udh-scratch:rw` para `HOME`/`TMPDIR`/sinalização da ponte, sem contaminar `/workspace`) e socket UDS do gateway em `/ipc:ro`.
     - **`artifact`** (B3.1/F-04): executa artefato promovido (`/artifact:ro`) e testes oficiais (`/sealed-at:ro`) com scratch descartável (`/work:rw`), **sem agente**, **sem IPC de gateway**, **sem credenciais** e **sem egress**.
   - Empacotar uma **imagem OCI local dedicada** (`runtime/Containerfile` + `runtime/build_image.py`, stdlib-only, sem commitar binários no Git) contendo Python 3 stdlib + Node v22 + Pi 1.2.0 + fechamento transitivo de libs (`ldd`, inclusive `ld-linux` e extensões C de `lib-dynload/`) + `/opt/udh/bridge.mjs` + `/opt/udh/at_runner.py`, fixada por digest `sha256` em `runtime/image-manifest.json`. Caso falte camada ou haja impedimento offline em T1, parar e pedir autorização explícita antes de qualquer operação de rede (Regra #6).
2. **Gateway de Inferência stdlib endurecido (`udh/gateway.py`):**
   - Função do mesmo Core (B2.1, sem microserviço permanente) operando sobre Unix Domain Socket em diretório curto `0700` sob `/tmp` (ou `$XDG_RUNTIME_DIR` quando `<= 100` bytes; nunca `os.chdir()` nem `TMPDIR` longo) com socket `0600`, `SO_PEERCRED` (`peer_uid == os.getuid()`), token efêmero de capacidade em tempo constante (`hmac.compare_digest`) e finalidade restrita (`X-UDH-Purpose: inference`).
   - Leitor HTTP/1.1 direto sobre `socket.recv()` com `sock.settimeout(remaining)` recalculado antes de **cada** `recv()`, teto de cabeçalhos (`16 KiB`) e deadlines absolutos (`header_deadline`, `body_deadline`) contra envio lento (1 byte/s — fechando C2 do spike), rejeição de `Transfer-Encoding`, múltiplos `Content-Length`, payload excedente (`413`), JSON inválido/não-finito (`400`), rotas/métodos/modelos fora da allowlist (`403`), concorrência excedida (`503`) e orçamento excedido (`429`).
   - Dois provedores explícitos e segregados:
     - **`stub` (padrão, 100% offline):** dublê determinístico compatível com SSE OpenAI-completions para testes, conformance e `POC-01` sem credenciais nem rede.
     - **`openrouter_free` (opt-in explícito):** reconstrução por allowlist estrita do payload upstream (descartando chaves arbitrárias como `provider`/`route`/`models` vindas do contêiner), HTTPS com TLS verificado para `openrouter.ai` sem seguir redirects 3xx, validação de preço zero em todos os campos numéricos de `pricing` (`Decimal` finito `== 0`), `provider.max_price = 0`, conferência de `usage.cost == 0` quando retornado, e varredura de vazamento de segredo com janela deslizante entre chunks SSE.
3. **Primeiro adaptador de agente — Pi 1.2.0 (`udh/adapters/pi.py`):**
   - Contrato declarativo de capacidades (`conformance` com `--no-tools`; `developer_python_stdlib` com ferramentas mínimas no sandbox `--network=none`).
   - Invocação headless determinística (`--offline --no-mcp --no-extensions --no-skills --no-prompt-templates --no-context-files --no-themes --no-approve --no-session --mode json --print`), proteção E4 de configuração fora de `/workspace` (`/udh/agent-config`) com conferência SHA-256 pré/pós, e **parser estruturado estrito do stream JSONL** (exige `finals[-1]['stopReason'] == 'stop'`, ausência de qualquer evento `error`/`aborted`, ausência de erro de transporte/ponte, `returncode == 0` e `outcome is None`).
4. **Store oficial protegida + Executor Confiável F-04 (`udh/trust_store.py`, `udh/executor.py`):**
   - Store protegida (`0700`, padrão fora do workspace em `$XDG_DATA_HOME/udh/trust_store` / `~/.local/share/udh/trust_store` ou `--store PATH`) para: (a) selos de AT (`at_version = sha256:...` + manifesto SHA-256 por arquivo, B4.5); (b) promoção imutável de artefato Python 3 stdlib por `artifact_digest` (B3.2, varredura recursiva `os.walk(..., followlinks=False)` + `O_NOFOLLOW` rejeitando qualquer symlink/arquivo especial e arquivos/pacotes de sombreamento `sitecustomize*`, `usercustomize*`, `unittest*`, `udh*`, `*.pth`); (c) armazenamento oficial de relatórios de evidência com `evidence_sha256` vinculado a `run_id`, `artifact_digest`, `at_version`, `commit_sha` e `policy_sha256`; (d) cache de conformance por `(policy_sha256, image_digest)`.
   - Executor F-04 (`udh/executor.py` + `runtime/at_runner.py`): **nunca importa** código do projeto/testes no Core e separa processos dentro do contêiner `artifact`:
     - O `nonce` efêmero é enviado **exclusivamente via `stdin`** ao processo pai `at_runner.py` (nunca em `argv`, `env` ou arquivo) após `PR_SET_DUMPABLE=0` no pai, e `stdin` é fechado antes de tocar `/artifact`.
     - O pai `at_runner.py` faz inventário estático por AST dos casos `test_*` em `/sealed-at`, executa os testes em **processo worker filho** (`python3 -I -B`, `stdin=DEVNULL`, sem acesso ao `nonce` do pai) com `/artifact` ao final do `sys.path` (após stdlib), verifica conclusão integral contra o inventário AST e emite o envelope oficial assinado pelo `nonce` no `stdout` do contêiner (logs do worker segregados em `stderr`, sem arquivo gravável em `/work`).
     - O `TrustedExecutor` valida o envelope + `nonce` + `returncode == 0` + `outcome is None`, ignora qualquer relatório falsificado no workspace e grava o veredito oficial via `CoreService.record_evidence(..., collector_route='TRUSTED_EXECUTOR')`.
5. **Evolução controlada de `doctor` (B1.3/B1.5) e Política v2 (`udh/doctor.py`, `udh/policy.py`, `udh/service.py`):**
   - `doctor` inspeciona a imagem OCI dedicada por digest + cache de conformance `(policy_sha256, image_digest)` em `store_dir`; `python3 -m udh doctor --conformance [--store PATH]` executa a prova real de conformance do Pi 1.2.0 atrás do gateway `stub` e **falha fechado (`NOT_READY`, exit `3`)** se não atingir `READY`.
   - Política v2 (`POLICY_VERSION = 2`) exige `conformant_adapters` explícito contendo o adaptador requisitado (nunca booleano solto sobre `READY_WITH_LIMITS`) para autorizar `EXECUTE_AGENT` (`AGENT_EXECUTION_AUTHORIZED`), mantendo `fail-closed` padrão (`GATEWAY_AND_ADAPTER_NOT_READY`) e preservando `POLICY_VERSION = 1` bit-a-bit idêntica em bancos v1–v3.
6. **Execução oficial e verificação das POCs de produto (`POC-01`, `POC-02`, `POC-04`):**
   - Suíte executável e reproduzível (`python3 -m udh.poc` + `tests/test_*.py`) cobrindo `POC-01`, `POC-02` e `POC-04` sobre os componentes reais do Core em Podman rootless, gerando resumo compacto verificável (`<= 32 KiB`) em `openspec/changes/vertical-runtime-pi-gateway-poc/verification-summary.json`.

### O que **NÃO** faz parte deste change

- **Não implementa** `POC-03`, segundo/terceiro agentes (OpenCode/Codex), handoff multi-agente nem `DISCOVER`/`SPEC`/`PLAN` ou tabela multi-ticket/DAG (`TICKET-01/02`).
- **Não implementa** perfil `provision` (B2.4), pontes MCP (Context7/GitHub/Playwright), preview web (`B3.6`), interface web local (`B1.7`), Memorix, Judge consultivo ou instalador/WSL2.
- **Não modifica** `udh/operator.py`, `bootstrap/slice1/**`, selos `v1–v6`, AT protegidos legados, `spike/slice0/**` nem o baseline normativo.
- **Não coloca arquivos não-`.py` dentro de `udh/`** (preservando o inventário exigido pelos integradores dos AT selados) e **não adiciona** dependências Python externas no Core (100% biblioteca padrão).
- **Não declara** MVP completo/liberável (conforme B1.6, isso exige ao menos dois agentes conformes e `POC-03` aprovada).

---

## 2. Contratos de autoridade e integração com o Core v4

- **Um único Core:** todos os novos módulos Python vivem sob `udh/` (somente arquivos `.py`), acionados por `CoreService` e pelas entradas CLI explícitas `python3 -m udh doctor [--conformance] [--store PATH]` e `python3 -m udh.poc`, mantendo `python3 -m udh` 100% compatível com os 50 AT selados (`v1–v6`) — sem adicionar `migrate`, `block` ou `unblock` nem flags de autoridade em `python3 -m udh policy`.
- **Sem mudança de esquema SQLite (`user_version` permanece `4`):** reutiliza integralmente `runs`, `transitions`, `evidence_refs` e `checkpoints` do Change 1, preservando leitura `O(1)` e regressão `v1–v6`.
- **Fonte única de verdade de evidências:** apenas o `TrustedExecutor` (`collector_route='TRUSTED_EXECUTOR'`) registra vereditos oficiais de `AT_RESULT` / `POC_RESULT` na store protegida e no SQLite v4; a fachada de conclusão valida ainda que o `evidence_id` corresponda à evidência oficial mais recente para o par `(artifact_digest, at_version)`.

---

## 3. Arquivos permitidos na implementação (após aprovação)

### Infraestrutura de imagem OCI local (fora de `udh/`)
- `runtime/Containerfile` *(novo)* — definição da imagem OCI dedicada local (Python 3 stdlib + Node v22 + Pi 1.2.0 + libs `glibc`/`lib-dynload` + `/opt/udh/bridge.mjs` + `/opt/udh/at_runner.py`), construída localmente e fixada por digest.
- `runtime/build_image.py` *(novo)* — script stdlib-only de build offline via staging temporário (`--build-context`), fechamento transitivo `ldd`, smoke test dentro da imagem construída e gravação do digest real em `runtime/image-manifest.json` (nenhum binário commitado no Git).
- `runtime/bridge.mjs` *(novo)* — ponte HTTP loopback (`127.0.0.1`) → Unix Domain Socket (`/ipc/gateway.sock`) endurecida (validação defensiva de método/rota/framing/timeouts), empacotada `:ro` dentro da imagem OCI.
- `runtime/at_runner.py` *(novo)* — entrypoint oficial F-04 executado com `python3 -I` dentro do perfil `artifact`: `PR_SET_DUMPABLE=0`, leitura do `nonce` exclusivamente via `stdin` (fechado antes de tocar `/artifact`), inventário AST de `/sealed-at`, execução de testes em processo worker filho isolado e emissão do envelope oficial em `stdout`.
- `runtime/image-manifest.json` *(novo)* — manifesto versionado com referência da imagem por digest `sha256`, hashes dos artefatos empacotados e perfil de stack `python3-stdlib-v1`.

### Código de produto do Core (100% Python stdlib, somente `.py` sob `udh/`)
- `udh/runtime.py` *(novo)* — `RuntimeManager` rootless (Podman), perfis `developer` e `artifact`, validação fail-closed de capabilities/digest/mounts (`--mount`, `--user uid:gid`), limites cgroup v2, captura com limite de bytes/timeout, callback `on_started`, `podman kill` + `podman inspect` e cleanup verificado.
- `udh/gateway.py` *(novo)* — Gateway de inferência stdlib (UDS curto `<= 100` bytes sob `/tmp` + `SO_PEERCRED` + token efêmero em tempo constante + leitor HTTP/1.1 sobre `socket.recv()` com deadline absoluto + `503`/`429` + auditoria/redação + backends `stub` e `openrouter_free`).
- `udh/adapters/__init__.py` *(novo)* — contrato declarativo base de adaptadores de agente (`AgentAdapter`, `AdapterCapabilities`, `AgentExecutionResult`).
- `udh/adapters/pi.py` *(novo)* — adaptador Pi 1.2.0 (perfis `conformance` e `developer_python_stdlib`, proteção E4 em `/udh/agent-config`, scratch separado em `/run/udh-scratch`, parser JSONL estrito).
- `udh/trust_store.py` *(novo)* — store protegida fora do workspace (selo de AT por `at_version`, promoção imutável por `artifact_digest` com varredura recursiva `O_NOFOLLOW`, armazenamento oficial de evidências e cache de conformance por `(policy_sha256, image_digest)`).
- `udh/executor.py` *(novo)* — Executor Confiável F-04 (orquestração no perfil `artifact`, envio de `nonce` apenas via `stdin`, validação cruzada do envelope/exit code/inventário AST, rejeição de tampering/relatório falsificado e registro em `CoreService`).
- `udh/poc.py` *(novo)* — orquestrador verificável de `POC-01`, `POC-02` e `POC-04` sobre os componentes reais do Core + entrada CLI `python3 -m udh.poc`.
- [udh/doctor.py](../../../udh/doctor.py) — inspeção da imagem OCI por digest, leitura de cache de conformance `(policy_sha256, image_digest)` em `store_dir` e modo explícito `run_conformance=True` (fail-closed `NOT_READY` em erro/ausência).
- [udh/policy.py](../../../udh/policy.py) — evolução de `POLICY_VERSION = 2` no ramo `EXECUTE_AGENT` com `conformant_adapters` explícito (`POLICY_VERSION = 1` em v1–v3 bit-a-bit intocada).
- [udh/service.py](../../../udh/service.py) — integração de `doctor(store_dir=..., run_conformance=...)`, `policy(..., conformant_adapters=..., adapter=...)` e fachada `authorize_agent_execution(...)`.
- [udh/__main__.py](../../../udh/__main__.py) — flags opcionais `--conformance` e `--store` em `doctor` (preservando `policy` e recusa de `migrate`/`block`/`unblock` com exit `2`).

### Testes complementares e fechamento
- `tests/test_runtime_and_gateway.py` *(novo)* — testes unitários e de integração Podman rootless do `RuntimeManager`, endurecimento do `Gateway` (incluindo slowloris 1 byte/s real, `SO_PEERCRED`, `503`/`429`, allowlist e `openrouter_free` com dublê TLS/preço zero/janela deslizante) e `PiAdapter`.
- `tests/test_trust_store_and_executor.py` *(novo)* — testes da `TrustStore` e do `TrustedExecutor` F-04 (worker isolado, tentativa de ataque via código em `/artifact` tentando forjar resultado ou ler `/proc/<ppid>/cmdline`/`environ`/`mem`, tampering recursivo de `sitecustomize`/`unittest`/`.pth`/manifesto, relatório falsificado ignorado, ciclo `IN_PROGRESS → DONE`).
- `tests/test_pocs_change2.py` *(novo)* — validação automatizada ponta a ponta de `POC-01`, `POC-02` e `POC-04` em Podman rootless (modo offline `stub`).
- [tests/test_policy_v2_and_doctor.py](../../../tests/test_policy_v2_and_doctor.py) — atualização segura com `--store` isolado + cobertura de `doctor --conformance` (sucesso `READY` e falha fechada `NOT_READY` exit `3`), invalidação `STALE` e `EXECUTE_AGENT = ALLOW` somente com `conformant_adapters` válido.
- `openspec/changes/vertical-runtime-pi-gateway-poc/verification-summary.json` *(novo, `<= 32 KiB`)* + sincronização obrigatória dos documentos ativos (`STATE.md`, `MEMORY.md`, `README.md`, `AGENTS.md`, `openspec/project.md`, `UDH-v2.5-ARQUITETURA-CONSOLIDADA-v2.5.1.md`) ao concluir.

### Arquivos explicitamente proibidos neste change
- `udh/operator.py` (intocado).
- `bootstrap/slice1/**` (congelado; nenhum selo `v7`, nenhum AT alterado).
- `spike/slice0/**` (descartável/intocado).
- `Universal-Dev-Harness-v2_5-BASELINE-NORMATIVO-CONSOLIDADO-FINAL.md` (baseline preservado).
- `relatorio.md`, `SPIKE-SLICE0-*.md` (registros históricos preservados).

---

## 4. Critérios de aceite e pronto do Change 2

| ID | Critério verificável |
| --- | --- |
| C2-01 | **Imagem OCI dedicada por digest:** `runtime/build_image.py` + `runtime/Containerfile` constroem localmente a imagem contendo Python 3 stdlib + Node v22 + Pi 1.2.0 + libs `glibc`/`lib-dynload` + `bridge.mjs` + `at_runner.py` com smoke check verde (`python3 -I` stdlib + `pi --version`); `runtime/image-manifest.json` registra o digest real `sha256:...`; `RuntimeManager` recusa qualquer imagem sem `@sha256:...`, com digest divergente ou que exija pull. |
| C2-02 | **Runtime Manager rootless (`developer` e `artifact`):** execução confirmada com `network=none`, `--read-only`, `--read-only-tmpfs=false`, `cap-drop=ALL`, `no-new-privileges`, `userns=keep-id`, `--user uid:gid`, `--mount` validado, cgroup v2 (`memory`, `pids`, `cpus`), captura com limite de bytes/timeout, `podman kill` + `podman inspect` e cleanup verificado (zero contêiner órfão). Perfil `artifact` sem agente, sem IPC e sem escrita no artefato/ATs. |
| C2-03 | **Gateway stdlib endurecido:** UDS curto (`<= 100` bytes, `0700`/`0600`) + `SO_PEERCRED` + token efêmero (`hmac.compare_digest`) + `X-UDH-Purpose: inference`; leitor sobre `socket.recv()` com encerramento determinístico sob slowloris (1 byte/s); rejeição verificada de rota (`403`), método (`403`), finalidade (`403`), token (`401`), modelo (`403`), framing (`400`), payload excessivo (`413`), JSON não-finito (`400`), concorrência (`503`) e orçamento (`429`); auditoria JSONL sem vazamento de segredos; backend `openrouter_free` validado contra allowlist de campos, preço não-zero, redirects 3xx e vazamento de segredo fatiado entre chunks (testes determinísticos sem credencial real). |
| C2-04 | **Adaptador Pi 1.2.0 + Conformance B1.3/B1.5:** Pi 1.2.0 executa dentro da imagem OCI dedicada em `network=none` consumindo o gateway `stub`, com `/udh/agent-config` e `/run/udh-scratch` fora de `/workspace`; proteção E4 verificada por SHA-256 pré/pós; corte abrupto do gateway detectado pelo parser JSONL estrito mesmo se o processo sair com código `0`; `python3 -m udh doctor --conformance` grava cache por `(policy_sha256, image_digest)` e eleva `overall_status` para `READY` (ou falha fechado `NOT_READY` exit `3` se falhar/imagem ausente); alteração de digest ou política invalida o cache (`STALE` → `READY_WITH_LIMITS`). |
| C2-05 | **Store protegida + Executor Confiável F-04:** selo de AT com `at_version` SHA-256; promoção imutável por `artifact_digest` com varredura recursiva `O_NOFOLLOW`; execução isolada em perfil `artifact` com `nonce` exclusivo via `stdin` + `PR_SET_DUMPABLE=0` + processo worker filho + validação contra inventário AST dos ATs; tentativas de forja de resultado/leitura de `/proc` ou tampering de `sitecustomize*`/`usercustomize*`/`unittest*`/`udh*`/`.pth`/manifesto são bloqueadas/rejeitadas (fail-closed). |
| C2-06 | **`POC-01` PASS (evidência reproduzível):** todas as verificações negativas de isolamento rootless, namespaces (`on_started`), rede, sockets, symlinks, capabilities, cgroups, proteção E4 de configuração, gateway/UDS (incluindo slowloris) e execução do adaptador Pi 1.2.0 aprovadas sobre os componentes reais de produto. |
| C2-07 | **`POC-02` PASS (evidência reproduzível):** tentativas do agente/processo no perfil `developer` de ler/alterar/remover/sombrear ATs selados ou runner são bloqueadas; adulteração na store ou tampering recursivo via workspace são detectados e recusados pelo Executor F-04. |
| C2-08 | **`POC-04` PASS (evidência reproduzível):** relatório falsificado pelo agente no workspace/stdout ou tentativa de `os._exit(0)` no código testado não geram `PASS` nem habilitam `COMPLETE_RUN` (SQLite v4 mantém bloqueio exit `4` com evidência oficial `FAIL`); artefato válido promovido gera evidência oficial `PASS` vinculada a `run_id`/`artifact_digest`/`at_version` na store e no SQLite v4, habilitando `ACCEPT_ARTIFACT = ALLOW` e conclusão `IN_PROGRESS → DONE`. |
| R1 | **Regressão zero:** 100% dos testes complementares existentes + novos testes do Change 2 PASS; **50/50** AT selados congelados (`v1`, `v2`, `v5`, `v6`) PASS em Podman rootless sem alterar nenhum selo/AT/operador. |
| R2 | **Revisão independente F-02:** revisão em contexto separado (`research`) sobre o diff candidato sem achados abertos; resumo compacto (`<= 32 KiB`) gravado e documentação ativa sincronizada antes de integrar em `main`. |

---

## 5. Suposições explícitas

1. **Construção offline da imagem OCI local:** `runtime/build_image.py` tentará construir 100% offline sobre a base Alpine local + staging dos binários/libs `glibc` do host (Node v22, Pi 1.2.0, Python 3.13 + stdlib/`lib-dynload`). Se houver incompatibilidade bloqueante em T1, pararemos e pediremos autorização explícita antes de qualquer `pull`/`apk`/`apt` em rede (Regra #6).
2. **Credenciais reais fora do fluxo padrão:** toda a verificação obrigatória do Change 2 (`unittest`, `doctor --conformance`, `POC-01`, `POC-02`, `POC-04`) roda 100% offline com backend `stub` e zero credenciais reais. Uma rodada adicional opcional com `--live-openrouter-free` só será executada se o usuário solicitar e autorizar explicitamente.
3. **Integração em `main` somente ao final:** todo o desenvolvimento ocorre na branch `feat/change2-vertical-runtime-pi-gateway-poc` até que `R1`, `R2` e a sincronização documental estejam concluídos.
4. **Nenhuma implementação de código de produto começa antes da confirmação do plano e aprovação explícita.**
