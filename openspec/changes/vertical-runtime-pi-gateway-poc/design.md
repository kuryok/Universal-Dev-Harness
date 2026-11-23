# Design Técnico — Change 2 (`vertical-runtime-pi-gateway-poc`)

Documento técnico complementar a [proposal.md](proposal.md). Incorpora integralmente os ajustes da revisão independente F-02. Nenhum código de produto é alterado por este arquivo.

---

## 1. Arquitetura do marco vertical (Core único, módulos coesos)

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    UDH Core (Domínio Confiável Linux)                   │
│                                                                         │
│  CoreService (udh/service.py) ──► SQLiteStore v4 (udh/store.py)         │
│       │                           Policy v2 (udh/policy.py)             │
│       │                           Doctor (udh/doctor.py)                │
│       ├──► TrustStore (udh/trust_store.py)   [0700, fora do workspace]  │
│       │      ├── sealed_at/<at_version>/     (ATs + manifesto SHA-256)  │
│       │      ├── artifacts/<digest>/         (snapshot imutável)        │
│       │      ├── evidence/<run_id>/          (relatórios oficiais)      │
│       │      ├── audit/<run_id>.jsonl        (auditoria do gateway)     │
│       │      └── conformance.json            (cache policy+digest)      │
│       │                                                                 │
│       ├──► InferenceGateway (udh/gateway.py) [UDS /tmp curto + PEERCRED]│
│       │      ├── Backend: stub (determinístico offline, padrão)         │
│       │      └── Backend: openrouter_free (opt-in HTTPS, max_price=0)   │
│       │                                                                 │
│       ├──► PiAdapter (udh/adapters/pi.py)    [Pi 1.2.0 + parser JSONL]  │
│       │                                                                 │
│       └──► TrustedExecutor (udh/executor.py) [F-04, nunca importa app]  │
│                      │                                                  │
│                      ▼                                                  │
│            RuntimeManager (udh/runtime.py)   [Podman rootless, @sha256] │
└──────────────┬──────────────────────────────────┬───────────────────────┘
               │ Perfil: developer                │ Perfil: artifact (F-04)
               ▼                                  ▼
┌─────────────────────────────────┐  ┌────────────────────────────────────┐
│ Contêiner Rootless (network=none│  │ Contêiner Rootless (network=none)  │
│ RO root, cap-drop=ALL, keep-id, │  │ RO root, cap-drop=ALL, keep-id,    │
│ --user uid:gid)                 │  │ --user uid:gid; SEM agente/IPC     │
│                                 │  │                                    │
│ /workspace             (:rw)    │  │ /artifact             (:ro)        │
│ /udh/agent-config      (mount)  │  │ /sealed-at            (:ro)        │
│   ├── models.json      (:ro)    │  │ /work                 (:rw temp)   │
│   ├── settings.json    (:ro)    │  │ /opt/udh/at_runner.py (:ro imagem) │
│   └── auth.json        (:ro)    │  │                                    │
│ /run/udh-scratch       (:rw)    │  │ stdin ──► nonce (fechado antes de  │
│   (HOME, TMPDIR, bridge-ready)  │  │           tocar /artifact)         │
│ /ipc/gateway.sock      (:ro)    │  │ pai at_runner.py (PR_SET_DUMPABLE=0│
│ /opt/udh/bridge.mjs    (:ro)    │  │   + AST inventory)                 │
│ /opt/pi/... (Pi 1.2.0) (:ro)    │  │   └─► worker filho (python3 -I -B, │
└─────────────────────────────────┘  │       stdin=DEVNULL, sem nonce)    │
                                     └────────────────────────────────────┘
