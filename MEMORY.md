# UDH — retomada curta

Atualizado: 25/11/2026. Ler [AGENTS.md](AGENTS.md), [STATE.md](STATE.md) e conferir Git. Memória não aprova requisitos nem substitui evidências.

## Estado confirmado
- **Change 4 (`artifact-preview-and-guided-ui`) concluído, verificado (`20150ac`) e integrado em `main`:** imagem OCI `artifact-only` sem agentes (`localhost/udh-runtime-artifact@sha256:0eda5898...`), ponte reversa `/preview-ipc/preview.sock` anti-TOCTOU (`O_PATH|O_NOFOLLOW` + `SO_PEERCRED`/`ns/net`), proxy isolado `p-<24hex>.localhost` (grant one-time → cookie host-only `SameSite=Lax`, CSP `style-src 'self'`, `POST /api/previews/<pid>/grant`), smoke test `B3.4` com reinício real e `ADR-028` (`PENDING→DELIVERED`), e interface guiada mínima `B1.7` (`python3 -m udh.web`, `X-UDH-Token` em `/api/*`, zero cookies, zero `innerHTML`, cartões `ADR-025`).
- **Evidências em `20150ac`:** **157/157** complementares PASS, **50/50** AT selados congelados PASS (`v6` 16/16, `v5` 13/13, `v2` 11/11, `v1` 10/10; 714 chamadas, 0 infra), **4/4 POCs (`POC-01`–`POC-04`) PASS**, **Mozilla Firefox 153.7.0esr** headless offline PASS (`C4-06`) e revisão independente R2/F-02 **PASS** ([resumo](openspec/changes/artifact-preview-and-guided-ui/verification-summary.json)).
- Governança em [openspec/project.md](openspec/project.md) (`bootstrap/slice1/` congelado, resumos `<= 32 KiB`, 1 branch por change).

## Próximo passo
- Branch `feat/change5-discover-plan-tickets` (`089530d`): **todos os gates offline + R2/F-02 `PASS`** (`225/225` complementares, `doctor` `READY`, `4/4` POCs, `50/50` AT `v1–v6`, [resumo](openspec/changes/discover-plan-tickets/verification-summary.json)). **Bloqueio para merge:** ensaio ao vivo `DISC-01` (`deepseek-v4.1-flash-free` no OpenCode Zen retornou `HTTP 429` com `'public'` e com chave pessoal). Próximo: rodar `DISC-01` com outro modelo/API oficial ou Ollama local e então integrar em `main`. T3-org adiada.

## Cuidados
- `DOC`/`SKILL`/`DISC`/`TICKET`, `B2.4`, `B3.8`/`B3.9`, `UX-04`, Codex, Chromium/Chrome e WSL2 seguem `NOT_RUN`; ainda não declarar MVP liberável completo.
- `openrouter_free` foi validado apenas com dublês determinísticos (zero credenciais reais); rede externa exige consentimento explícito.
- Não alterar selos `v1`–`v6`, AT protegidos, `udh/operator.py`, `udh/policy.py`, `udh/__main__.py`, `runtime/image-manifest.json` nem `runtime/opencode-image-manifest.json`; zero arquivos não-`.py` em `udh/`; 1 branch por change + atualizar docs ativos a cada commit.
