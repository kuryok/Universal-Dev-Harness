# Verificação do recorte de policy

## Preparo de infraestrutura — 21/10/2026

Autorização humana: contrato de policy ("Aprovo!") e preparo independente AT/selo v2 ("aprovado"). Nenhuma dependência nova ou alteração de controladores/AT v1.

- AT independentes: autor `policy_at_author`, onze métodos, oito NEW_FAILURE e três EXISTING_PASS; P7 permanece revisão independente.
- Complementares: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v` — 14 PASS antes do produto.
- Baseline: `PYTHONDONTWRITEBYTECODE=1 python3 bootstrap/slice1/integrate_policy.py --candidate b26fd96 --baseline` — PASS, onze métodos e zero erros de infraestrutura. As oito falhas são a ausência esperada de policy no commit fixo, não aceite do produto. Relatório: `bootstrap/slice1/evidence/policy-baseline.json`.
- Primeira tentativa do coordenador classificou IDs/subtests incorretamente e resultou FAIL; os AT não foram alterados. Corrigido o coletor, reexecutado após revisão e alterações de vínculo até o relatório final corresponder aos hashes atuais.
- Revisão encontrou vínculo insuficiente baseline/publicação; corrigido com commit fixo, identidade do DRAFT, hashes exatos, cobertura, auditoria, resultados e relatório canônico incluídos na revisão. Artefato de baseline também é conferido no preparo da publicação.
- Regressão: `PYTHONDONTWRITEBYTECODE=1 python3 bootstrap/slice1/integrate_acceptance.py` — I1–I7 PASS, incluindo T1–T8. Dez AT Core v1 contra b26fd96 também PASS via `integrate_policy.run_candidate(..., legacy=True)`; evidência `bootstrap/slice1/evidence/policy-v1-regression.json`.
- Harness fast/full e CLI OpenSpec: não executados, ausentes no projeto. Nenhuma alegação de aceite oficial ou proteção contra o operador do host.

A publicação e o produto serão registrados abaixo apenas após execução efetiva.

Infraestrutura revisada por `policy_infra_review`: PASS, sem achados pendentes, hashes e baseline conferidos. Selo v2 publicado exclusivamente e repetição idêntica verificada: `sha256:438ce43f70e0137115f2c30920623eabf5bfd042035958951fdaafa151e41f3f`. Arquivos da v1 preservados. A publicação do conjunto de testes não significa que policy foi implementada ou passou nesses AT.

## Produto — resultado final

Candidato `bfb2cbbd5644a08ae868c326a399283118b57a6e`: consulta implementada em `udh/policy.py`, serviço e CLI; domínio/SQLite preservados. O Core totaliza seis módulos pequenos. READ_RUN permite apenas metadados; execução, transições, aceite e entrega permanecem negados.

- 16 complementares PASS na execução final.
- 11 AT policy PASS pelo selo v2 publicado, 55 chamadas isoladas; nenhum erro/falha/exclusão.
- 10 AT Core v1 PASS contra o mesmo candidato, 49 chamadas isoladas; nenhum erro/falha/exclusão.
- I1–I7/T1–T8 reexecutados após implementação: PASS.
- Revisão independente P7 PASS em `product-review.json`; hashes dos seis módulos coincidem com os dois snapshots testados. Selos v1/v2 conferidos; fontes v1 intactas.
- Resumo verificável em `verification.json`; relatórios detalhados em `bootstrap/slice1/evidence/policy-candidate.json` e `policy-v1-regression.json`.

Não implementado: transições persistidas, migração SQLite, execução de agentes e gates de aceite/entrega. Não há ACCEPTED/DELIVERED oficial nem Slice 1 completa. Suposição: contexto administrativo local existente de show, sem autenticação nova. Harness fast/full e CLI OpenSpec não executados porque ausentes.

### Reprodução

Complementares: `PYTHONDONTWRITEBYTECODE=1 TMPDIR="$PWD/bootstrap/slice1" python3 -m unittest discover -s tests -v`.

Consulta: `python3 -m udh policy --db CAMINHO --run-id UUID --action READ_RUN`. Ações negadas retornam JSON com exit 3.

AT isolados no selo publicado (a partir da raiz do projeto):

```python
from pathlib import Path
import sys
sys.path.insert(0, str(Path('bootstrap/slice1').resolve()))
from integrate_policy import run_candidate
from policy_seal import PREVIOUS
ref = 'bfb2cbbd5644a08ae868c326a399283118b57a6e'
policy = run_candidate(Path('bootstrap/slice1/seals/v2').resolve(),
    'sha256:438ce43f70e0137115f2c30920623eabf5bfd042035958951fdaafa151e41f3f', ref)
core = run_candidate(Path('bootstrap/slice1/seals/v1').resolve(), PREVIOUS, ref, legacy=True)
for result in (policy, core):
    assert not result['errors'] and not result['failures'] and not result['skipped']
    print(result['tests_run'], len(result['audit']))
```

Próximo recorte: especificar a primeira transição persistida, com migração versionada e autorização confiável, antes de integrar execução de agentes.
