# UDH — retomada curta

Atualizado: 23/11/2026. Ler [AGENTS.md](AGENTS.md), [STATE.md](STATE.md) e conferir Git. Memória não aprova requisitos nem substitui evidências.

## Estado confirmado
- **Change 2 (`vertical-runtime-pi-gateway-poc`) concluído, verificado (`beac057`) e integrado em `main`:** Runtime Manager rootless (`developer`/`artifact`), imagem OCI local offline por digest (`localhost/udh-runtime@sha256:bed9a71a...`), Gateway stdlib endurecido (UDS/`SO_PEERCRED`/slowloris/`stub`+`openrouter_free`), adaptador Pi 1.2.0 (E4 + parser JSONL), TrustStore + Executor Confiável F-04 (`PR_SET_DUMPABLE=0` pai/worker + `stdin` nonce + AST duplo) e `doctor --conformance` **`READY`**.
- **Evidências em `beac057`:** **125/125** complementares PASS, **50/50** AT selados congelados PASS (`v6` 16/16, `v5` 13/13, `v2` 11/11, `v1` 10/10; 714 chamadas, 0 infra), **`POC-01`, `POC-02` e `POC-04` PASS** (Linux x86_64 rootless, backend `stub` offline) e revisão independente R2/F-02 **PASS** ([resumo](openspec/changes/vertical-runtime-pi-gateway-poc/verification-summary.json)). `udh/operator.py` e `bootstrap/slice1/` intocados.
- Governança em [openspec/project.md](openspec/project.md) (B1–B4, Emenda ADR-018 A, congelamento arquitetural, `bootstrap/slice1/` congelado, resumos `<= 32 KiB`, 1 branch por change).

## Próximo passo
- Definir e aprovar o OpenSpec do **Change 3** em branch dedicada (`feat/<change3>`): segundo agente (OpenCode) + `POC-03` (handoff e retomada F-03) e/ou fluxo de preview local (`B3.6`) + interface guiada (`B1.7`).

## Cuidados
- `POC-03`, `DOC`/`SKILL`/`DISC`/`TICKET`, `UX-04` e WSL2 seguem `NOT_RUN`; não declarar MVP liberável antes de >= 2 agentes conformes + `POC-03` (B1.6).
- `openrouter_free` foi validado em testes com dublês determinísticos (zero credenciais reais); chamada em rede exige consentimento explícito.
- Não alterar selos `v1`–`v6`, AT protegidos nem `udh/operator.py`; zero arquivos não-`.py` em `udh/`; 1 branch por change + atualizar docs ativos a cada commit. Sem skills globais; commits em português.
