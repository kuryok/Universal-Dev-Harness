# Slice 1 — primeira transição: bloquear uma execução pendente

Estado: proposta para aprovação humana; somente especificação, sem código ou AT novos. Base: Core/policy `bfb2cbb`, evidências `7072eba`. Não altera documentos fixados nos selos v1/v2.

## Objetivo e fundamento

Permitir ao operador local registrar uma pausa explícita: `PENDING → BLOCKED`, com motivo, revisão otimista e evento persistidos atomicamente em SQLite v2. BLOCKED significa que o trabalho está impedido/pausado e não pode começar; não prova falha de testes, aceite, entrega ou execução prévia. A operação não inicia nem interrompe um agente, pois nenhum executor está integrado.

Referências: arquitetura consolidada v2.5.1, ADR-002/004/011/013/024/028, UX-01 e F-03. Escolha proposta: começar por bloqueio administrativo, antes de IN_PROGRESS. Não criar um serviço de policy ou um segundo Core. Não ratificar a emenda ADR-018 A.

## Limites

Inclui uma única transição e migração explícita. Não inclui desbloqueio, retomada, IN_PROGRESS/DONE, certificação IMPLEMENTED/TESTED/ACCEPTED/DELIVERED, execução de agentes, UI, rede, credenciais, Memorix, dependências novas ou mecanismo genérico de permissões. Retomada de BLOCKED exigirá outro contrato aprovado. Migração não é autorização de trabalho.

## Autorização: fronteira, não alegação

1. O mesmo Core terá uma entrada administrativa separada, proposta como `python3 -m udh.operator`, executada exclusivamente pelo controlador local confiável. Isso é uma entrada do Core, não outro orquestrador. A origem confiável é o domínio do SO/controlador, não um campo actor/role fornecido na solicitação.
2. Antes de executar, o controlador verifica o snapshot aprovado do Core e sua configuração protegida. Ele seleciona o banco operacional por configuração confiável; o pedido não fornece caminho arbitrário de banco nem executável/módulo a importar. O Core não carrega código do workspace do candidato no processo confiável.
3. O candidato não recebe o banco operacional, configuração, canal administrativo ou descritor herdado. Nenhum desses caminhos fica montado, inclusive somente leitura, no sandbox. Dados de teste montados em /work não são o estado operacional oficial.
4. O operador pede uma ação específica no terminal do domínio confiável. Não há credencial portátil, token secreto novo, papel alegado, flag de aprovação ou recibo JSON reutilizável que o candidato possa apresentar. O controlador não aceita solicitações de mudança vindas do agente neste recorte.
5. A autorização é limitada ao pedido `(request_id, run_id, expected_revision, reason, target=BLOCKED)`. A identidade da rota, versão da regra e hashes do Core/configuração são metadados medidos pelo controlador, nunca argumentos de autoridade do solicitante. Eles são registrados com o evento; seu hash identifica bytes, não autentica sozinho.
6. O registro de autorização é histórico da decisão tomada na entrada protegida, não uma tabela de permissões que qualquer CLI possa consumir. Falha de verificação, origem ambígua ou acesso compartilhado ao banco impede a operação antes de escrever.

Pré-condição de implementação: especificar e provar, em AT independentes, a instalação/configuração do snapshot confiável e a separação do banco/canal. A fronteira atual de execução de candidatos não fornece automaticamente esse caminho administrativo. No bootstrap, fixtures e controlador podem ficar dentro do projeto, fora dos mounts do candidato; isso prova a separação frente ao contêiner, não proteção contra o usuário/agente com acesso de escrita ao host. Uma instalação operacional fora do projeto exigirá autorização própria; esta proposta não autoriza fazê-la.

Não expor um método público que conceda autoridade ao receber `trusted=True`, `approved=True`, um objeto JSON ou apenas a existência de um arquivo. Um agente executando uma cópia do Core com seus próprios dados não altera o banco operacional protegido.

## Interface proposta

Entrada do operador, somente no domínio confiável:

- `python3 -m udh.operator migrate --to 2`
- `python3 -m udh.operator block --run-id UUID --expected-revision 0 --request-id ID --reason TEXTO`

