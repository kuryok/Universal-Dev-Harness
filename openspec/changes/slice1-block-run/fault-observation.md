# Observação de interrupções M3/B4 — contrato para aprovação

Estado: experimento instrumentado explicitamente aprovado pelo usuário em 21/10/2026 na pergunta de observação M3/B4. Derivada da recomendação do autor independente dos AT; não modifica o contrato funcional.

## Técnica e limite

Executar o mesmo snapshot identificado do Core em contêiner administrativo, mas com launcher de teste protegido e identificado separadamente. O launcher instrumentado usa subclass de sqlite3.Connection/trace_callback e barreira síncrona em IPC privado para avisar o controlador antes de cada statement de escrita observado; o override de commit sinaliza também depois de o COMMIT retornar e antes de devolver a resposta. Nenhum módulo de produto é alterado ou importado no host. Instrumentação não entra na CLI de produto.

O controlador só interrompe depois de receber e validar o marcador esperado e inspecionar o processo/contêiner correto. Nunca envia sinal ao host verificador. Barreiras e canais ficam exclusivamente no runtime administrativo; o adversário não recebe sockets, FDs, configuração ou mounts desses canais. O controlador encerra o contêiner/processo identificado e verifica remoção. Os leitores confiáveis reabrem o SQLite sintético e comparam estado/esquema/eventos/pedidos com o snapshot anterior ou o commit completo esperado.

Os pontos cobrem a ordem de DDL/cópia/rename/user_version da migração e as gravações evento/pedido/estado da transição. Um callback antes do próximo statement comprova apenas que o statement anterior retornou; o checkpoint pós-commit exige wrapper após retorno efetivo, não apenas trace do texto COMMIT. Cada ponto registra operação, ordinal, hash do launcher e Core, evidência do marcador e resultado recuperado. Ponto não atingido, callback ausente ou timeout não é PASS: caso funcional falha ou é INFRA_ERROR segundo causa demonstrada. Não contar a saída 137 sozinha como prova do sinal/ponto.

Limites explícitos: experimento instrumentado, não black-box puro; o wrapper e a execução diferem do launcher comum e são identificados. Não prova queda de energia, disco defeituoso ou que código malicioso não tente evitar o wrapper. Tentativa de evitá-lo precisa tornar a cobertura ausente/falha, nunca sucesso presumido. Todos os cenários normais continuam executados com launcher não instrumentado e o mesmo Core. Nenhum resultado do candidato é usado para conceder autorização.

## Escopo adicional se aprovado

Autor independente cria os casos M3/B4 nos arquivos de AT já autorizados. Fixture nova `bootstrap/slice1/block_fixtures/fault_launcher.py`; suporte no controlador novo aprovado e testes complementares da instrumentação. Não editar controladores/selos v1/v2 nem introduzir flags/hooks públicos de produto. Cobertura por ponto e baseline ficam fixados antes do selo v3. Se o mecanismo IPC necessário não preservar o isolamento descrito, parar com evidência e propor delta antes de relaxar a fronteira.

Sem aprovação deste contrato, M3/B4 permanecem NOT_RUN, publicação v3 e implementação da transição bloqueadas, conforme acceptance-handoff.md já aprovado. O preparo dos demais AT/runtime pode continuar.
