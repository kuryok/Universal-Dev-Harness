# Design derivado do contrato aprovado

Estado: detalhamento documental; contrato PENDING→BLOCKED aprovado pelo usuário em 21/10/2026. Não é implementação, publicação de novo selo ou instalação operacional.

## Esquema lógico materializado

O DDL abaixo define somente v2; as constantes/validação do esquema v1 são preservadas. O predicado nonempty, quando indicado abaixo, é exatamente o usado atualmente para request_id/spec_id/spec_version: `length(trim(COLUNA, char(9)||char(10)||char(11)||char(12)||char(13)||' ')) > 0`. O predicado de timestamp também é o atual: `length(created_at) >= 20 AND substr(created_at, -1) = 'Z' AND julianday(created_at) IS NOT NULL`. Domínio continua validando UUID e RFC3339; CHECK SQL não substitui essa validação.

Tabela runs:

| Coluna | Tipo e restrições |
| --- | --- |
| id | TEXT PRIMARY KEY NOT NULL |
| request_id | TEXT NOT NULL UNIQUE; nonempty |
| spec_id, spec_version | TEXT NOT NULL; nonempty em cada uma |
| state | TEXT NOT NULL; IN ('PENDING','BLOCKED') |
| created_at | TEXT NOT NULL; predicado UTC atual |
| revision | INTEGER NOT NULL; typeof='integer'; par permitido (PENDING,0) ou (BLOCKED,1) |

Tabela events:

| Coluna | Tipo e restrições |
| --- | --- |
| sequence | INTEGER PRIMARY KEY; >0, sem AUTOINCREMENT |
| run_id | TEXT NOT NULL REFERENCES runs(id), sem cascade |
| kind | TEXT NOT NULL; IN ('RUN_CREATED','RUN_BLOCKED') |
| created_at | TEXT NOT NULL; predicado UTC atual |

Unicidade composta events(run_id,kind) permanece. Sequência é global, não um contador por run. Novo RUN_BLOCKED precisa de sequence maior que seu RUN_CREATED.

Tabela block_requests:

| Coluna | Tipo e restrições |
| --- | --- |
| request_id | TEXT PRIMARY KEY NOT NULL; nonempty |
| run_id | TEXT NOT NULL UNIQUE REFERENCES runs(id), sem cascade |
| expected_revision | INTEGER NOT NULL; typeof='integer' e =0 |
| reason | TEXT NOT NULL; nonempty |
| event_sequence | INTEGER NOT NULL UNIQUE REFERENCES events(sequence), sem cascade |
| authorization_id | TEXT NOT NULL UNIQUE; UUID validado pelo domínio |
| controller_route | TEXT NOT NULL CHECK = 'OPERATOR_LOCAL' |
| core_manifest_sha256, control_sha256 | TEXT NOT NULL; length=64 AND NOT GLOB '*[^0-9a-f]*' |
| authorization_rule_version | INTEGER NOT NULL; typeof='integer' e =1 |

Não existe tabela de grants. A linha em block_requests é o histórico do pedido autorizado, não autorização transportável. SQL impede NULL, duplicação, tipos/valores inválidos locais e referência órfã. O leitor/transação do Core valida também os invariantes que cruzam tabelas; esses invariantes não podem ser inferidos apenas por foreign_key_check.

DDL concreto deve usar colunas/checks/uniquidades acima, sem triggers ocultas, índices extras ou permissividade de tabelas desconhecidas. A expressão SQL exata usada pelo store será fixada na implementação e revisada antes dos AT; não há tolerância que apague espaços dentro de literais.

## Algoritmo da migração

1. Abrir banco existente em mode=rw, foreign_keys ON e timeout atual; BEGIN IMMEDIATE. Não inicializar arquivo ausente.
2. Se v2 exato e íntegro, não reconstruir nada; commit de leitura/validação e migrated=false. Se v1: conferir DDL original, ausência de triggers/objetos extras e foreign_key_check; carregar/verificar todas as execuções e eventos segundo domínio v1, incluindo eventos órfãos. Recusar quaisquer outras versões/estados.
3. Criar runs_v2 com DDL final correspondente ao nome temporário; copiar todos os valores originais com revision=0, sem gerar IDs/datas.
4. Criar events_v2 com FK para runs_v2; copiar explicitamente sequence/run_id/kind/created_at.
5. Remover events antiga antes de runs antiga; essas tabelas não têm referências externas permitidas. Remover runs antiga.
6. Renomear runs_v2 para runs, deixando SQLite atualizar a FK de events_v2; renomear events_v2 para events. Exigir legacy_alter_table OFF; recusar ambiente que não preserve as referências finais esperadas. Criar block_requests somente após os nomes finais.
7. Conferir DDL final, contagens, igualdade literal dos dados preservados, foreign_key_check vazio e invariantes de todas as runs; então PRAGMA user_version=2 e commit.

