# Universal Dev Harness — UDH

Projeto local-first para conduzir uma pessoa que não sabe programar de uma ideia a software funcional, testado e utilizável. O Core (100% Python 3 stdlib, somente arquivos `.py` sob `udh/`) implementa o ledger SQLite v1–v4, Runtime Manager rootless com 3 imagens OCI dedicadas fixadas por digest (Pi 1.2.0, OpenCode 1.18.35 e `artifact-only` sem agentes), Gateway de inferência stdlib endurecido, handoff F-03 com `git_dir` externo, seleção ADR-026, Executor Confiável F-04, smoke test `B3.4` com contrato de entrega `ADR-028`, preview web local isolado (`B3.6`) e interface guiada mínima local (`B1.7`), com **4/4 POCs fundacionais (`POC-01`–`POC-04`)** e **`B3.1`/`B3.4`/`B3.6`/`B1.7`** verificados (`PASS`) em Linux x86_64 rootless offline.

- [Baseline normativo](Universal-Dev-Harness-v2_5-BASELINE-NORMATIVO-CONSOLIDADO-FINAL.md): propósito (Seção 0), ADRs e invariantes. A [nota v2.5.1](UDH-v2.5-ARQUITETURA-CONSOLIDADA-v2.5.1.md) é derivada para consulta.
- [Estado atual](STATE.md), [memória curta](MEMORY.md) e [instruções de trabalho](AGENTS.md).
- [Processo documental adaptado de OpenSpec e decisões ratificadas](openspec/project.md): requisitos em changes aprovados. A CLI 2.1.1 foi exercitada com snapshots sintéticos; isso não valida automaticamente os changes existentes.

## Onde fica cada coisa

| Se você quer mexer em… | Comece por… |
|---|---|
| Painel local e preview | [`udh/web.py`](udh/web.py), [`udh/web_ui.py`](udh/web_ui.py) e [`udh/preview.py`](udh/preview.py) |
| Estado, política e persistência | [`udh/service.py`](udh/service.py), [`udh/domain.py`](udh/domain.py), [`udh/policy.py`](udh/policy.py) e [`udh/store.py`](udh/store.py) |
| Descoberta, especificações e tickets | [`udh/discovery.py`](udh/discovery.py), [`udh/discovery_session.py`](udh/discovery_session.py), [`udh/spec_handoff.py`](udh/spec_handoff.py), [`udh/ticket_plan.py`](udh/ticket_plan.py) e [`udh/ticket_store.py`](udh/ticket_store.py) |
| Agentes, contêineres e verificação | [`udh/adapters/`](udh/adapters/), [`udh/gateway.py`](udh/gateway.py), [`udh/runtime.py`](udh/runtime.py) e [`udh/executor.py`](udh/executor.py). As imagens e seus scripts ficam em [`runtime/`](runtime/). |
| Testes e requisitos | [`tests/`](tests/) para testes complementares, [`openspec/changes/`](openspec/changes/) para requisitos e [`bootstrap/slice1/`](bootstrap/slice1/) para o recorte de aceite congelado. |
| Histórico da Slice 0 | [`spike/`](spike/) e [`relatorio.md`](relatorio.md) |

## Verificar neste ambiente de desenvolvimento

```bash
PYTHONDONTWRITEBYTECODE=1 TMPDIR="$PWD/bootstrap/slice1" python3 -m unittest discover -s tests
PYTHONDONTWRITEBYTECODE=1 python3 -m udh doctor --conformance
PYTHONDONTWRITEBYTECODE=1 python3 -m udh.poc
PYTHONDONTWRITEBYTECODE=1 python3 -m udh.web --db /tmp/udh-demo.db --store /tmp/udh-demo-store
```

Os testes usam Python 3 (stdlib-only), Podman rootless e as 3 imagens OCI locais construídas offline por `python3 runtime/build_image.py --target all` (digests fixados em `runtime/image-manifest.json`, `runtime/opencode-image-manifest.json` e `runtime/artifact-image-manifest.json`). Todo o fluxo padrão roda 100% offline com o backend `stub` do Gateway, sem credenciais reais de LLM.

`DISCOVER`/`SPEC`/`PLAN`, multi-ticket DAG (`TICKET-01/02`), perfil `provision` (`B2.4`), execução persistente (`B3.8`), exportação (`B3.9`), `DOC`/`SKILL`/`UX-04`, terceiro agente (Codex), Chromium/Chrome e WSL2 continuam pendentes (`NOT_RUN`); o projeto ainda não é um MVP liberável completo.

Na branch do [Change 5](openspec/changes/discover-plan-tickets/tasks.md), o plano persistido, o início condicionado ao aceite das predecessoras, as rodadas de descoberta com confirmação humana e a validação de snapshots com OpenSpec 2.1.1 passaram nos testes complementares. A [skill de descoberta adaptada](openspec/changes/discover-plan-tickets/skills/grill-with-docs-udh/SKILL.md) está preparada; o encaminhamento para SPEC e a troca de agente sobre esse plano ainda estão pendentes.
