# Slice 1 — desbloqueio administrativo de BLOCKED

Estado: proposta para aprovação humana, somente especificação. Base implementada: `ad2bd47`; evidências `1fc61db`; selo v5 `182d2ba7…`. Não modifica documentos/AT/controladores fixados nos selos existentes.

## Objetivo e limites

Adicionar uma única transição `BLOCKED/revisão 1 → PENDING/revisão 2`, solicitada pelo operador local através da fronteira administrativa protegida do mesmo Core. PENDING significa que a pausa administrativa foi retirada; não significa trabalho iniciado, causa técnica do bloqueio comprovadamente resolvida, teste aprovado ou entrega aceita.

Não executar agente, retomar subprocesso, reexecutar efeito externo, restaurar arquivos, modificar Git ou conceder IN_PROGRESS/DONE/ACCEPTED/DELIVERED. Não implementar ciclos gerais: depois de PENDING/2, um novo block é conflito. O primeiro ciclo completo é limitado a PENDING/0 → BLOCKED/1 → PENDING/2. Outro bloqueio/retomada exigirá contrato próprio, sem apagar eventos ou zerar revisão.

Referências: arquitetura v2.5.1, ADR-002/004/011/013/024/028, UX-01 e F-03. A recuperação aqui cobre a consistência do estado SQLite/journal após interrupção. Não completa o checkpoint de Git/spec/artefatos/evidências da F-03; esse trabalho segue pendente antes de integrar execução de agentes. Não ratifica ADR-018 A.

## Interface e autorização

No launcher administrativo protegido:

`python3 -m udh.operator unblock --run-id UUID --expected-revision 1 --request-id ID --reason TEXTO`

- Sem --db, --target, --approved ou fatos de autoridade fornecidos pelo solicitante. UUID segue validação existente; revisão é inteiro não negativo, sem bool; ID/motivo não vazios/whitespace e preservados literalmente. target é sempre PENDING.
- Contexto e banco continuam inacessíveis ao contêiner adversário. O host executa somente ferramentas confiáveis; o Core candidato executa no runtime administrativo isolado. Metadados/hash/configuração não são credencial portátil. O operador decide retirar a pausa; nenhum parecer de LLM ou declaração textual de resolução confere autorização.
- Novo perfil de configuração protegido schema=2: mesmo db_path fixo e manifesto do Core, allowed_operations exatamente ['MIGRATE_V2','BLOCK_PENDING','MIGRATE_V3','UNBLOCK_RUN']. Perfil schema=1 e sua lista antiga continuam aceitos somente para MIGRATE_V2/BLOCK_PENDING. Uma operação ausente da lista efetiva recebe DENY antes de consultar o banco. Medições e authorization_rule_version=1 são registradas por operação, com controller_route=OPERATOR_LOCAL.
- Os pins e launcher novos precisam de preparação/revisão independente, sem alterar os bytes dos lançadores/selos publicados. Configuração nova não é carregada por argv/ambiente do candidato. Só o controlador confiável provisiona seu caminho e hash esperados.
- CLI comum não ganha unblock/migrate/block. Policy mantém o corpo/hash atual e TRANSITION_RUN=DENY; READ_RUN continua apenas metadados.

Sucesso: exit0, stdout JSON único UTF-8, stderr vazio. Campos exatos: schema_version=3, request_id, run_id, from_state=BLOCKED, to_state=PENDING, revision=2, event_sequence, created_at UTC RFC3339 e authorization_id UUID. Valores são persistidos/derivados do evento, não gerados novamente no replay.

Negação de contexto/operação: exit3, JSON DENY/AUTHORIZATION_REQUIRED com message/next_action em pt-BR, stderr vazio e nenhum acesso ao banco. Conflito de estado/revisão ou chave reutilizada com payload diferente: exit4, stdout vazio e stderr em pt-BR. Falha operacional/domínio: exit1. Parsing/flags inválidas: exit2. Sem traceback. Validação ocorre também no serviço, antes de I/O; a decisão transacional usa o estado real.

## SQLite v3 e migração explícita

`python3 -m udh.operator migrate --to 3`, somente com MIGRATE_V3 no perfil efetivo.

- Aceita banco v2 exato/íntegro ou v3 exato/íntegro. Em v1 exige primeiro migrate --to2; não encadeia migrações silenciosamente. Ausente/versão0/futura/esquema alterado/triggers/dados inválidos: recusa sem criar, reparar ou modificar banco.
- v2→v3 devolve {'schema_version':3,'migrated':true}; repetir em v3 devolve migrated=false sem novos eventos/pedidos. migrate --to2 e init em v3 recusam sem downgrade. init e criação inicial continuam exclusivamente v1.
- BEGIN IMMEDIATE, foreign_keys ON, legacy_alter_table OFF; nenhuma mudança intermediária de FK, executescript ou commit. Validar esquema, quick_check, referências, invariantes e igualdade literal dos valores legados antes de user_version=3/commit. Falha pré-commit recupera v2 integral; commit sem resposta é reconciliado pela repetição idempotente.
- Reconstruir runs_v3, events_v3 e block_requests_v3 com FKs entre tabelas temporárias; copiar valores/sequências explicitamente. Remover block_requests antes de events antes de runs antigas, renomear para nomes finais com referências atualizadas e só então criar unblock_requests. Nenhuma referência externa é permitida. Revisar o SQL final exato produzido pelos renames antes de publicar novos AT.
- Preservar run IDs, referências opacas de spec, motivos, datas, revisões0/1, eventos e registros de autorização. Não fabricar desbloqueios. Cada block_request migrado recebe somente result_schema_version=2, preservando a forma da resposta emitida antes da migração.
- Cliente antigo recusa v3. Cliente novo conserva formatos/comportamentos v1/v2 e lê v3 explicitamente, sem apresentar v3 como v2 para passar AT antigos.