A configuração é provisionada pelo controlador, sem flag --control/--db nesta entrada. Contrato fixo: JSON com schema=1, db_path absoluto, core_manifest_sha256 e allowed_operations exatamente [MIGRATE_V2, BLOCK_PENDING]. Caminho e hash esperados da configuração ficam fixados no launcher confiável, não no ambiente/argv do candidato. O manifesto do Core lista somente os módulos aprovados e seus SHA-256; o controlador verifica bytes/tipos/caminhos antes de executar esse snapshot, sem PYTHONPATH do workspace. Arquivos de controle não podem ser symlinks nem ter ancestrais redirecionáveis. O provisionamento de fixtures e o launcher precisam de handoff protegido e AT próprios antes de código; possuir um JSON com esse formato não concede acesso ao banco protegido.

- UUID segue a validação atual; revision é inteiro não negativo, sem bool; request_id e reason não vazios/whitespace, preservados literalmente. target não é parâmetro: sempre BLOCKED.
- Sucesso de block: exit 0, stdout JSON único, stderr vazio. Resposta: `schema_version=2`, `request_id`, `run_id`, `from_state=PENDING`, `to_state=BLOCKED`, `revision=1`, `event_sequence`, `created_at` UTC RFC3339 e `authorization_id`. Repetição exata devolve os mesmos valores persistidos.
- Negação de origem/autorização: exit 3, stdout JSON com `decision=DENY`, `reason_code=AUTHORIZATION_REQUIRED`, `message` e `next_action` em pt-BR; stderr vazio. Não consultar dados operacionais para responder a origem não autorizada.
- Conflito de revisão/estado ou request_id reutilizado com payload diferente: exit 4, stdout vazio, erro em pt-BR no stderr. Erros operacionais/domínio: exit 1; argumentos inválidos: exit 2; stdout vazio e stderr em pt-BR, sem traceback.
- Validação: transporte/origem confiável, parsing/domínio, verificação do snapshot/configuração, abertura do banco, esquema e decisão transacional. Não confiar em validação feita apenas na CLI.
- Entrada comum `python3 -m udh` não ganha escrita por flags. Tentativas de `block`/`migrate` são recusadas; os comandos comuns ainda não implementados podem manter exit 2. AT verificam ausência de escrita, não impõem que o parser comum exponha esses comandos.
- `policy` continua com as regras/hash v1: TRANSITION_RUN permanece DENY nesse caminho de consulta; READ_RUN permite somente metadados. Não reutilizar esse ALLOW para bloquear ou migrar.

## Migração SQLite v1 → v2

- `init` continua criando exclusivamente v1 e retornando schema_version=1. Em banco v2, init v1 recusa e não modifica; não há downgrade ou migração implícita. create/show/policy sobre v1 preservam exatamente seus formatos/comportamentos.
- Migrar exige banco existente v1 exato, esquema e dados íntegros; banco ausente não é criado. Versões 0/futuras, esquema divergente, objetos extras ou triggers não declarados são recusados sem alteração. A permissividade de triggers das fixtures v1 não implica migração automática desses objetos.
- Migração explícita ocorre em uma transação `BEGIN IMMEDIATE`, com foreign_keys ON, sem executescript/commit intermediário. Reconstrução das restrições necessária para permitir BLOCKED e RUN_BLOCKED. Validar integridade referencial e invariantes antes do commit; user_version passa a 2 na mesma transação, por último.
- Preservar literalmente UUIDs, request_ids, referências de spec, timestamps, eventos e suas sequências. Cada execução legada recebe revision=0; nenhum evento de transição/autorização é fabricado. Banco vazio válido também migra. A identidade do banco é seu conteúdo/controle, não apenas o caminho.
- `migrate --to 2` sobre v2 exato é idempotente e devolve `{"schema_version":2,"migrated":false}`; sobre v1 bem-sucedido devolve migrated=true. Não recria tabelas nem acrescenta histórico ao repetir. Nenhum backup exportado ou arquivo de configuração é escrito fora do projeto nesta tarefa.
- Falha em qualquer ponto anterior ao commit deixa esquema, dados e versão anteriores recuperáveis pelo SQLite. Depois de commit confirmado, uma interrupção antes da resposta é reconciliada consultando a versão: repetir migrate não repete efeitos. Banco ocupado falha dentro do timeout atual, sem retry ilimitado.
- Cliente antigo continua recusando v2. O cliente novo entende v1/v2; nunca apresenta v2 como v1 para passar AT antigos.