```

---

## 2. Imagem OCI dedicada por digest (`runtime/`)

### 2.1 Estratégia de build offline (`runtime/build_image.py` + `runtime/Containerfile`)
- **Problema técnico endereçado (`glibc` vs `musl`):** o storage local do Podman dispõe de base Alpine (`musl`), enquanto o host Debian 13 fornece Node v22 (`/home/debian/.local/share/pi-node/current/bin/node`), Pi `1.2.0` (`/home/debian/.pi/agent/install/releases/1.2.0/node_modules`) e Python 3.13 (`/usr/bin/python3` + `/usr/lib/python3.13`, inclusive `lib-dynload/*.so`) linkados dinamicamente a `glibc` (`/lib64/ld-linux-x86-64.so.2`, `libc.so.6`, `libstdc++.so.6`, `libsqlite3.so.0`, `libssl.so.3`, `libcrypto.so.3`, `libz.so.1`, `libexpat.so.1`, `libffi.so.8`, etc.).
- **Build reproduzível sem commitar binários e sem baixar da rede:**
  1. `runtime/build_image.py` (100% Python stdlib) cria um diretório temporário de staging (fora do Git) contendo:
     - `/opt/udh/bin/node` (Node v22) e `/opt/pi/node_modules` (Pi 1.2.0, validando ausência de symlinks escapando da raiz);
     - `/usr/bin/python3` e a stdlib Python 3 completa (sem `dist-packages`/pacotes de terceiros);
     - fechamento transitivo `ldd` do executável `node`, do executável `python3` e de todos os `.so` em `lib-dynload/` (incluindo o dynamic linker `/lib64/ld-linux-x86-64.so.2`), preservando seus caminhos esperados no rootfs da imagem;
     - `/opt/udh/bridge.mjs` (`0444`) e `/opt/udh/at_runner.py` (`0444`);
     - diretórios mountpoint vazios `/udh/agent-config`, `/run/udh-scratch`, `/ipc`, `/workspace`, `/artifact`, `/sealed-at`, `/work`.
  2. Executa `podman build --pull=never --network=none --build-context stage=<tempdir> -f runtime/Containerfile -t localhost/udh-runtime:change2 runtime/`.
  3. Executa **smoke check obrigatório dentro da imagem construída** (com `--network=none --read-only --cap-drop=ALL`):
     - `python3 -I -c "import sqlite3, ssl, json, unittest, hashlib, ast; print('PY_OK')"`
     - `/opt/udh/bin/node /opt/pi/node_modules/@earendil-works/pi-coding-agent/dist/bundle/cli.js --version` (`1.2.0`).
  4. Extrai o digest canônico `sha256:...` via `podman image inspect` e grava `runtime/image-manifest.json` com `image_repository`, `image_digest`, `stack_profile: "python3-stdlib-v1"`, `pi_version: "1.2.0"`, `bridge_sha256`, `at_runner_sha256`.
  5. **Regra de parada (Regra #6):** se alguma dependência nativa/base impedir o build 100% offline sobre a base Alpine local, o script falha fechado e **não** faz `pull`/`apk`/`apt` em rede — pausamos para aprovação explícita do usuário.

### 2.2 Regra fail-closed no consumo (`udh/runtime.py`)
- `RuntimeManager` exige referência canônica `<repo>@sha256:<64-hex>`, consulta `podman image inspect` confirmando existência local do digest exato e passa sempre `--pull=never`. Digest ausente, formato inválido ou divergência no manifesto falham fechado (`IMAGE_DIGEST_UNAVAILABLE`).

---

## 3. Runtime Manager rootless (`udh/runtime.py`)

### 3.1 Flags obrigatórias em ambos os perfis (`developer` e `artifact`)
- `--pull=never`
- `--network=none`
- `--read-only`
- `--read-only-tmpfs=false`
- `--cap-drop=ALL`
- `--security-opt=no-new-privileges`
- `--userns=keep-id`
- `--user <os.getuid()>:<os.getgid()>` (garante UID/GID idêntico independente do `USER` da imagem OCI)
- Limites cgroup v2 validados positivamente antes do spawn:
  - `developer`: padrão `memory=512m`, `memory_swap=512m`, `pids_limit=96`, `cpus=1.0`, `timeout=60s`.
  - `artifact`: padrão `memory=256m`, `memory_swap=256m`, `pids_limit=64`, `cpus=1.0`, `timeout=30s`.

### 3.2 Validação estrutural de mounts (`--mount`)
- Todos os bind mounts usam `--mount type=bind,source=<src>,destination=<dst>[,readonly=true]` após validação estrita:
  - rejeitar qualquer path contendo `:`, `,`, `\n`, `\r` ou `\x00`;
  - resolver `src = Path(source).resolve(strict=True)` e garantir ausência de sobreposição entre workspace, `TrustStore` e IPC;
  - rejeitar explicitamente qualquer mount onde `src` englobe `$HOME` (`home.is_relative_to(src)`), `/`, `/proc`, `/sys`, `/etc`, `/run/podman`, `/var/run/docker.sock`, ou qualquer caminho da `TrustStore` com modo gravável (`readonly=False`).

### 3.3 Ciclo de vida, `stdin`, `on_started`, `inspect` e limpeza
- Fluxo em `RuntimeManager`:
  1. `podman create --name <name> --interactive` (quando há payload `stdin`, ex.: `nonce` do F-04) + flags obrigatórias + `<repo>@<digest>` + comando.
  2. `podman start --attach [--interactive] <name>` com `start_new_session=True`; quando `stdin_bytes` é fornecido, escreve e fecha o pipe `stdin` imediatamente (EOF garantido).
  3. Callback opcional `on_started(container_name)` para inspeção viva de namespaces/mounts (usado em `POC-01`).
  4. Drenagem simultânea não-bloqueante de `stdout`/`stderr` com `selectors.DefaultSelector`, deadline absoluto e teto configurável de bytes por stream (`256 KiB` padrão).
  5. Em `TIMEOUT` ou `OUTPUT_LIMIT`: mata o grupo do processo cliente **e** executa `podman kill <name>` antes da inspeção final.
  6. Executa `podman inspect <name>` para registrar estado real (`ExitCode`, `OOMKilled`, `NetworkMode`, `ReadonlyRootfs`, `CapDrop`, `SecurityOpt`).
  7. Executa `podman rm --force --time=0 <name>` e confirma `podman container exists <name>` == exit `1` (falha fechada se sobrar contêiner órfão).

---

## 4. Gateway de inferência stdlib endurecido (`udh/gateway.py`)

### 4.1 Transporte UDS seguro (`<= 100` bytes) e leitor HTTP/1.1 direto sobre `socket.recv()`
- **Caminho curto do UDS (sem `os.chdir()`):** cria diretório `0o700` sob `/tmp` (ex.: `/tmp/udh-ipc-<12hex>/gateway.sock`, ~35 bytes — ou `$XDG_RUNTIME_DIR` somente se o caminho final tiver `<= 100` bytes UTF-8), validando `len(path_bytes) < 107` antes de `bind()` e aplicando `os.chmod(sock_path, 0o600)`.
- **Concorrência:** `ThreadingMixIn` + `UnixStreamServer` com `BoundedSemaphore(max_concurrency=4)`. Quando saturado, **não** derruba a conexão silenciosamente: envia HTTP `503 Service Unavailable` (`{"error":"concurrency_limit_exceeded"}`) com `Connection: close` e garante `release()` em `finally`.
- **Autenticação de peer:** `getsockopt(SOL_SOCKET, SO_PEERCRED, 12)` → `(pid, uid, gid)`. Se `uid != os.getuid()` → `403 peer_denied`.
- **Leitor HTTP/1.1 direto sobre `socket.recv()` (fecha achado slowloris C2):**
  - **Não** usa `BaseHTTPRequestHandler.handle_one_request()` (que reinicia timeout a cada byte recebido em `rfile.readline()`).
  - Implementa `_read_http_request(conn, *, header_deadline, body_deadline, max_header_bytes=16384, max_body_bytes=262144)`:
    1. Loop de leitura de cabeçalhos chamando `conn.settimeout(max(0.01, header_deadline - time.monotonic()))` **antes de cada `conn.recv(4096)`**, encerrando deterministicamente com `408 request_header_timeout` se ultrapassar `header_deadline` (padrão `5.0s`, inclusive contra envio de 1 byte/s) ou `431 headers_too_large` se exceder `16 KiB`.
    2. Validação estrita da request-line (`POST`, `/v1/chat/completions`, `HTTP/1.1` ou `HTTP/1.0`) e de todos os cabeçalhos (preservando duplicatas para detecção):
       - Método != `POST` → `403 method_denied`; path != `/v1/chat/completions` → `403 route_denied`.
       - Exatamente 1 `Authorization: Bearer <token>` validado com `hmac.compare_digest` → senão `401 capability_denied`.
       - Exatamente 1 `X-UDH-Purpose: inference` → senão `403 purpose_denied`.
       - Presença de `Transfer-Encoding` ou quantidade de `Content-Length != 1` ou valor não-inteiro negativo → `400 unsupported_framing`.
       - `Content-Length > max_body_bytes` → `413 size_denied` (antes de ler o corpo).
    3. Loop de leitura do corpo com `conn.settimeout(max(0.01, body_deadline - time.monotonic()))` antes de cada `recv()`, encerrando com `408 request_body_timeout` se estourar `body_deadline` (padrão `10.0s`).
    4. Parse JSON com `strict_json_loads` (rejeitando `NaN`/`Infinity`): exige `dict`, `model in allowed_models`, `stream is True`, `isinstance(messages, list)` e `allow_tools` conforme política do perfil.
- **Orçamento por `run_id`:** `threading.Lock` controla `max_calls` (`429 run_budget_exceeded`), `max_response_bytes` e `call_timeout_seconds`.

### 4.2 Backends (`stub` e `openrouter_free`)
- **`stub` (padrão offline):** emite stream SSE OpenAI-compatible determinístico (`UDH_CONFORMANCE_OK` ou texto controlado), com suporte a modo `cut_after_first_chunk=True` para provar C4 (corte abrupto no meio do stream).
- **`openrouter_free` (opt-in explícito):**
  - Host fixo `openrouter.ai`, `http.client.HTTPSConnection` com `ssl.create_default_context()`, **sem seguir redirects 3xx** (qualquer `status != 200` é rejeitado como `502 provider_http_<status>`).
  - **Reconstrução por allowlist estrita do corpo enviado ao upstream:** envia apenas `{"model": model, "stream": True, "messages": messages, "max_tokens": min(req_max, limit), "provider": {"max_price": {"prompt": 0, "completion": 0}}}` (mais `tools` apenas se `allow_tools=True`). Qualquer chave `provider`, `route`, `models`, `transforms`, etc. enviada pelo contêiner é descartada.
  - Validação prévia no catálogo `GET /api/v1/models` (teto 4 MiB): exige modelo presente com sufixo `:free`, `pricing` como `dict` não vazio e **todos** os campos numéricos/string de `pricing` conversíveis em `Decimal` finito igual a `0` (`prompt`, `completion`, `request`, `image`, etc.).
  - Durante o stream SSE, confere `usage.cost == 0` quando presente e aplica **varredura em janela deslizante** (buffer dos últimos `len(secret)` bytes + chunk atual) para detectar vazamento da credencial mesmo que fatiado entre linhas/chunks (`secret_in_response_blocked` → fecha conexão imediatamente).

### 4.3 Auditoria (B2.3 / DEF-04)
- JSONL append-only na `TrustStore` (`audit/<run_id>.jsonl`) contendo apenas metadados seguros (`timestamp`, `run_id`, `policy_sha256`, `purpose`, `backend`, `status`, `verdict`, `peer_uid`, `model`, `has_tools`, `request_bytes`, `response_bytes`, `stream_events`, `usage`, `elapsed_seconds`). Nunca registra `Authorization`, credenciais ou corpos brutos de erro do provedor.

---

## 5. Adaptador Pi 1.2.0 (`udh/adapters/pi.py`) e Ponte (`runtime/bridge.mjs`)

### 5.1 Ponte defensiva (`runtime/bridge.mjs`)
- Listener exclusivamente em `127.0.0.1:43127` dentro do namespace `network=none`, encaminhando para `/ipc/gateway.sock`.
- Defesa em profundidade (mantendo `udh/gateway.py` como autoridade real): valida `method === 'POST'`, `url === '/v1/chat/completions'`, `x-udh-purpose` vazio ou `'inference'`, ausência de `transfer-encoding`, `content-length` único válido, e configura `server.headersTimeout`, `server.requestTimeout` e timeout de upstream.

### 5.2 Isolamento de configuração (E4) e scratch fora de `/workspace`
- `/udh/agent-config` (mountpoint fora de `/workspace`, evitando contaminação do artefato e pre-shadowing): montado `:rw` para os locks `.lock` exigidos pelo Pi, com `models.json`, `settings.json` e `auth.json` individuais montados `:ro` a partir de diretório temporário no domínio confiável; hashes SHA-256 conferidos antes e depois da execução.
- `/run/udh-scratch` (`:rw` fora de `/workspace`): abriga `HOME=/run/udh-scratch/home`, `TMPDIR=/run/udh-scratch/tmp` e `/run/udh-scratch/bridge-ready` (nenhum artefato de infraestrutura é gravado em `/workspace`).

### 5.3 Parser JSONL estrito do Pi (lição C4/F1)
- Invocação: `--offline --no-mcp --no-extensions --no-skills --no-prompt-templates --no-context-files --no-themes --no-approve --no-session --mode json --print`.
- O adaptador faz parse de cada linha JSON do `stdout` com `strict_json_loads`, coletando todos os eventos do assistente e seus `stopReason`:
  - **Condição necessária e suficiente de sucesso (`ok=True`):** `container.returncode == 0` **E** `container.outcome is None` **E** pelo menos um evento final do assistente **E** `finals[-1].get('stopReason') == 'stop'` **E** **nenhum** evento no stream com `stopReason in ('error', 'aborted')` ou tipo de erro de transporte/ponte **E** hashes E4 de configuração intactos.
  - Qualquer divergência (inclusive `exit 0` com `stopReason == 'error'` após queda do gateway) classifica deterministicamente como falha estruturada (`AGENT_STREAM_INTERRUPTED` / `AGENT_EXECUTION_FAILED`, `ok=False`).

---

## 6. Store Protegida (`udh/trust_store.py`) e Executor Confiável F-04 (`udh/executor.py`)

### 6.1 Layout da `TrustStore` (`0700`, padrão fora do workspace)
- **Caminho padrão** quando `--store` não é informado: `$XDG_DATA_HOME/udh/trust_store` (fallback `~/.local/share/udh/trust_store`), sempre validando ausência de symlinks/sobreposição com o workspace.
- Diretórios internos `0o755` e arquivos imutáveis `0o444` (proteção efetiva garantida por `0o700` na raiz da store + montagem `:ro` no contêiner, permitindo remoção limpa por `shutil.rmtree` em testes):
  - `sealed_at/<at_version_hex>/`: `manifest.json` + `tests/`. `at_version = "sha256:<digest>"`. `verify_sealed_at()` valida correspondência exata de arquivos, ausência de extras/symlinks e SHA-256 de cada arquivo.
  - `artifacts/<artifact_digest>/`: `manifest.json` + `tree/`. `promote_artifact()` varre recursivamente a origem com `os.walk(..., followlinks=False)` e abre arquivos com `os.O_RDONLY | os.O_NOFOLLOW`, rejeitando:
    - qualquer symlink, FIFO, socket ou device;
    - qualquer arquivo ou diretório de sombreamento em qualquer nível da árvore: `sitecustomize.py`, `sitecustomize/`, `usercustomize.py`, `usercustomize/`, `unittest.py`, `unittest/`, `udh.py`, `udh/`, ou qualquer `*.pth`.
  - `evidence/<run_id>/<request_id>.json`: relatório oficial imutável com `evidence_sha256`.
  - `conformance.json`: cache B1.3/B1.5 com `{policy_sha256, image_digest, adapter_name, adapter_version, status, verified_at}`.

### 6.2 Runner isolado F-04 (`runtime/at_runner.py` + `udh/executor.py`) — defesa contra forja
- **Separação rigorosa de processo e canal de `nonce` (fecha achado CRÍTICO #1):**
  1. `TrustedExecutor` gera `nonce = secrets.token_hex(32)` na memória do Core. **Nunca** passa `nonce` em `argv`, `env` ou arquivo.
  2. Executa `RuntimeManager.run_artifact(..., command=['python3', '-I', '-B', '/opt/udh/at_runner.py', '--artifact', '/artifact', '--at-dir', '/sealed-at'], stdin_bytes=json.dumps({'nonce': nonce}).encode())`.
  3. Dentro do contêiner (`artifact`), o processo **pai** `at_runner.py`:
     - Chama imediatamente `prctl(PR_SET_DUMPABLE, 0)` (via `ctypes.CDLL('libc.so.6')`) para impedir leitura de `/proc/<ppid>/mem` por qualquer subprocesso filho;
     - Lê `nonce` de `sys.stdin` e fecha `sys.stdin` antes de tocar `/artifact`;
     - Confirma que `unittest` provém da stdlib (fora de `/artifact`) e revalida recursivamente `/artifact` contra symlinks e módulos/pacotes sombreados proibidos (`sitecustomize*`, `usercustomize*`, `unittest*`, `udh*`, `*.pth`);
     - Faz **inventário estático por `ast`** dos arquivos `test_*.py` em `/sealed-at`, extraindo o conjunto exato de métodos `TestCase.test_*` esperados (`expected_tests`, exigindo `len(expected_tests) > 0`);
     - Dispara um **processo worker filho** (`python3 -I -B /opt/udh/at_runner.py --worker --artifact /artifact --at-dir /sealed-at`, `stdin=DEVNULL`, ambiente mínimo sem `nonce`, `stdout`/`stderr` em pipes internos do pai). O worker adiciona `/artifact` **ao final** de `sys.path` (após a stdlib), executa `unittest` sobre `/sealed-at` e escreve o relatório de casos executados em seu pipe ao pai;
     - O **pai** verifica: (a) worker retornou `0`; (b) o conjunto de testes reportados como executados e aprovados cobre **100%** de `expected_tests` (`failures == 0`, `errors == 0`, `skipped == 0`, `unexpected == []`); (c) todos os logs livres do worker/artefato são encaminhados apenas para `stderr`;
     - Somente o **pai** emite uma única linha JSON no `stdout` do contêiner contendo `{"udhf04": 1, "nonce": nonce, "expected_tests": [...], "passed_tests": [...], "tests_run": N, "failures": F, "errors": E, "tampering_ok": True}` e encerra com exit `0` se aprovado ou `1` caso contrário.
  4. No Core, `TrustedExecutor` decide o `verdict` (`PASS | FAIL | ERROR`): exige `container.returncode == 0` **E** `container.outcome is None` **E** `stdout` contendo exatamente o JSON válido com `hmac.compare_digest(parsed['nonce'], nonce)` **E** `tests_run > 0` **E** `failures == 0` **E** `errors == 0` **E** `set(passed_tests) == set(expected_tests)`.
     - Se código malicioso em `/artifact` invocar `os._exit(0)` no worker: o worker morre antes de completar a suíte → pai detecta falta dos testes esperados → pai reporta `FAIL` e sai `1`!
     - Se código malicioso tentar ler `/proc/<ppid>/cmdline`, `/proc/<ppid>/environ` ou `/proc/<ppid>/mem`: `nonce` não está em `cmdline`/`environ` e `PR_SET_DUMPABLE=0` bloqueia `mem`!
     - Qualquer arquivo falso em `/workspace` ou texto `"PASS"` no stdout do worker (capturado e desviado para `stderr`) é completamente ignorado!
  5. Grava o relatório oficial na `TrustStore`, calcula `evidence_sha256` e chama `CoreService.record_evidence(..., collector_route='TRUSTED_EXECUTOR')`.
  6. Na conclusão da execução, valida-se também que o `evidence_id` corresponde à evidência oficial mais recente para o par `(artifact_digest, at_version)`.

---

## 7. Evolução controlada de `doctor` e Política v2

### 7.1 `udh/doctor.py` (B1.3 / B1.5)
- `run_doctor(*, db_path=None, env_probes=None, store_dir=None, run_conformance=False)`:
  - Sem `env_probes`: resolve `store_dir` (informado ou padrão `$XDG_DATA_HOME/udh/trust_store`).
  - Se `env_probes` for passado sem `'runtime_components'`: preserva comportamento `LIMITED` (`GATEWAY_AND_ADAPTER_NOT_CONFIGURED`) compatível com os testes unitários do Change 1.
  - Modo normal (`run_conformance=False`): verifica `runtime/image-manifest.json`, presença local do digest no Podman e cache `conformance.json` na `TrustStore` para `(policy_sha256_v2, image_digest)`:
    - Cache válido `PASS` → `runtime_components = READY` (`RUNTIME_PI_CONFORMANT`, `conformant_adapters: ['pi']`) → `overall_status = 'READY'`.
    - Imagem ausente → `LIMITED` (`AGENT_IMAGE_NOT_BUILT`).
    - Cache ausente ou digest/política divergente (`STALE`) → `LIMITED` (`CONFORMANCE_STALE_OR_MISSING`).
  - Modo explícito (`run_conformance=True`, via `python3 -m udh doctor --conformance [--store PATH]`):
    - Se host/Podman não estiver `READY` ou imagem por digest estiver ausente ou a prova real do `PiAdapter` + `InferenceGateway(stub)` falhar → `runtime_components = NOT_READY` (`ADAPTER_CONFORMANCE_FAILED` / `AGENT_IMAGE_NOT_BUILT`) → **`overall_status = 'NOT_READY'` (exit `3`)** — nunca exit `0` sem conformance concluída!
    - Se passar → grava `conformance.json` vinculado a `(policy_sha256_v2, image_digest)` e retorna `overall_status = 'READY'` (exit `0`).

### 7.2 `udh/policy.py` (Política v2) e `udh/service.py`
- `POLICY_VERSION = 1` (bancos v1–v3): **zero alterações** (bit-a-bit idêntico).
- `POLICY_VERSION = 2` (bancos v4):
  - Assinatura pura (sem I/O): `evaluate(run, action, *, preflight_status=None, conformant_adapters=(), adapter='pi')`.
  - Em `EXECUTE_AGENT`:
    - `run.state != 'IN_PROGRESS'` → `DENY` (`RUN_NOT_IN_PROGRESS`).
    - `run.has_unknown_pendencies` → `DENY` (`UNKNOWN_PENDENCY_ACTIVE`).
    - `preflight_status == 'NOT_READY'` → `DENY` (`PREFLIGHT_NOT_READY`).
    - `preflight_status in ('READY', 'READY_WITH_LIMITS') and adapter in conformant_adapters` → **`ALLOW` (`AGENT_EXECUTION_AUTHORIZED`)**.
    - Caso contrário (incluindo `preflight_status is None` ou `conformant_adapters` vazio) → `DENY` (`GATEWAY_AND_ADAPTER_NOT_READY`).
  - `CoreService.authorize_agent_execution(run_id, *, store_dir=None, adapter='pi')` consulta `doctor(store_dir=store_dir)` para obter `preflight_status` e `conformant_adapters` e delega a `policy(...)`, enquanto `python3 -m udh policy` permanece inalterado (compatível com ATs selados `v2`).

---

## 8. Roteiro verificável das POCs (`udh/poc.py`)

- **`POC-01` (Isolamento + Gateway + Pi 1.2.0):**
  1. Validação fail-closed de imagem por digest (rejeição de tag sem digest e digest falso).
  2. Inspeção rootless (`uid_map`, `CapEff=0`, `NoNewPrivs=1`, `Seccomp=2`, raiz RO, namespaces via `on_started`, sem socket Podman/Docker, symlink externo bloqueado, segredo sintético ausente).
  3. Limites cgroup v2 efetivos (`memory` OOM exit 137, `pids` fork bloqueado, `cpus` throttling medido).
  4. Isolamento de rede (`network=none`, TCP/UDP externo e DNS recusados, loopback interno OK).
  5. Endurecimento do Gateway UDS (`0700`/`0600`, caminho curto `<= 100` bytes, `SO_PEERCRED`, `401`/`403`/`400`/`413`/`429`/`503`, slowloris 1 byte/s encerrado no deadline absoluto, streaming SSE, redação de segredos).
  6. Execução real do adaptador Pi 1.2.0 na imagem OCI dedicada (conformance `stub` com `/udh/agent-config` e `/run/udh-scratch` fora de `/workspace`, proteção E4 contra escrita/remoção/symlink/rename-shadowing + corte abrupto do gateway detectado pelo parser JSONL estrito).
- **`POC-02` (ATs protegidos + Baselines + F-04 tampering):**
  1. Processo no perfil `developer` tenta ler/alterar/apagar/substituir ATs selados na `TrustStore` → bloqueado por isolamento e hashes intactos.
  2. Adulteração simulada de arquivo/manifesto na `TrustStore` → Executor F-04 recusa (`AT_INTEGRITY_VIOLATION`).
  3. Tentativa de tampering recursivo via workspace (`sitecustomize.py`, `sub/unittest/foo.py`, `usercustomize/`, `*.pth`, symlink escapando) → bloqueada na promoção e/ou detectada pelo runner F-04 (`RUNNER_TAMPERING_DETECTED`).
- **`POC-04` (Evidência oficial não falsificável + SQLite v4):**
  1. Cenários negativos: (a) artefato defeituoso grava `/workspace/evidence.json` falso e imprime `"PASS"` no stdout; (b) artefato malicioso tenta invocar `os._exit(0)` ou inspecionar `/proc/<ppid>/cmdline`/`environ`/`mem` para capturar o `nonce` → Executor F-04 registra `FAIL` oficial na `TrustStore` e no SQLite v4; `CoreService.complete(...)` falha com `ConflictError` (exit `4`).
  2. Cenário positivo: artefato Python 3 stdlib válido promovido por `artifact_digest` → Executor F-04 executa ATs selados em perfil `artifact` (pai + worker isolado + inventário AST) → grava evidência oficial `PASS` vinculada a `run_id`, `artifact_digest` e `at_version` → registra em SQLite v4 → Política v2 `ACCEPT_ARTIFACT = ALLOW` → `CoreService.complete(...)` conclui `IN_PROGRESS → DONE`.
