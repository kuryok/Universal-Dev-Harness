# UDH — retomada curta

Atualizado: 28/11/2026. Ler [AGENTS.md](AGENTS.md), [STATE.md](STATE.md) e conferir Git. Memória não aprova requisitos nem substitui evidências.

## Estado confirmado
- **Change 5 (`discover-plan-tickets`) verificado e pronto para merge:** SQLite v5–v9 (planos persistidos `TICKET-01`, contratos B4.2 e descoberta `DISC-01`), handoff gerenciado E2E offline `TICKET-02` (`Pi -> OpenCode + artifact-only`), gates offline completos (`225/225` complementares PASS, `4/4` POCs PASS, `50/50` AT selados `v1–v6` PASS, revisão R2/F-02 PASS em `089530d`).
- **Ensaio ao vivo `DISC-01` (`C5-T3b.2b-03`) PASS:** executado com sucesso em contêiner Podman (`network=none`), skill `grill-with-docs-udh`, Gateway local para Ollama (`qwen2.5:7b-instruct-q4_K_M`) e validação canônica estrita com OpenSpec CLI 2.1.1 até `SPEC_READY` em ambos os casos ([resumo](openspec/changes/discover-plan-tickets/verification-summary.json)).
- Governança em [openspec/project.md](openspec/project.md) (`bootstrap/slice1/` congelado, resumos `<= 32 KiB`, 1 branch por change).

## Próximo passo
- Integrar a branch `feat/change5-discover-plan-tickets` em `main` e taggear marco verificado. Após o merge, iniciar o próximo change aprovado no OpenSpec.

## Cuidados
- `DOC`/`SKILL`, `B2.4`, `B3.8`/`B3.9`, `UX-04`, Codex, Chromium/Chrome e WSL2 seguem `NOT_RUN`; ainda não declarar MVP liberável completo.
- `openrouter_free` foi validado apenas com dublês determinísticos (zero credenciais reais); rede externa exige consentimento explícito.
- Não alterar selos `v1`–`v6`, AT protegidos, `udh/operator.py`, `udh/policy.py`, `udh/__main__.py`, `runtime/image-manifest.json` nem `runtime/opencode-image-manifest.json`; zero arquivos não-`.py` em `udh/`; 1 branch por change + atualizar docs ativos a cada commit.
