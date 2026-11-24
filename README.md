# Universal Dev Harness — UDH

Projeto local-first para conduzir uma pessoa que não sabe programar de uma ideia a software funcional, testado e utilizável. O Core (100% Python 3 stdlib) implementa o ledger SQLite v1–v4, Runtime Manager rootless com imagens OCI dedicadas por agente fixadas por digest (Pi 1.2.0 e OpenCode 1.18.35), Gateway de inferência stdlib endurecido com allowlist de ferramentas, módulo de handoff F-03 com `git_dir` externo, seleção determinística ADR-026 e Executor Confiável F-04, com todas as **4 POCs fundacionais (`POC-01`–`POC-04`) verificadas (`PASS`)** em Linux x86_64 rootless.

- [Baseline normativo](Universal-Dev-Harness-v2_5-BASELINE-NORMATIVO-CONSOLIDADO-FINAL.md): propósito (Seção 0), ADRs e invariantes. A [nota v2.5.1](UDH-v2.5-ARQUITETURA-CONSOLIDADA-v2.5.1.md) é derivada para consulta.
- [Estado atual](STATE.md), [memória curta](MEMORY.md) e [instruções de trabalho](AGENTS.md).
- [Processo documental adaptado de OpenSpec e decisões ratificadas](openspec/project.md): requisitos dos incrementos em changes aprovados, sem CLI/validação canônica instalada.

## Verificar neste ambiente de desenvolvimento

```bash
PYTHONDONTWRITEBYTECODE=1 TMPDIR="$PWD/bootstrap/slice1" python3 -m unittest discover -s tests
python3 -m udh doctor --conformance
python3 -m udh.poc
```

Os testes usam Python 3 (stdlib-only), Podman rootless e as imagens OCI locais construídas offline por `python3 runtime/build_image.py --target all` (digests fixados em `runtime/image-manifest.json` e `runtime/opencode-image-manifest.json`). Todo o fluxo padrão roda 100% offline com o backend `stub` do Gateway, sem credenciais reais de LLM.

Preview web local isolado (`B3.6`), interface guiada (`B1.7`), `DISCOVER`/`SPEC`/`PLAN`, terceiro agente (Codex), WSL2 e entrega ponta a ponta para iniciantes continuam pendentes (`NOT_RUN`); o projeto ainda não é um MVP liberável.
