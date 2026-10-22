# Resultado — gates referenciados e novas saídas

Continuação autorizada pelo usuário após codec9747b6f. Contrato/refinamento em [integration.md](integration.md). Produto/Core sem alteração; geração7 ainda não publicável.

## Alterações

- evidence_store: publicação comum exclusiva para objetos/resumos; destinos novos sob runs/UUID/summary.json; leitura por descritor, fechamento e comparação entre resumo/metadados. Hashes/specs/contagens aparecem no resumo; ele não basta para aceitar. Os layouts flat/integração/fronteira separam auditoria/transcrição sem truncar saídas já capturadas pelo runtime.
- block_seal: API default6 deriva bytes do snapshot congelado; DRAFT7 opt-in fixa fontes atuais e AT funcionais preservados. Gate de baseline referenciado verifica identidade, candidato, cobertura16/13-3, auditoria, regressões e provas I/T. Publicar7 é recusado, mesmo com review fornecida, até o preparo seguinte.
- integrate_block: chamada antiga --baseline recusa exit2 antes de escrever; --baseline --generation7 produz coleta nova. AT do rascunho7 são comparados byte a byte com executor6, que é usado explicitamente com seu pin. O relatório declara as duas identidades. Sem regressões novas fornecidas, resultado geral INCOMPLETE e exit1 são deliberados, mesmo com discriminação funcional PASS.
- integrate_acceptance/check_boundary: build_report sem ler/copiar históricos; saída append-only e destino validado antes de coletar; reutilização/verificação do selo1 e revisão própria de14 componentes antes/depois da coleta. Nenhuma exclusão da preservação histórica T8.

## Testes e revisão

Rodadas desta sessão:77 complementares PASS, depois46 de codec/coleta/gates PASS e85 complementares PASS (39 anteriores+46). Os novos complementares foram escritos em contexto separado do implementador. Oito métodos de gate/API/CLI incluem múltiplas mutações negativas, cobertura truncada, resumo PASS com provas ausentes, candidato/pins/hash/métodos divergentes, bool em contagens, exit124/125, remoção incompleta e compatibilidade da API6.

Falhas encontradas antes de concluir, preservadas no registro do desenvolvimento: layouts mistos podiam descartar captura; schema bool/float passava por igualdade com1; regressões externas não conferiam todas as identidades/auditoria; resumos omitiam metadados; I/T só com flags PASS podia satisfazer o ramo sem provas. Correções de causa foram verificadas por negativas. O nome de vínculo I/T foi padronizado como i1_i7_t1_t8 no formato novo, pois o codec exige identificadores minúsculos; os formatos3–6 permanecem intactos.

Fixtures de gate são explicitamente sintéticas: usam auditorias históricas lidas e a coleta I/T nova como dados; um complementar sintético não é anunciado como regressão corrente. O contexto do gate fixa os14 hashes daquela revisão preservada, independentemente de fontes futuras; testes separados verificam criação real de DRAFT7. Não alteram origem nem provas para fazer o gate passar.

[Revisão I/T](infrastructure-review.json) fixa14 hashes antes da execução real. [Revisão independente de fontes/testes](integration-review.json) separa o alcance deste incremento de exportação/AT/publicação. Nenhum parecer LLM concede aceite do produto.

## Coletas reais novas

- I1–I7 PASS e T1–T8 PASS: [integração](../../../bootstrap/slice1/evidence/runs/0c35dbbf-bc96-490b-99cc-d4029a081c32/summary.json), [fronteira](../../../bootstrap/slice1/evidence/runs/702dc883-555a-4c64-80c4-d255e189dcc3/summary.json). O fechamento da integração compartilha a fronteira:7 objetos,19.857 bytes comprimidos/232.698 JSON. Resumos1.432/770 bytes, sem cópia de auditorias inline.
- Primeira coleta16 teve erro de infraestrutura na remoção de um contêiner e foi registrada como FAIL/INCOMPLETE, nunca sucesso funcional: [tentativa preservada](../../../bootstrap/slice1/evidence/runs/62ce3385-9f17-49ce-a830-1bfad64f94ef/summary.json). O resultado de block foi exit0, mas removed=false, portanto a recusa foi correta.
- [Limpeza confiável](../../../bootstrap/slice1/evidence/runs/69769f0d-d842-4f30-bca3-0394d71da8dc/summary.json): somente contêiner udh-operator-afbe78c3e63540ffa226ab4914ed8257, exists0→rm0→exists1. Sem alterar timeouts ou AT.
- [Repetição serial intermediária](../../../bootstrap/slice1/evidence/runs/bca301e7-3ab8-42e1-9394-c801f28325b2/summary.json): discriminação PASS16, geral INCOMPLETE por regressões/AT de transporte pendentes. Foi coletada antes das últimas correções do gate; não substitui resultado dos fontes finais.

[Coleta final serial](../../../bootstrap/slice1/evidence/runs/78a7d7e9-0fb5-4170-b8e4-827f6e497fbd/summary.json):16 métodos,13 falhas funcionais esperadas e3 PASS,150 chamadas auditadas, zero infraestrutura/errors/skips. baseline_discrimination=PASS; resultado geral INCOMPLETE/exit1 permanece por R1 corrente/AT/exportação pendentes. Seus26 hashes de fontes e4 specs foram comparados com os arquivos finais, sem divergência. Fechamento final3 objetos/23.227 bytes gzip e resumo1.989 bytes. Nenhum relatório global histórico foi reescrito, nem falha apagada para reduzir volume.

## Reprodução e pendências

`PYTHONDONTWRITEBYTECODE=1 TMPDIR="$PWD/bootstrap/slice1" python3 -m unittest discover -s tests -v`.

Coleta funcional nova: `PYTHONDONTWRITEBYTECODE=1 TMPDIR="$PWD/bootstrap/slice1" python3 bootstrap/slice1/integrate_block.py --baseline --generation 7`. Sem regressões completas vinculadas, espera-se exit1/INCOMPLETE; consultar baseline_discrimination e auditoria, não presumir falha funcional do Core nem aceite.

I/T novo: `PYTHONDONTWRITEBYTECODE=1 TMPDIR="$PWD/bootstrap/slice1" python3 bootstrap/slice1/integrate_acceptance.py`. Requer revisão atual de componentes; execuções são novas e não substituem evidência anterior.

Exportação/fechamento offline, AT independentes de transporte, regressões correntes v1/v2/v5 completas e publicação7: não executados neste incremento. O gate é exercitado com fechamento sintético completo, que não substitui essas coletas. Harness fast/full e CLI OpenSpec ausentes: não executados. Nenhuma dependência, rede, imagem ou credencial nova.

Próximo passo: implementar/testar fechamento e exportação offline; preparar AT independentes de transporte e executar as regressões completas antes de publicar7. Só depois retomar migração/leitura v3 e desbloqueio; execução de agentes continua indisponível.

Volume total das seis coletas novas, incluindo tentativa inválida e limpeza:18 objetos únicos/90.526 bytes gzip e8.681 bytes de resumos. Nenhum relatório grande foi copiado para a revisão ou manifesto. Isso mede apenas este incremento, não a economia total do Git.

Fechamento da sessão:85 complementares PASS em8,653s nos bytes finais; o revisor executou46 testes de codec/coleta/gate separadamente. Hashes das revisões, links locais, limites STATE/MEMORY, sintaxe e diff --check PASS. Comparação com HEAD confirmou Core, AT, selos1–6 e todos os JSON históricos intactos. Mudanças preexistentes no baseline/arquitetura e __pycache__ permanecem fora do commit.