## Modelo persistido e leitura v3

Manter tabelas/módulos separados por responsabilidade. Contrato lógico para DDL revisado:

- runs: campos existentes, permitindo somente (PENDING,0), (BLOCKED,1), (PENDING,2); revision inteiro não negativo. Criação em v3 continua PENDING/0. Estado terminal deste recorte é PENDING/2, sem reblock.
- events: campos/ordem/sequências existentes, novo kind RUN_UNBLOCKED; UNIQUE(run_id,kind) permanece, portanto no máximo um evento de cada tipo por run.
- block_requests: campos atuais preservados; novo result_schema_version INTEGER NOT NULL CHECK IN(2,3). Registros migrados guardam2; bloqueios criados diretamente em v3 guardam3. expected_revision continua0, regra/rota/medições originais permanecem imutáveis.
- unblock_requests: request_id TEXT PRIMARY KEY NOT NULL/nonempty; run_id TEXT NOT NULL UNIQUE REFERENCES runs(id); expected_revision INTEGER NOT NULL com typeof=integer e valor1; reason TEXT NOT NULL/nonempty; event_sequence INTEGER NOT NULL UNIQUE REFERENCES events(sequence); blocked_event_sequence INTEGER NOT NULL UNIQUE REFERENCES events(sequence); authorization_id TEXT NOT NULL UNIQUE/UUID; controller_route=OPERATOR_LOCAL; core_manifest_sha256/control_sha256 TEXT NOT NULL com 64 hex minúsculos; authorization_rule_version INTEGER NOT NULL/typeof=integer/valor1. Sem cascades, grants pendentes, tokens ou triggers.
- A chave de unblock é global dentro de unblock_requests, em namespace distinto de create e block. O payload da identidade é operação UNBLOCK_RUN + run_id + expected_revision + reason. Idêntico ID textual usado em operações diferentes não confunde seus resultados.
- RUN_UNBLOCKED referencia exatamente o RUN_BLOCKED anterior da mesma run; tem sequência maior e vínculo a um único unblock_request. Seu JSON contém sequence/kind/created_at, from_state=BLOCKED, to_state=PENDING, revision=2, request_id/reason/authorization_id e blocked_event_sequence. Eventos antigos conservam seus formatos/valores.

Invariantes do leitor: PENDING/0 tem apenas RUN_CREATED, sem pedidos de transição; BLOCKED/1 tem RUN_CREATED+RUN_BLOCKED e um block_request; PENDING/2 tem os três eventos na ordem correta e exatamente um pedido de cada transição. Recusar órfãos, ligação entre runs diferentes, revisão/evento/pedido incompatíveis ou sequência fora de ordem, inclusive quando FKs isoladamente parecem íntegras. Nenhuma reconstrução silenciosa de sucesso. Validar também conteúdo de domínio/medições, mantendo SQL literal significativo.

show/create em v3 retornam schema_version=3 e revisão real; demais campos conservam significado. policy informa estado atual e mantém identidade das regras. O histórico de bloqueio não é apagado, e o motivo do desbloqueio não sobrescreve o motivo original.

## Transação, concorrência e replay durável

Em BEGIN IMMEDIATE: validar v3 e integridade; buscar request_id em unblock_requests antes de conferir estado. Repetição idêntica, ainda pela rota autorizada, devolve o resultado persistido sem nova data/UUID/evento/autorização. Payload diferente conflita sem efeito. Medições atuais do launcher são verificadas para autorizar a chamada; não substituem as medições históricas no replay.

Pedido novo: conferir BLOCKED/1, expected_revision=1 e registro/evento de bloqueio consistentes; gerar UUID/data no Core; inserir RUN_UNBLOCKED e unblock_request, atualizar runs por compare-and-set para PENDING/2, validar conjunto e commit. Falha em qualquer escrita desfaz tudo. Duas chamadas iguais concorrentes retornam o mesmo resultado; distintas para a mesma revisão têm uma vencedora e um conflito. Commit sem resposta permite replay seguro. Não há efeito externo a repetir.

### Replay do bloqueio antigo depois do desbloqueio

Repetir o block_request original continua permitido como leitura de seu resultado histórico, desde que a chamada atual esteja autorizada. Deve devolver exatamente a resposta original: to_state=BLOCKED e revision=1, data/UUID/sequence originais, schema_version=2 para registro migrado ou3 para bloqueio criado em v3. Isso não altera o estado atual PENDING/2. show é a fonte do estado atual; resposta de replay descreve a transição histórica.

