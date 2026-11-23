# Estado do projeto

Atualizado: 23/11/2026. Fase: fundação de estado SQLite v1–v3 concluída; pronto para o marco vertical de produto.

Retomada: [AGENTS.md](AGENTS.md), este estado e [MEMORY.md](MEMORY.md). Fonte normativa: [baseline rastreado](Universal-Dev-Harness-v2_5-BASELINE-NORMATIVO-CONSOLIDADO-FINAL.md) (restaurado no disco) + ratificação delegada em [openspec/project.md](openspec/project.md); [v2.5.1](UDH-v2.5-ARQUITETURA-CONSOLIDADA-v2.5.1.md) é nota editorial derivada.

## Funciona

- Core modular SQLite v1–v3 (`0bd458d`): `init`/`create`/`show`/`policy` (RO), migrações explícitas v1→v2 e v2→v3, `block` (`PENDING/0→BLOCKED/1`) e `unblock` (`BLOCKED/1→PENDING/2`) atômicos com idempotência, replay durável e replay histórico preservado.
- Verificação completa no candidato `0bd458d`: **98/98** complementares PASS; **50/50** AT protegidos selados PASS em Podman rootless (`v6` 16/16 com 439 chamadas, `v5` 13/13 com 171, `v2` 11/11 com 55, `v1` 10/10 com 49; zero erros/infra) + revisão independente R2/F-02 PASS. Resumo compacto: [unblock-verification-summary.json](openspec/changes/slice1-unblock-run/unblock-verification-summary.json).
- Governança alinhada em [openspec/project.md](openspec/project.md): B1–B4, Emenda ADR-018 A e congelamento arquitetural ratificados no desenho por delegação do usuário; `bootstrap/slice1/` congelado (sem selo v7); política contra JSONs crus gigantes no Git; `origin` configurado no GitHub (`kuryok/Universal-Dev-Harness`).

## Pendente

- **Produto ponta a ponta:** executor confiável de produto, Runtime Manager rootless, adaptadores reais de agentes (Pi 1.2.0 primeiro, depois OpenCode/Codex), gateway de inferência com contexto real, `DISCOVER`/`SPEC`/`PLAN`, perfil `artifact`/preview ("Ver o app"), interface guiada, Memorix, Judge consultivo e instalador.
- **Segurança e POCs:** `POC-01`–`POC-04` e `DOC`/`SKILL`/`DISC`/`TICKET` seguem `NOT_RUN`. Store local do bootstrap não substitui store protegida oficial (ADR-016). Coletas I/T não reexecutadas (bootstrap intocado).
- **Dívida técnica v4:** em SQLite v2/v3, `_validate_schema()` executa checagem global (`O(N)`) exigida pelos AT congelados v5/v6; desacoplar na v4 para leitura `O(1)` e ledger unificado de transições.

## Próxima tarefa

Especificar (em change OpenSpec aprovado) o **marco vertical de produto**: (1) ledger SQLite v4 para ciclo completo (`PENDING ↔ IN_PROGRESS ↔ BLOCKED → DONE`) com leitura `O(1)`; (2) Runtime Manager rootless + adaptador Pi 1.2.0 + gateway real para executar 1 ticket ponta a ponta e rodar `POC-01`/`02`/`04`.

## Verificação

- `PYTHONDONTWRITEBYTECODE=1 TMPDIR="$PWD/bootstrap/slice1" python3 -m unittest discover -s tests` (98 PASS).
- [README](README.md) para entrada. Não abrir credenciais, não limpar `spike/slice0/workspace/` sem autorização nem declarar entrega/segurança sem POCs reais.
