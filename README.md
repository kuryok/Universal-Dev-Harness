# Universal Dev Harness — UDH

Projeto local-first para conduzir uma pessoa que não sabe programar de uma ideia a software funcional, testado e utilizável. O Core (100% Python 3 stdlib) implementa o ledger SQLite v1–v4, Runtime Manager rootless com imagem OCI dedicada por digest, Gateway de inferência stdlib endurecido, primeiro adaptador de agente (Pi 1.2.0) e Executor Confiável F-04, com `POC-01`, `POC-02` e `POC-04` verificadas (`PASS`) em Linux x86_64 rootless.

- [Baseline normativo](Universal-Dev-Harness-v2_5-BASELINE-NORMATIVO-CONSOLIDADO-FINAL.md): propósito (Seção 0), ADRs e invariantes. A [nota v2.5.1](UDH-v2.5-ARQUITETURA-CONSOLIDADA-v2.5.1.md) é derivada para consulta.
- [Estado atual](STATE.md), [memória curta](MEMORY.md) e [instruções de trabalho](AGENTS.md).
- [Processo documental adaptado de OpenSpec e decisões ratificadas](openspec/project.md): requisitos dos incrementos em changes aprovados, sem CLI/validação canônica instalada.

## Verificar neste ambiente de desenvolvimento

```bash
PYTHONDONTWRITEBYTECODE=1 TMPDIR="$PWD/bootstrap/slice1" python3 -m unittest discover -s tests
python3 -m udh doctor --conformance
python3 -m udh.poc
```

Os testes usam Python 3 (stdlib-only), Podman rootless e a imagem OCI dedicada local construída offline por `python3 runtime/build_image.py` (digest fixado em `runtime/image-manifest.json`). Todo o fluxo padrão roda 100% offline com o backend `stub` do Gateway, sem credenciais reais de LLM.

`POC-03` (handoff com 2º agente), `DISCOVER`/`SPEC`/`PLAN`, preview web (`B3.6`), interface guiada (`B1.7`), WSL2 e entrega ponta a ponta continuam pendentes (`NOT_RUN`); o projeto ainda não é um MVP liberável para iniciantes (conforme B1.6).
