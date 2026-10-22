# Verificação documental e medição local

Data: 22/10/2026. Base: `e6015a6`. Este recorte autorizado produz especificação; armazenamento novo, compressão em disco, gates novos e selo v7 não foram implementados.

## Medição somente leitura

Experimento com biblioteca padrão, sem gravar objetos: ler JSON existente, serializar com json.dumps(sort_keys=True, ensure_ascii=False, separators=(',',':'), allow_nan=False), UTF-8 sem newline; gzip.compress(..., compresslevel=9, mtime=0) em memória. Os bytes comprimidos não foram publicados nem referenciados como evidência de execução nova. Resultados medidos nesta sessão:

| Arquivo na base | Bytes atuais | JSON canônico | Gzip em memória |
| --- | ---: | ---: | ---: |
| evidence/unblock-baseline.json |663.092|444.054|22.057|
| openspec/changes/slice1-unblock-run/preparation-review.json |751.568|447.985|24.172|
| seals/v6/manifest.json |450.217|450.216|25.105|
| evidence/unblock-regression-integration.json |2.408.343|1.362.929|87.586|

Caminhos abreviados evidence/seals são relativos a bootstrap/slice1. Os números não são economia implementada: incluem payloads duplicados da representação antiga e não representam o grafo final. gzip.compress do experimento não é promessa de identidade binária com o futuro codec GzipFile entre versões Python/zlib.

A leitura dos fontes confirmou: block_seal.validate_baseline consome relatório completo em review.baseline_gate.report; verify_seal vincula a cobertura aos AT selados; integrate_block insere auditoria/transcrição completas e external_evidence. integrate_acceptance/check_boundary e o complementar de instrumentação escrevem destinos históricos. O desenho preserva as validações semânticas e propõe modificar somente os novos caminhos de coleta.

## Checks desta especificação

- Verificar diff sem erros e links locais do change/STATE/MEMORY, limites de linhas/palavras e correspondência literal de Core, selos1–6 e evidências históricas à base Git.
- PASS nesta sessão: links locais do change/STATE/MEMORY, limites de linhas/palavras e git diff --check. git diff HEAD confirmou Core, bootstrap inteiro (incluindo selos/evidências), testes e change de unblock sem alterações.
- [Revisão documental independente](doc-review.json): DOCUMENTARY_PASS, dois achados resolvidos (identidade nova da rota I/T e API opt-in geração7), sem achados pendentes. SHA256 do contrato: `4ed30376714ed168efc6bcacf0985eb5639f9e12176e356a2c20c57b4171fa35`. Não equivale a aprovação humana ou validação executável.
- Testes funcionais, complementares39, AT e interrupções: **não executados** nesta tarefa documental; nenhum código foi alterado. Os resultados anteriores permanecem anteriores. Não regenerar relatórios para sustentar mudança só documental.
- Harness fast/full e CLI OpenSpec: **não executados**, ausentes no projeto/ambiente. O contrato é mantido documentalmente em OpenSpec.

## Limites e próximo passo

Gzip/hash não tornam dado confiável. A implementação futura precisa demonstrar roundtrip, integridade, limites, fechamento offline, concorrência, interrupção e as regressões sem alterar arquivos históricos. Os cenários E1–E9 são requisitos propostos, não testes que já passaram.

Próximo passo: revisão/aprovação do desenho e de seus arquivos protegidos nomeados; então implementação incremental do armazenamento e transporte, seguida de selo v7. Só depois retomar migração/leitura v3, antes do desbloqueio. Sem mudança na disponibilidade de execução de agentes.
