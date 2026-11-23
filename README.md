# Universal Dev Harness — UDH

Projeto local-first para conduzir uma pessoa que não sabe programar de uma ideia a software funcional, testado e utilizável. Estamos no fechamento da fundação de estado da Slice 1 (Core SQLite v1–v3, política somente leitura, bloqueio e desbloqueio administrativos); a execução real de agentes, preview, interface guiada e entrega ponta a ponta são o próximo marco vertical.

- [Baseline normativo](Universal-Dev-Harness-v2_5-BASELINE-NORMATIVO-CONSOLIDADO-FINAL.md): propósito (Seção 0), ADRs e invariantes. A [nota v2.5.1](UDH-v2.5-ARQUITETURA-CONSOLIDADA-v2.5.1.md) é derivada para consulta.
- [Estado atual](STATE.md), [memória curta](MEMORY.md) e [instruções de trabalho](AGENTS.md).
- [Processo documental adaptado de OpenSpec e decisões ratificadas](openspec/project.md): requisitos dos incrementos em changes aprovados, sem CLI/validação canônica instalada.

## Verificar neste ambiente de desenvolvimento

```bash
PYTHONDONTWRITEBYTECODE=1 TMPDIR="$PWD/bootstrap/slice1" python3 -m unittest discover -s tests
```

Os testes usam Python 3 (stdlib-first) e, nos cenários de runtime isolado, Podman com a imagem local preparada no bootstrap. Não é um instalador nem requer credenciais de LLM para executar os testes complementares.

POCs de segurança (`POC-01`–`POC-04`) e store oficial fora do domínio gravável do host continuam pendentes (`NOT_RUN`). Nenhum PASS do bootstrap representa produto completo ou autorização de entrega.