Todos os passos 2–7 estão na mesma transação, sem executescript ou alteração de foreign_keys durante ela. Falha/kill pré-commit deve recuperar a v1; kill pós-commit deve recuperar v2 íntegra. Não há sqlite_sequence no esquema atual; sequence é copiada explicitamente, não recalculada. Nenhum banco criado pela migração, downgrade, reparo silencioso ou backup externo.

## Transação de bloqueio

BEGIN IMMEDIATE; validar v2 e integridade; consultar request_id antes de conferir o estado alvo. Repetição com mesmo run_id/expected_revision/reason retorna o evento e authorization_id originais sem comparar o novo instante com o antigo. Metadados medidos do controlador ficam preservados no primeiro resultado; uma atualização legítima do launcher não cria outro evento no replay, mas a chamada atual precisa passar sua própria verificação de origem/snapshot/configuração.

Pedido diferente com mesma chave falha. Para chave nova, verificar PENDING/revision0, criar UUID de autorização e data UTC no Core, inserir RUN_BLOCKED e block_requests, atualizar runs para BLOCKED/revision1 por compare-and-set, validar conjunto e commit. Não há transação parcial de autorização nem sucesso se o compare-and-set afetar zero linhas. Retorno vem dos dados persistidos. Mudanças concorrentes distintas para a mesma run geram somente uma vencedora.

A migração/conexão permanece em migrations/store; invariantes e descrição da transição ficam em domínio/transitions; serviço trata o caso de uso; operator trata argumentos/apresentação. A ordem interna das escritas é fixa para os testes de interrupção; não expor flags públicas de fault injection. Os testes complementares podem instrumentar conexão; os AT independentes precisam de observação de processo/SQLite sob contrato explícito no handoff.

## Fronteira administrativa do bootstrap

O controlador host usa apenas código confiável e biblioteca padrão. Ele não importa módulos do candidato, nem para validar manifesto/configuração, nem para consultar SQLite.

O snapshot do Core sob teste executa em contêiner administrativo separado do contêiner adversário. Só o administrativo recebe snapshot RO, configuração/launcher RO e diretório privado do SQLite RW. O diretório inteiro fica disponível ao SQLite administrativo para journal/WAL/SHM; seus arquivos e pais não são montados no adversário. O diretório de controle do host nunca é montado como um todo por conveniência.

O administrativo não é o executor de agentes e não inicia nenhum agente. O adversário continua com somente seu candidato RO e dados próprios RW, sem rede, socket Podman, canais, descritores ou configuração administrativa. A autorização operacional é a decisão do controlador de entregar o banco somente ao processo administrativo autorizado, não um booleano que o Core aceite do candidato.

Antes de cada chamada, o controlador valida snapshot/configuração contra hashes fixados na sessão confiável; a execução usa esses mesmos bytes, protegidos contra escrita pelo candidato. Conferir hashes após a chamada e registrar auditoria fora de ambos os diretórios de dados. O bootstrap não protege de quem edita o host: esse limite permanece explícito.

O launcher confiável é gerado pelo controlador a partir de template fixo e pins de configuração/manifesto; não é fornecido pelo candidato. Configuração canônica JSON UTF-8, chaves ordenadas, separadores sem espaços, allow_nan=false e uma quebra de linha final. O hash da configuração cobre todos os bytes, incluindo a quebra. db_path é `/state/udh.sqlite`, não o caminho do host. allowed_operations é exatamente ['MIGRATE_V2','BLOCK_PENDING']; schema=1; core_manifest_sha256 referencia os bytes canônicos do manifesto de módulos. O manifesto tem schema=1 e files com mapa exato de módulos aprovados → SHA256. Sem caminhos absolutos, '..', módulos extras, symlinks ou código/configuração de plugins.

A operação é enviada ao controlador pelo operador/AT confiável; não há endpoint escutando o sandbox. O launcher administrativo provisiona o contexto verificado da entrada operator. Uma cópia desse launcher feita pelo adversário não tem acesso ao banco verdadeiro; AT não podem tratar o banco fabricado pelo adversário como estado autoritativo.

Este layout é contrato do experimento de bootstrap. Instalação operacional, proteção contra escritor no host e promoção automática do Core não são implementadas nem presumidas.
