# Estado do projeto

Atualizado: 28/11/2026. Fase: **Change 5 (`discover-plan-tickets`) verificado e pronto para merge** — gates offline (225/225, 4/4 POCs, 50/50 AT v1–v6, R2/F-02 PASS) e ensaio ao vivo de `DISC-01` (`C5-T3b.2b-03`) verificado PASS com Pi 1.2.0, Ollama local (`qwen2.5:7b-instruct-q4_K_M`) e OpenSpec 2.1.1.

Retomada: [AGENTS.md](AGENTS.md), este estado e [MEMORY.md](MEMORY.md). Normativo: [baseline](Universal-Dev-Harness-v2_5-BASELINE-NORMATIVO-CONSOLIDADO-FINAL.md) + [openspec/project.md](openspec/project.md); [v2.5.1](UDH-v2.5-ARQUITETURA-CONSOLIDADA-v2.5.1.md) é nota derivada.

## Funciona

- **Core SQLite v1–v4 e extensões Change 5 (SQLite v5–v9):** ledger `transitions`, checkpoints F-03, leitura O(1), Política v2, entrega ADR-028, planos persistidos de tickets (`TICKET-01`), contratos de cobertura B4.2 e sessões de descoberta persistidas (`DISC-01`).
- **3 imagens OCI dedicadas (`udh/runtime.py`, `runtime/`):** Pi 1.2.0, OpenCode e `artifact-only` sem agentes (`localhost/udh-runtime-artifact`).
- **Gateway + Handoff F-03 + Troca de Agentes F-03/F-04 (`TICKET-02`):** handoff gerenciado E2E offline verificado PASS (`Pi -> OpenCode + artifact-only`), e 4/4 POCs PASS.
- **Descoberta e Especificação (`DISC-01`):** fluxo completo validado ao vivo com Pi 1.2.0 em contêiner (`network=none`), skill `grill-with-docs-udh`, inferência local Ollama, bloqueio de respostas não confirmadas e validação de snapshots canônicos com CLI oficial OpenSpec 2.1.1 até `SPEC_READY`.
- **Verificação geral:** 225/225 complementares, 50/50 AT selados (v1–v6; 714 chamadas, 0 infra), POC-01–04 4/4, revisão independente R2/F-02 PASS e ensaio ao vivo DISC-01 PASS ([resumo](openspec/changes/discover-plan-tickets/verification-summary.json)).

## Pendente

- **Integração em `main`:** merge da branch `feat/change5-discover-plan-tickets` em `main`.
- **Restante do produto:** perfil `provision` (`B2.4`), execução protegida persistente (`B3.8`) e exportação (`B3.9`), `DOC-01/02`, `SKILL-01/02`, `UX-04`, terceiro agente (Codex), WSL2 (`B1.1b`), Chromium/Chrome, Memorix, Judge consultivo e instalador. Ainda não é MVP liberável completo.

## Próxima tarefa

Realizar o merge da branch `feat/change5-discover-plan-tickets` em `main` com tag anotada de marco verificado e iniciar o próximo change aprovado no OpenSpec.

## Verificação

- `UDH_TEST_DISCOVERY_PI=1 UDH_TEST_OPENSPEC=/home/debian/.config/nvm/versions/node/v26.13.0/bin/openspec python3 -m unittest discover -s tests` (225 PASS).
- Ensaio real de descoberta via Ollama local (`C5-T3b.2b-03`: `qwen2.5:7b-instruct-q4_K_M` PASS até `SPEC_READY`).
- `PYTHONDONTWRITEBYTECODE=1 python3 -m udh.poc` (4/4 PASS).
- [README](README.md) para entrada. Não abrir credenciais nem limpar o workspace sem autorização.
