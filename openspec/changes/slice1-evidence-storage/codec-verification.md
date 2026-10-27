# Primeiro incremento — codec e objetos atômicos

> **Histórico — primeiro incremento do codec, anterior à integração dos gates, em 22/10/2026.** Declarações de implementação e contagens abaixo valem para aquele incremento. A referência posterior de implementação do transporte é [integration-verification.md](integration-verification.md); a [auditoria de 27/10](report-audit.md) registra o que foi conferido novamente. Os resultados históricos foram preservados.

Autorização humana: implementar o codec e validar integridade, limites e publicação atômica antes de integrar aos gates, em22/10/2026. Este resultado cobre somente esse incremento do contrato aprovado. Não publica selo7, não altera Core e não integra nenhuma decisão de aceite ao armazenamento novo.

## Implementado

`bootstrap/slice1/evidence_store.py`, biblioteca padrão, com:

- encode/decode JSON UTF-8 canônico e gzip determinístico no runtime atual; referências e envelopes exatos, hashes comprimido/lógico e tamanhos positivos estritos. Tipos audit/transcript/report/regression/review; chaves JSON string, números finitos, profundidade JSON64. Dados literais preservados.
- Decode limitado pela referência e pelo teto16MiB, CRC/eof e um único membro gzip; recusa truncamento, dados residuais, concatenação, JSON duplicado/não canônico, tipos ou hashes divergentes. O encoder usa gzip nível9, filename vazio e mtime0. O decoder aceita membro gzip válido medido por hash, sem usar metadados de gzip como autoridade de origem.
- EvidenceStore exige raiz absoluta preexistente dentro do projeto; percorre ancestrais por descritores O_DIRECTORY/O_NOFOLLOW, abre objetos O_NOFOLLOW/O_NONBLOCK e valida fstat regular antes da leitura. Consome os bytes desse descritor, sem reabrir o caminho para decodificar.
- put valida os vínculos e todo fechamento proposto antes de publicar. Escreve temporário exclusivo privado, flush/fsync do arquivo, os.link exclusivo para o nome final derivado do hash e fsync do diretório. Não sobrescreve nem remove objeto final publicado. Existente só é reutilizado se seus bytes forem exatamente os esperados. Temporário próprio é removido em finally; interrupção abrupta pode deixá-lo órfão.
- walk valida tipos de vínculos, referências repetidas consistentes, ciclos e limites256objetos/profundidade8 (raiz0). Orçamento agregado16MiB comprimidos/64MiB JSON, contando cada hash uma vez; individual4/16MiB. Altura dos descendentes é guardada como metadado para conferir também caminhos compartilhados mais profundos, sem cache dos payloads. validate só retorna os contadores depois de esgotar o fechamento. Abandonar walk antes do fim não comprova fechamento.

Formato dos vínculos neste incremento: audit/lista de objetos e transcript/string são folhas; report/objeto exige audit+transcript e aceita regression opcional; regression/objeto exige nomes [a-z][a-z0-9_]{0,63} apontando a reports; review/objeto exige baseline(report)+regression(regression). Isso valida estrutura, não semântica dos resultados, autorizações ou cobertura dos AT; essas decisões pertencem aos gates futuros.

`tests/test_evidence_storage.py` foi escrito em contexto separado do implementador e contém28 complementares. O único ajuste em teste anterior foi mudar a saída de instrumentação de `evidence/block-instrumentation.json` para seu diretório temporário próprio, conforme o contrato; asserções preservadas, relatório histórico intacto.

## Verificação nesta sessão

Comando: `PYTHONDONTWRITEBYTECODE=1 TMPDIR="$PWD/bootstrap/slice1" python3 -m unittest discover -s tests -v`.

Rodadas:25 novos complementares PASS;64 complementares PASS (39 anteriores+25 novos); após os testes adicionais, rodada final67 PASS (39 anteriores+28 novos), zero failures/errors/skips. Sem skips/xfail ou testes de aceite alterados. Os limites menores usados em alguns casos são patches de constantes só no processo de teste, para exercitar exatamente as fronteiras sem alocar grandes volumes.

A interrupção real usa filho POSIX próprio, wrapper de teste em os.link e Pipe: pai confirma marker antes/depois do link e termina o filho bloqueado. Antes do link não há nome final; depois há objeto final íntegro e legível, sem retorno de sucesso do filho. Pending é namespace temporário, não referência válida. Falhas sintéticas fsync/link cobrem erro antes da publicação e erro depois do link: a chamada falha, preservando ausência ou objeto completo órfão. Convergência de escritores é testada com threads reais.

Limites: encerramento de processo instrumentado não prova queda de energia/hardware nem RSS máximo; testes de concorrência não são prova formal. Num ensaio intermediário do teste novo, Pipe.poll foi interpretado incorretamente como mensagem embora também sinalize EOF; o autor corrigiu a asserção para recv/EOFError após morte, preservando a exigência de ausência de sucesso. Não houve flexibilização de teste para acomodar erro do codec.

## Pendências deliberadas

Gates, CLIs/destinos de coleta, manifests/resumos, importação/exportação offline, proteção de mounts do coletor novo, AT independentes de transporte e selo7: não implementados/não executados neste incremento. AT v1/v2/v5/v6 e I1–I7/T1–T8 não foram reexecutados aqui; a rodada de complementares inclui o teste sintético SQLite já existente. Nenhuma evidência histórica foi regenerada.

Harness fast/full e CLI OpenSpec: não executados, ausentes. Não há dependência nova, download, rede, credencial ou mudança fora do projeto. O orçamento lógico não é limite exato de memória do parser. Erros de API/memória devem continuar sendo tratados como infraestrutura pelo chamador confiável antes de publicar uma raiz de aceite. Um hash ou objeto individual válido não autoriza nem certifica resultado.

Próximo incremento: integrar resolução de referências aos gates e novas rotas de saída, mantendo API default6 e CLIs conforme contrato; somente depois regressões completas, exportação/fechamento offline, autoria/revisão de AT e selo7. Core permanece v1/v2 sem unblock e sem execução de agentes.

Resultado final:67 testes PASS em2,668s, zero failures/errors/skips. [Revisão independente dos bytes](codec-review.json): PASS, sem achados pendentes; o revisor executou separadamente os28 novos testes. Sintaxe dos arquivos alterados, links locais, limites STATE/MEMORY, hashes do parecer e git diff --check PASS. Core, selos1–6, AT e evidências históricas permanecem sem alteração. Selo6 verificado também pelo verificador confiável nesta sessão.
