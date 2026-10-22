# Correção da publicação IPC — contrato existente, geração 4

Objetivo: cumprir o contrato instrumentado já aprovado sem arquivos IPC parcialmente visíveis. Não altera a migração, autorização ou critérios dos AT. Continuação da validação solicitada em 22/10/2026.

Diagnóstico: M3 apresentou INFRA_ERROR em dois checkpoints no selo v3; esses pontos passaram isoladamente. O bridge v3 não guardou a causa bruta, portanto a ligação causal entre os erros e a corrida é inferência. A publicação de ACK com write_text abre o destino antes de escrever; o observador pode ver exists=true e ler conteúdo vazio. A publicação de done.json tem o mesmo padrão. Reprodutor em migration-ipc-race.json comprova a possibilidade desse padrão, sem afirmar causa bruta não coletada.

Correção nos arquivos já autorizados: operator_process.py escreve ACK em arquivo temporário no mesmo diretório e publica por replace somente após fechar; block_fixtures/fault_launcher.py faz o mesmo para done.json. Marcadores já são publicados por rename. Nenhuma flag pública, bypass de teste ou mudança de timeout/asserções.

O controlador deve preservar no audit a resposta bruta da observação antes de classificar erro, para não perder diagnósticos. operator_bridge.py recebe apenas essa ampliação de evidência, sem aceitar erros de infraestrutura como resultados funcionais.

Publicação: evolução do mesmo block_seal.py para produzir geração4, predecessor fixo d1362b119315a504976adf573f802fd7b3e4b045e03554a1432fc43c76cf3e0a; seu verificador reconhece exclusivamente os contratos exatos 3 e 4. A geração3 conserva cadeia/aprovação/lista de specs originais; geração4 acrescenta este documento. Fonte e AT/manifestos/controladores copiados em seals/v1/v2/v3 não são editados. create_seal só publica geração4 por criação exclusiva. Nenhuma reinterpretação das evidências antigas.

Pronto: prova determinística da publicação completa; complementares; baseline do Core sem operator novamente identificado; revisão independente dos bytes finais; selo v4; repetir M3 e migração no candidato96bd4dc; regressões e hashes conferidos. Os AT permanecem idênticos aos do v3. Não habilitar block. Falha recorrente muda abordagem e permanece registrada, sem enfraquecer gates.