## Dados e invariantes v2

Contrato lógico fixo; o DDL deve materializar as restrições abaixo e será revisado antes dos AT:

- runs conserva os campos atuais e acrescenta revision inteiro ≥0; estados permitidos neste recorte são somente PENDING/BLOCKED. Criações em v2 continuam PENDING/revision0, com único RUN_CREATED.
- events conserva sequence global positiva, run_id, kind e created_at; admite RUN_CREATED/RUN_BLOCKED. O evento inicial mantém seus valores; RUN_BLOCKED vincula motivo, request_id, revisão 0→1 e authorization_id. Os detalhes novos ficam em block_requests, ligados por event_sequence; events mantém as quatro colunas atuais e amplia somente a restrição de kind. Isso preserva o JSON dos eventos v1.
- Tabela block_requests: request_id TEXT PRIMARY KEY não vazio; run_id TEXT NOT NULL UNIQUE REFERENCES runs(id); expected_revision INTEGER NOT NULL CHECK typeof=integer e valor0; reason TEXT NOT NULL não vazio; event_sequence INTEGER NOT NULL UNIQUE REFERENCES events(sequence); authorization_id TEXT NOT NULL UNIQUE, UUID gerado pelo Core; controller_route TEXT NOT NULL CHECK valor OPERATOR_LOCAL; core_manifest_sha256 e control_sha256 TEXT NOT NULL, 64 hex minúsculos; authorization_rule_version INTEGER NOT NULL CHECK valor1. Uma linha representa conjuntamente o pedido, histórico de autorização e detalhes de RUN_BLOCKED; não há tabela de grants consumíveis nem autorização pendente.
- Vínculo composto validado pelo Core na mesma transação: evento pertence à mesma run, kind RUN_BLOCKED, revisão0→1 e ordem após RUN_CREATED. Campos fixos from_state/to_state/revision são derivados da transição única; timestamp/sequence vêm do evento persistido. Isso permite replay sem gerar novos IDs. Identificador de requisição da criação e do bloqueio têm namespaces próprios.
- PENDING exige revision0, um RUN_CREATED e nenhum RUN_BLOCKED; BLOCKED exige revision1, RUN_CREATED seguido de exatamente um RUN_BLOCKED válido com autorização/pedido correspondentes. Ausência, duplicação, ordem/revisão divergente ou journal incompatível gera erro de integridade, nunca reconstrução silenciosa de sucesso.
- show/create em v2 retornam o envelope de execução com schema_version=2, revision e eventos; os campos antigos mantêm seu significado. RUN_CREATED conserva suas três propriedades; RUN_BLOCKED acrescenta from_state, to_state, reason, request_id, revision e authorization_id. policy continua retornando seu contrato atual, com o estado efetivo consultado. Os formatos v1 permanecem sem campos novos.

## Atomicidade, idempotência e concorrência

Em uma transação de escrita: validar v2 e integridade; procurar request_id de bloqueio; se já existir, conferir payload literal/identidade da operação e devolver o resultado persistido. Repetição ainda precisa entrar pela rota confiável: idempotência não autentica o solicitante. Payload diferente falha sem alteração.

Para pedido novo: conferir PENDING/revision0 e expected_revision=0; decidir a regra fixa de bloqueio pela origem confiável; registrar pedido/autorização e evento e atualizar estado/revisão como unidade atômica. UUID/timestamp são gerados pelo Core e persistidos, não fornecidos pelo cliente. Se qualquer escrita falhar, rollback integral, inclusive auditoria decisória no banco; negações do controlador não se tornam autorizações persistidas.

