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
