# UDH — retomada curta

Atualizado: 23/11/2026. Ler [AGENTS.md](AGENTS.md), [STATE.md](STATE.md) e conferir Git. Memória não aprova requisitos nem substitui evidências.

## Estado confirmado
- Baseline normativo restaurado no disco (idêntico ao `HEAD`). Por delegação do usuário (23/11/2026), B1–B4, Emenda ADR-018 A e congelamento arquitetural foram ratificados no desenho em [openspec/project.md](openspec/project.md).
- Incremento `slice1-unblock-run` concluído e verificado no commit `0bd458d`: **98/98** complementares PASS, **50/50** AT protegidos selados PASS (`v6` 16/16, `v5` 13/13, `v2` 11/11, `v1` 10/10; 714 chamadas auditadas, 0 erros/infra) e revisão independente R2/F-02 PASS ([resumo](openspec/changes/slice1-unblock-run/unblock-verification-summary.json)).
- `bootstrap/slice1/` congelado (sem selo v7 ou novos gates); política de evidências limita o Git a resumos compactos (`<= 32 KiB`). Remote `origin` configurado em `kuryok/Universal-Dev-Harness`.

## Próximo passo
- Abrir change OpenSpec para o **marco vertical de produto**: ledger de transições SQLite v4 com leitura `O(1)` (desacoplando `quick_check`/scan global de `show`/`policy`) + Runtime Manager rootless + primeiro adaptador real (Pi 1.2.0) + gateway com contexto real, preparando a execução de `POC-01`/`02`/`04`.

## Cuidados
- `POC-01`–`POC-04`, `DOC`/`SKILL`/`DISC`/`TICKET` continuam `NOT_RUN`; bootstrap não certifica segurança oficial nem store fora do host.
- Não alterar selos `v1`–`v6`, AT protegidos nem evidências históricas; não commitar JSONs crus de megabytes.
- Não abrir credenciais nem limpar `spike/slice0/workspace/` sem autorização. Preferências: sem skills globais; commits em português quando autorizados; informar próximo passo.