Duas chamadas iguais concorrentes retornam o mesmo resultado, com um único evento. Duas chaves distintas para a mesma revisão: só uma vence; a outra recebe conflito e não acrescenta autorização/evento. Se a resposta se perder após commit, repetir a mesma requisição é seguro. Nenhuma reexecução de ação externa está envolvida.

## Organização e sequência de entrega

Arquivos de produto candidatos após aprovação: `udh/domain.py`, `store.py`, `service.py`, `__main__.py`, novo `migrations.py`, `transitions.py` e `operator.py`; testes complementares específicos. O DDL deve seguir este contrato; qualquer mudança de semântica exige aprovação neste mesmo change. O handoff detalha apenas os arquivos e fixtures da fronteira protegida, sem manter plano paralelo. Separar migração, regras de domínio, transação de persistência, serviço e apresentação. Não concentrar essas responsabilidades no Core único ou em arquivo crescente.

1. Aprovar semântica BLOCKED, compatibilidade, autorização e critérios desta proposta.
2. Revisar o DDL derivado e detalhar o handoff protegido neste change; aprovar qualquer alteração semântica e escopo exato de infraestrutura. Autoria dos AT em contexto independente, baseline negativo, revisão e próximo selo imutável são pré-condições; não criar/editar AT como implementador.
3. Implementar migração e leitura versionada em incremento próprio; verificar antes de bloquear execuções.
4. Implementar uma única transição através da entrada confiável e verificar concorrência/recuperação; revisão independente final.

Nenhuma alteração dos AT/selos/controladores v1/v2 é autorizada por esta proposta documental. A geração posterior e o caminho administrativo precisam de handoff explícito, sem enfraquecer verificadores anteriores.

## Cenários de aceite propostos (ainda não executáveis)

| ID | Evidência necessária |
| --- | --- |
| M1 | Migrar fixtures v1 vazia/múltiplas runs; comparar valores/eventos/sequências literalmente, versão2 e revision0; repetir sem efeitos. |
| M2 | Ausente, versão futura/0, esquema/trigger/linha incompatível: erro e nenhuma criação/alteração. |
| M3 | Falha e encerramento do processo entre operações de DDL/cópia/versão: recuperação integral; commit sem resposta seguido de repetição segura; concorrência de migração. |
| B1 | Pedido genuíno via controlador protegido: PENDING→BLOCKED/revision1, motivo/evento/autorização consistentes após reiniciar processo. |
| B2 | Candidato tenta canal, banco, configuração, flags/JSON de autoridade, import de entrada administrativa, substituição de snapshot e symlink: não alcança estado operacional nem obtém autorização. Fixture acessível ao candidato não conta como banco oficial. |
| B3 | Exatidão idempotente, payload divergente e chamadas concorrentes iguais/distintas; no máximo uma transição e um registro de autorização. |
| B4 | Falha/kill antes de cada escrita e após commit/antes da resposta: atomicidade e replay comprovados, sem evento ou autorização órfã. |
| B5 | Tentativas de IN_PROGRESS/DONE/ACCEPTED/DELIVERED, segunda transição e revisão obsoleta: recusadas; política comum continua sem conceder escrita. |
| R1 | 16 complementares, 10 AT Core v1 e 11 AT policy v2 preservados em bancos v1; I1–I7/T1–T8 e selos intactos. Novos AT cobrem separadamente v2; não reescrever expectativas antigas para versão2. |
| R2 | Revisão independente de DDL, fronteira administrativa, modularidade e evidências; nenhuma aceitação por parecer de LLM. |

AT precisam observar também o SQLite por leitor confiável e fixtures declaradas, pois JSON sozinho não prova atomicidade ou integridade. POC de falha de processo não prova queda de energia/hardware; declarar esse limite. Se não for possível proteger canal/banco no runtime real, B2 falha e a transição não é liberada.

## Resultado desta preparação

Somente proposta documental. Testes novos/migração/transição: não executados, pois não implementados. A verificação dos testes existentes desta sessão será registrada separadamente; resultados anteriores não provam o contrato novo. Suposições a aprovar: bloqueio administrativo primeiro, migração opt-in e operador local confiável sem credenciais novas. Não há aceite de produto completo nem encerramento da Slice 1.
