# Universal Dev Harness — UDH

Projeto local-first para conduzir uma pessoa que não sabe programar de uma ideia a software funcional, testado e utilizável. A fundação operacional do Core está concluída e verificada (SQLite v1–v4 com ledger unificado de transições, leitura `O(1)` em v4, checkpoints F-03, política v2 e preflight `doctor`, 100% stdlib); o próximo marco (Change 2) é integrar Runtime Manager rootless, Gateway de inferência, adaptador Pi 1.2.0 e Executor Confiável F-04 para executar `POC-01`/`02`/`04`.

- [Baseline normativo](Universal-Dev-Harness-v2_5-BASELINE-NORMATIVO-CONSOLIDADO-FINAL.md): propósito (Seção 0), ADRs e invariantes. A [nota v2.5.1](UDH-v2.5-ARQUITETURA-CONSOLIDADA-v2.5.1.md) é derivada para consulta.
- [Estado atual](STATE.md), [memória curta](MEMORY.md) e [instruções de trabalho](AGENTS.md).
- [Processo documental adaptado de OpenSpec e decisões ratificadas](openspec/project.md): requisitos dos incrementos em changes aprovados, sem CLI/validação canônica instalada.

## Verificar neste ambiente de desenvolvimento

```bash
PYTHONDONTWRITEBYTECODE=1 TMPDIR="$PWD/bootstrap/slice1" python3 -m unittest discover -s tests
python3 -m udh doctor
```

Os testes usam Python 3 (stdlib-only) e, nos cenários de runtime isolado, Podman rootless com a imagem local preparada no bootstrap. Não é um instalador nem requer credenciais de LLM para executar os testes complementares ou o `doctor`.

POCs de segurança (`POC-01`–`POC-04`), execução real de agentes e entrega ponta a ponta continuam pendentes (`NOT_RUN`). Nenhum PASS do Core v4 ou do bootstrap representa produto completo ou certificação de segurança.
