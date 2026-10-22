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

## Revalidação do desenho solicitada pelo usuário

Revisão crítica posterior ao commit166b5a1 encontrou três lacunas reais: exportação não fechava inventário de código/specs; limites por objeto permitiam aproximadamente16 GiB lógicos no grafo; CLI antiga ainda podia escrever unblock-baseline.json. O contrato agora exige pacote completo/pin externo, limites agregados16 MiB comprimidos/64 MiB JSON (objeto4/16 MiB), validação sem reidratação multiplicativa e recusa exit2/zero escrita para a chamada antiga. O parecer inicial doc-review.json fica preservado com o hash da versão que revisou; não cobre automaticamente o texto corrigido.

Experimento adicional, somente memória e stdlib, sem modificar artefatos: separar audit/transcript do baseline em envelopes, guardar baseline uma vez, trocar sua inclusão na revisão por referência e a inclusão da revisão no manifesto por referência. GzipFile(filename='', mtime=0, compresslevel=9), JSON canônico definido na proposta. Reconstrução dos três documentos produziu igualdade JSON integral com os originais, incluindo auditoria/saídas/datas; não se alega identidade dos arquivos pretty-print nem reutilização de seus hashes para o formato novo.

Resultados: quatro objetos únicos somaram25.417 bytes gzip/448.955 bytes JSON; manifesto com referências2.488 bytes. Os três arquivos atuais somam1.864.877 bytes. Repetir armazenamento do mesmo relatório não aumentou o número de objetos. Maior objeto:16.044 bytes gzip/384.943 JSON, dentro dos orçamentos propostos. A amostra não cobre a regressão completa, não projeta economia de todo Git e não implementa o codec seguro: leitura gzip simplificada no ensaio não é prova de recusa de bombas, corrupção ou limites.

Validação adicional somente leitura: verify_seal confirmou pin v6; reconstruir body com status DRAFT/review nulo a partir do snapshot congelado reproduziu exatamente a identidade de rascunho registrada no baseline. Nenhum módulo udh foi importado no host. Essa prova sustenta o ramo compatível da API, mas não implementa o publicador v7.

Conclusão técnica: a mudança atende ao problema demonstrado de duplicação, mantendo dados completos e verificáveis. O custo de codec/referências/testes é uma decisão de infraestrutura, não uma dependência funcional de SQLite v3. Recorte mantido pequeno: utility/API tipada do bootstrap e testes, sem indexador, serviço, GC, CLI de exportação ou sistema de arquivos genérico. Implementação e provas E1–E9 permanecem pendentes.

[Revisão independente do desenho corrigido](design-validation.json): DOCUMENTARY_PASS, três achados resolvidos, sem pendências documentais; hash do texto final fixado no parecer. Checks finais desta sessão: links locais, limites STATE/MEMORY, diff --check e ausência de mudanças no Core/bootstrap/testes/evidências anteriores PASS. Os testes de produto/AT continuam não executados nesta tarefa somente documental.
