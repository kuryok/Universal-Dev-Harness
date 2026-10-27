# Universal Dev Harness — UDH

Projeto local-first para conduzir uma pessoa de uma ideia a software funcional e verificável. Estamos na fundação da Slice 1: Core SQLite, bloqueio administrativo e infraestrutura de verificação; o ciclo completo com agentes, interface guiada e entrega ainda não está implementado.

- [Baseline normativo](Universal-Dev-Harness-v2_5-BASELINE-NORMATIVO-CONSOLIDADO-FINAL.md): propósito e invariantes. A nota v2.5.1 é apenas derivada.
- [Estado atual](STATE.md), [memória curta](MEMORY.md) e [instruções de trabalho](AGENTS.md).
- [Processo documental adaptado de OpenSpec](openspec/project.md): requisitos dos incrementos em changes aprovados, sem CLI/validação canônica instalada.

## Verificar neste ambiente de desenvolvimento

```bash
PYTHONDONTWRITEBYTECODE=1 TMPDIR="$PWD/bootstrap/slice1" python3 -m unittest discover -s tests
```

Os testes usam Python e, nos cenários de runtime, Podman e a imagem local preparada no bootstrap. Não é um instalador nem requer credenciais de LLM para executar os complementares. Nenhuma dependência foi adicionada para este alinhamento.

POCs e segurança oficial continuam pendentes. Evidências de bootstrap não protegem contra proprietário com escrita no host. Nenhum PASS isolado representa produto completo ou autorização de entrega.