Não usar run.revision atual para reconstruir a resposta antiga. Uma chave nova ou payload/revisão diferente não reabre o bloqueio: conflito4. Replays antigos de create conservam seu comportamento existente de consultar a execução atual. Não expandir agora o conceito de checkpoint para certificar Git/artefato ou a identidade de uma spec opaca.

## Arquivos e preparo incluídos na proposta para aprovação

Produto: adaptar somente udh/domain.py, migrations.py, store.py, service.py, transitions.py e operator.py; CLI comum/policy só se indispensável para preservar o contrato, sem novas permissões. Testes complementares novos em tests/test_unblock.py. Não criar componente de orquestração novo nem concentrar tudo em um módulo.

Preparo transitório protegido proposto junto com este contrato: autoria independente de bootstrap/slice1/unblock_acceptance/test_unblock.py e coverage.json; evolução dos controladores fonte operator_process.py, operator_bridge.py, block_seal.py, integrate_block.py e block_fixtures/operator_launcher.py para perfil schema2/novo conjunto. Acrescentar fixtures sintéticas somente em block_fixtures quando necessárias. Próximo selo imutável v6 encadeado ao v5; declarar hashes/arquivos exatos no manifesto. Testes novos convivem com os antigos sem alterar suas asserções, bytes ou cobertura original.

A aprovação solicitada abrange esse preparo nomeado e a semântica funcional; não autoriza editar snapshots publicados v1–v5, testes antigos ou CI/configuração de engines, instalar fora do projeto, baixar imagens ou dependências, obter credenciais ou abrir rede. Se o desenho exigir mudar um desses limites ou criar outro componente, apresentar delta antes.

Antes do código de produto: refinamento do DDL dentro deste mesmo change, autoria de AT em contexto independente, baseline contra o Core atual sem unblock, perfil/runner protegidos e revisão do snapshot, cobertura e próximo selo. O protocolo instrumentado já aprovado permanece, sem hooks públicos novos: observar cada escrita e pós-commit, falha de infraestrutura nunca conta como negativa funcional. Registrar limites de processo instrumentado e preservação dos AT/selos antigos.

Sequência de implementação após aprovação e preparo: migração/leitura v3 → verificar; operação unblock e replay histórico → verificar; regressões e revisão final. Não iniciar agentes ao terminar.

## Cenários de aceite propostos, não executáveis

| ID | Resultado verificável |
| --- | --- |
| U1 | Perfil autorizado: BLOCKED/1→PENDING/2, evento/pedido/autorização consistentes, motivo literal preservado e consulta em novo processo. |
| U2 | Ausência/forja de contexto, perfil schema1 sem UNBLOCK_RUN, flags/JSON de aprovação, substituição de snapshot/configuração e acesso pelo adversário: negação sem escrita/acesso ao banco operacional. |
| U3 | Replay idêntico estável; mudança de run/motivo/revisão na mesma chave conflita; chaves de outras operações não confundem lookup; duas requisições iguais/distintas concorrentes discriminadas. |
| U4 | Repetir bloqueio original depois de migrar/desbloquear: mesma resposta histórica, estado atual não muda. Novo block ou revisão2/novo unblock em PENDING/2 conflita. |
| U5 | Migração v2 vazia/PENDING/BLOCKED preserva dados/eventos e marca result_schema_version2; repetição sem efeito; v1/ausente/futura/esquema/linha incompatível recusados. Novos block em v3 retornam schema3. |
| U6 | Falha/kill em cada escrita de migração/unblock e após commit: rollback/replay íntegros, sem pedido/autorização órfãos; concorrência de migração; todos os pontos identificados pelo observador confiável. |
| U7 | Journal fabricado com FKs válidas mas outra run/evento/revisão, ordem errada, motivo/ID/hash inválido ou histórico removido: leitor e operações recusam. |
| U8 | IN_PROGRESS/DONE/ACCEPTED/DELIVERED, reblock, autoexecução e concessão por policy continuam indisponíveis. |
| R1 | 36 complementares, 13 AT do v5, dez AT v1, onze AT policy e I1–I7/T1–T8 preservados nos perfis/bancos originais; selos intactos. Novos cenários verificam v3 separadamente. |
| R2 | Revisão independente de DDL, fronteira, compatibilidade, replay entre versões e modularidade; hashes do candidato e evidências correspondentes, sem achados pendentes. |

## Suposições e pronto desta especificação

Suposições para aprovação: uma única retirada administrativa de pausa; SQLite v3 opt-in; histórico imutável e replay de transição independente do estado atual; nenhuma promessa de execução/retomada de agente ou correção automática do motivo.

Esta proposta não implementa migração, unblock, novos AT ou perfil administrativo. A aprovação humana precede a execução do preparo nomeado e a implementação. Verificação desta preparação será registrada em verification.md; não aproveitar testes de bloqueio para declarar desbloqueio testado. Slice 1 permanece incompleta.
