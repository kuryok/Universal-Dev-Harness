# UDH — retomada curta

Atualizado: 24/11/2026. Ler [AGENTS.md](AGENTS.md), [STATE.md](STATE.md) e conferir Git. Memória não aprova requisitos nem substitui evidências.

## Estado confirmado
- **Change 4 (`artifact-preview-and-guided-ui`) concluído, verificado (`20150ac`) e integrado em `main`:** imagem OCI `artifact-only` sem agentes (`localhost/udh-runtime-artifact@sha256:0eda5898...`), ponte reversa `/preview-ipc/preview.sock` anti-TOCTOU (`O_PATH|O_NOFOLLOW` + `SO_PEERCRED`/`ns/net`), proxy isolado `p-<24hex>.localhost` (grant one-time → cookie host-only `SameSite=Lax`, CSP `style-src 'self'`, `POST /api/previews/<pid>/grant`), smoke test `B3.4` com reinício real e `ADR-028` (`PENDING→DELIVERED`), e interface guiada mínima `B1.7` (`python3 -m udh.web`, `X-UDH-Token` em `/api/*`, zero cookies, zero `innerHTML`, cartões `ADR-025`).
- **Evidências em `20150ac`:** **157/157** complementares PASS, **50/50** AT selados congelados PASS (`v6` 16/16, `v5` 13/13, `v2` 11/11, `v1` 10/10; 714 chamadas, 0 infra), **4/4 POCs (`POC-01`–`POC-04`) PASS**, **Mozilla Firefox 153.7.0esr** headless offline PASS (`C4-06`) e revisão independente R2/F-02 **PASS** ([resumo](openspec/changes/artifact-preview-and-guided-ui/verification-summary.json)).
- Governança em [openspec/project.md](openspec/project.md) (`user_version=4` preservado, `bootstrap/slice1/` congelado, resumos `<= 32 KiB`, 1 branch por change).

## Próximo passo
- Definir e aprovar o OpenSpec do **Change 5** em branch dedicada (`feat/<change5>`): `DISCOVER`/`SPEC`/`PLAN` + multi-ticket DAG (`DISC-01`, `TICKET-01/02`), ou outra prioridade definida pelo usuário.

## Cuidados
- `DOC`/`SKILL`/`DISC`/`TICKET`, `B2.4`, `B3.8`/`B3.9`, `UX-04`, Codex, Chromium/Chrome e WSL2 seguem `NOT_RUN`; ainda não declarar MVP liberável completo.
- `openrouter_free` foi validado apenas com dublês determinísticos (zero credenciais reais); rede externa exige consentimento explícito.
- Não alterar selos `v1`–`v6`, AT protegidos, `udh/operator.py`, `udh/policy.py`, `udh/__main__.py`, `runtime/image-manifest.json` nem `runtime/opencode-image-manifest.json`; zero arquivos não-`.py` em `udh/`; 1 branch por change + atualizar docs ativos a cada commit.
