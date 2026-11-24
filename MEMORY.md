# UDH — retomada curta

Atualizado: 24/11/2026. Ler [AGENTS.md](AGENTS.md), [STATE.md](STATE.md) e conferir Git. Memória não aprova requisitos nem substitui evidências.

## Estado confirmado
- **Change 3 (`opencode-handoff-poc03`) concluído, verificado (`5164e26`) e integrado em `main`:** imagem OCI dedicada do OpenCode 1.18.35 (`localhost/udh-runtime-opencode@sha256:7af7a5d4...`, mantendo imagem Pi/`artifact` intocada), Gateway com `allowed_tool_names` fail-closed + `stub_turns`, `OpenCodeAdapter` (E3 `zero_tools` + E4 `/udh/agent-config:ro` + anti-`/workspace/opencode.json`), `udh/handoff.py` (`git_dir` externo sanitizado + F-03 + ADR-026) e `doctor --conformance` **`READY`** (`['pi', 'opencode']`).
- **Evidências em `5164e26`:** **140/140** complementares PASS, **50/50** AT selados congelados PASS (`v6` 16/16, `v5` 13/13, `v2` 11/11, `v1` 10/10; 714 chamadas, 0 infra), **4/4 POCs fundacionais (`POC-01`–`POC-04`) PASS** (Linux x86_64 rootless, backend `stub` offline) e revisão independente R2/F-02 **PASS** ([resumo](openspec/changes/opencode-handoff-poc03/verification-summary.json)).
- Governança em [openspec/project.md](openspec/project.md) (B1–B4, Emenda ADR-018 A, congelamento arquitetural, `bootstrap/slice1/` congelado, resumos `<= 32 KiB`, 1 branch por change).

## Próximo passo
- Definir e aprovar o OpenSpec do **Change 4** em branch dedicada (`feat/<change4>`): preview web local isolado (`B3.6`) + interface guiada mínima (`B1.7`).

## Cuidados
- `DOC`/`SKILL`/`DISC`/`TICKET`, `B3.6`/`B1.7`, `UX-04`, Codex e WSL2 seguem `NOT_RUN`; não declarar MVP liberável antes da experiência guiada e entrega ponta a ponta.
- `openrouter_free` foi validado apenas com dublês determinísticos (zero credenciais reais); chamada em rede exige consentimento explícito.
- Não alterar selos `v1`–`v6`, AT protegidos, `udh/operator.py`, `udh/policy.py` nem `runtime/image-manifest.json`; zero arquivos não-`.py` em `udh/`; 1 branch por change + atualizar docs ativos a cada commit.
