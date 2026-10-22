# Refinamento do SQLite v3 e protocolo de desbloqueio

Autoria documental de preparo autorizado; não é revisão independente, implementação ou aceite. Complementa [proposal.md](proposal.md). Nenhum arquivo de produto, AT antigo ou selo publicado é alterado por este documento.

## DDL final exato

Os quatro comandos abaixo são executáveis individualmente, nesta ordem, em banco vazio. O texto sem o ponto e vírgula final é o valor esperado de `sqlite_master.sql`; comparar literalmente depois de remover somente espaços externos. Não normalizar aspas, espaços internos ou literais. Índices automáticos `sqlite_*` são permitidos; objetos adicionais, triggers e referências externas são recusados.

```sql
CREATE TABLE "runs" (
    id TEXT PRIMARY KEY NOT NULL,
    request_id TEXT NOT NULL UNIQUE CHECK (length(trim(request_id, char(9)||char(10)||char(11)||char(12)||char(13)||' ')) > 0),
    spec_id TEXT NOT NULL CHECK (length(trim(spec_id, char(9)||char(10)||char(11)||char(12)||char(13)||' ')) > 0),
    spec_version TEXT NOT NULL CHECK (length(trim(spec_version, char(9)||char(10)||char(11)||char(12)||char(13)||' ')) > 0),
    state TEXT NOT NULL CHECK (state IN ('PENDING', 'BLOCKED')),
    created_at TEXT NOT NULL CHECK (length(created_at) >= 20 AND substr(created_at, -1) = 'Z' AND julianday(created_at) IS NOT NULL),
    revision INTEGER NOT NULL CHECK (typeof(revision) = 'integer' AND
        ((state = 'PENDING' AND revision IN (0, 2)) OR (state = 'BLOCKED' AND revision = 1)))
);

CREATE TABLE "events" (
    sequence INTEGER PRIMARY KEY CHECK (sequence > 0),
    run_id TEXT NOT NULL REFERENCES "runs"(id),
    kind TEXT NOT NULL CHECK (kind IN ('RUN_CREATED', 'RUN_BLOCKED', 'RUN_UNBLOCKED')),
    created_at TEXT NOT NULL CHECK (length(created_at) >= 20 AND substr(created_at, -1) = 'Z' AND julianday(created_at) IS NOT NULL),
    UNIQUE (run_id, kind)
);

CREATE TABLE "block_requests" (
    request_id TEXT PRIMARY KEY NOT NULL CHECK (length(trim(request_id, char(9)||char(10)||char(11)||char(12)||char(13)||' ')) > 0),
    run_id TEXT NOT NULL UNIQUE REFERENCES "runs"(id),
    expected_revision INTEGER NOT NULL CHECK (typeof(expected_revision) = 'integer' AND expected_revision = 0),
    reason TEXT NOT NULL CHECK (length(trim(reason, char(9)||char(10)||char(11)||char(12)||char(13)||' ')) > 0),
    event_sequence INTEGER NOT NULL UNIQUE REFERENCES "events"(sequence),
    authorization_id TEXT NOT NULL UNIQUE,
    controller_route TEXT NOT NULL CHECK (controller_route = 'OPERATOR_LOCAL'),
    core_manifest_sha256 TEXT NOT NULL CHECK (length(core_manifest_sha256) = 64 AND core_manifest_sha256 NOT GLOB '*[^0-9a-f]*'),
    control_sha256 TEXT NOT NULL CHECK (length(control_sha256) = 64 AND control_sha256 NOT GLOB '*[^0-9a-f]*'),
    authorization_rule_version INTEGER NOT NULL CHECK (typeof(authorization_rule_version) = 'integer' AND authorization_rule_version = 1),
    result_schema_version INTEGER NOT NULL CHECK (typeof(result_schema_version) = 'integer' AND result_schema_version IN (2, 3))
);

CREATE TABLE "unblock_requests" (
    request_id TEXT PRIMARY KEY NOT NULL CHECK (length(trim(request_id, char(9)||char(10)||char(11)||char(12)||char(13)||' ')) > 0),
    run_id TEXT NOT NULL UNIQUE REFERENCES "runs"(id),
    expected_revision INTEGER NOT NULL CHECK (typeof(expected_revision) = 'integer' AND expected_revision = 1),
    reason TEXT NOT NULL CHECK (length(trim(reason, char(9)||char(10)||char(11)||char(12)||char(13)||' ')) > 0),
    event_sequence INTEGER NOT NULL UNIQUE REFERENCES "events"(sequence),
    blocked_event_sequence INTEGER NOT NULL UNIQUE REFERENCES "events"(sequence),
    authorization_id TEXT NOT NULL UNIQUE,
    controller_route TEXT NOT NULL CHECK (controller_route = 'OPERATOR_LOCAL'),
    core_manifest_sha256 TEXT NOT NULL CHECK (length(core_manifest_sha256) = 64 AND core_manifest_sha256 NOT GLOB '*[^0-9a-f]*'),
    control_sha256 TEXT NOT NULL CHECK (length(control_sha256) = 64 AND control_sha256 NOT GLOB '*[^0-9a-f]*'),
    authorization_rule_version INTEGER NOT NULL CHECK (typeof(authorization_rule_version) = 'integer' AND authorization_rule_version = 1)
);
```

As aspas duplas identificam tabelas e referências consistentemente, incluindo o novo `block_requests`; a diferença textual em relação ao v2 é deliberada e só vale após migração explícita. As colunas legadas do pedido de bloqueio mantêm ordem, tipos e restrições; acrescenta-se ao final `result_schema_version` com tipo inteiro efetivo e valores 2/3. Não há DEFAULT, cascades, tokens, grants, triggers ou índice manual. `INTEGER PRIMARY KEY` obriga sequência inteira e o CHECK exige valor positivo.

UUIDs de runs e autorizações exigem validação canônica de domínio, não apenas NOT NULL/UNIQUE do SQL. Textos devem passar também pelo validador de domínio; datas devem ser RFC3339 UTC válido; hashes permanecem 64 hex minúsculos. Não transformar strings para salvar ou comparar replay: espaços significativos e referências opacas são preservados. A checagem SQL de trim conserva literalmente o conjunto de whitespace legado.

## Migração v2→v3 e renames

Abrir somente banco existente (`mode=rw`), estabelecer `foreign_keys=ON` e `legacy_alter_table=OFF` antes de `BEGIN IMMEDIATE`. Validar versão e DDL de origem exatos, `quick_check`, FKs e domínio/journal de todas as runs. v1, versão0/futura, objetos extras, referências externas ou dados inválidos recusam sem escrita. v3 exato/íntegro retorna migrated=false; não repará-lo. v2 deve ser validado pelas constantes legadas, sem aceitá-lo como v3.

Ainda sob a mesma transação, capturar todos os valores legados explicitamente e em ordem determinística. Executar cada comando seguinte por `execute`, sem `executescript`, commits intermediários ou mudanças de FK. O SQL de criação temporária deriva do DDL final somente pela troca exata dos identificadores de tabelas para os nomes `_v3`; os literais não mudam.

```sql
CREATE TABLE "runs_v3" (
    id TEXT PRIMARY KEY NOT NULL,
    request_id TEXT NOT NULL UNIQUE CHECK (length(trim(request_id, char(9)||char(10)||char(11)||char(12)||char(13)||' ')) > 0),
    spec_id TEXT NOT NULL CHECK (length(trim(spec_id, char(9)||char(10)||char(11)||char(12)||char(13)||' ')) > 0),
    spec_version TEXT NOT NULL CHECK (length(trim(spec_version, char(9)||char(10)||char(11)||char(12)||char(13)||' ')) > 0),
    state TEXT NOT NULL CHECK (state IN ('PENDING', 'BLOCKED')),
    created_at TEXT NOT NULL CHECK (length(created_at) >= 20 AND substr(created_at, -1) = 'Z' AND julianday(created_at) IS NOT NULL),
    revision INTEGER NOT NULL CHECK (typeof(revision) = 'integer' AND
        ((state = 'PENDING' AND revision IN (0, 2)) OR (state = 'BLOCKED' AND revision = 1)))
);

INSERT INTO "runs_v3" (id, request_id, spec_id, spec_version, state, created_at, revision) SELECT id, request_id, spec_id, spec_version, state, created_at, revision FROM "runs";

CREATE TABLE "events_v3" (
    sequence INTEGER PRIMARY KEY CHECK (sequence > 0),
    run_id TEXT NOT NULL REFERENCES "runs_v3"(id),
    kind TEXT NOT NULL CHECK (kind IN ('RUN_CREATED', 'RUN_BLOCKED', 'RUN_UNBLOCKED')),
    created_at TEXT NOT NULL CHECK (length(created_at) >= 20 AND substr(created_at, -1) = 'Z' AND julianday(created_at) IS NOT NULL),
    UNIQUE (run_id, kind)
);

INSERT INTO "events_v3" (sequence, run_id, kind, created_at) SELECT sequence, run_id, kind, created_at FROM "events";

CREATE TABLE "block_requests_v3" (
    request_id TEXT PRIMARY KEY NOT NULL CHECK (length(trim(request_id, char(9)||char(10)||char(11)||char(12)||char(13)||' ')) > 0),
    run_id TEXT NOT NULL UNIQUE REFERENCES "runs_v3"(id),
    expected_revision INTEGER NOT NULL CHECK (typeof(expected_revision) = 'integer' AND expected_revision = 0),
    reason TEXT NOT NULL CHECK (length(trim(reason, char(9)||char(10)||char(11)||char(12)||char(13)||' ')) > 0),
    event_sequence INTEGER NOT NULL UNIQUE REFERENCES "events_v3"(sequence),
    authorization_id TEXT NOT NULL UNIQUE,
    controller_route TEXT NOT NULL CHECK (controller_route = 'OPERATOR_LOCAL'),
    core_manifest_sha256 TEXT NOT NULL CHECK (length(core_manifest_sha256) = 64 AND core_manifest_sha256 NOT GLOB '*[^0-9a-f]*'),
    control_sha256 TEXT NOT NULL CHECK (length(control_sha256) = 64 AND control_sha256 NOT GLOB '*[^0-9a-f]*'),
    authorization_rule_version INTEGER NOT NULL CHECK (typeof(authorization_rule_version) = 'integer' AND authorization_rule_version = 1),
    result_schema_version INTEGER NOT NULL CHECK (typeof(result_schema_version) = 'integer' AND result_schema_version IN (2, 3))
);

INSERT INTO "block_requests_v3" (request_id, run_id, expected_revision, reason, event_sequence, authorization_id, controller_route, core_manifest_sha256, control_sha256, authorization_rule_version, result_schema_version) SELECT request_id, run_id, expected_revision, reason, event_sequence, authorization_id, controller_route, core_manifest_sha256, control_sha256, authorization_rule_version, 2 FROM "block_requests";

DROP TABLE "block_requests";

DROP TABLE "events";

DROP TABLE "runs";

ALTER TABLE "runs_v3" RENAME TO "runs";

ALTER TABLE "events_v3" RENAME TO "events";

ALTER TABLE "block_requests_v3" RENAME TO "block_requests";

CREATE TABLE "unblock_requests" (
    request_id TEXT PRIMARY KEY NOT NULL CHECK (length(trim(request_id, char(9)||char(10)||char(11)||char(12)||char(13)||' ')) > 0),
    run_id TEXT NOT NULL UNIQUE REFERENCES "runs"(id),
    expected_revision INTEGER NOT NULL CHECK (typeof(expected_revision) = 'integer' AND expected_revision = 1),
    reason TEXT NOT NULL CHECK (length(trim(reason, char(9)||char(10)||char(11)||char(12)||char(13)||' ')) > 0),
    event_sequence INTEGER NOT NULL UNIQUE REFERENCES "events"(sequence),
    blocked_event_sequence INTEGER NOT NULL UNIQUE REFERENCES "events"(sequence),
    authorization_id TEXT NOT NULL UNIQUE,
    controller_route TEXT NOT NULL CHECK (controller_route = 'OPERATOR_LOCAL'),
    core_manifest_sha256 TEXT NOT NULL CHECK (length(core_manifest_sha256) = 64 AND core_manifest_sha256 NOT GLOB '*[^0-9a-f]*'),
    control_sha256 TEXT NOT NULL CHECK (length(control_sha256) = 64 AND control_sha256 NOT GLOB '*[^0-9a-f]*'),
    authorization_rule_version INTEGER NOT NULL CHECK (typeof(authorization_rule_version) = 'integer' AND authorization_rule_version = 1)
);
```

A remoção ocorre de filhos para pais. As FKs temporárias apontam exclusivamente às temporárias. Os renames ocorrem de pais para filhos; SQLite reescreve as referências em `events_v3` e `block_requests_v3` para os nomes finais, mantendo as aspas duplas. Criar `unblock_requests` somente depois desses renames.

Antes de `PRAGMA user_version=3` e do único commit: comparar `sqlite_master.sql` com os quatro DDLs finais acima, checar quick_check/FKs, validar todas as invariantes e igualdade literal dos valores legados, incluindo sequências, motivos, datas, UUIDs e medições. Só `result_schema_version=2` é acrescentado aos pedidos antigos; nenhum desbloqueio é fabricado. Falha antes do commit faz rollback integral para v2; commit sem resposta reconcilia pela repetição. `migrate --to2` e `init` recusam v3, sem downgrade. Criação nova em v3 usa PENDING/0 e resposta schema_version=3.

## Invariantes do leitor

- PENDING/0: somente RUN_CREATED; nenhum pedido de transição.
- BLOCKED/1: RUN_CREATED e RUN_BLOCKED, em ordem; exatamente um block_request correspondente, expected_revision=0.
- PENDING/2: os três eventos em ordem; exatamente um pedido de cada transição; unblock expected_revision=1.
- Cada pedido pertence à run indicada; event_sequence aponta ao evento do tipo correto da mesma run. blocked_event_sequence aponta exatamente ao RUN_BLOCKED daquela run, anterior ao RUN_UNBLOCKED. Toda transição tem um único pedido e nenhuma ligação entre runs é aceita, mesmo quando FKs passam.
- Validar UUIDs, datas, texto não vazio, medições, rota e versão da regra em todas as leituras/operações. Recusar pedido órfão, histórico removido, sequência incorreta ou revisão incompatível. `quick_check`/FK isolados não bastam. O motivo de bloqueio não é sobrescrito pelo motivo de desbloqueio.

O evento persistido guarda sequência/tipo/data; o JSON completo de transição é derivado do evento e do pedido vinculado, como no v2. Não há cópia adicional do motivo dentro de events. Uma alteração para outro motivo válido diretamente no host não é detectável por autenticação criptográfica inexistente; a proteção do armazenamento contra escrita adversária continua requisito da fronteira.

## Operação, autorização e replay

Perfil protegido schema2 permite exatamente MIGRATE_V2, BLOCK_PENDING, MIGRATE_V3 e UNBLOCK_RUN; schema1 conserva somente as duas primeiras. Operação ausente recebe DENY3/AUTHORIZATION_REQUIRED antes do banco. Medições atuais autorizam a chamada; o replay não substitui medições históricas. CLI comum não recebe permissões de escrita administrativa.

Validar argumentos também no serviço antes de I/O. Na transação BEGIN IMMEDIATE, validar v3 integral; buscar request_id em unblock_requests antes de conferir estado atual. Chave existente e payload literal idêntico (UNBLOCK_RUN, run_id, expected_revision, reason) devolve o mesmo resultado persistido. Chave existente com outro payload é conflito4 sem efeito. Namespace é separado de create e block.

Pedido novo exige BLOCKED/1 e bloqueio consistente; UUID/data são gerados pelo Core. Três escritas observáveis: inserir RUN_UNBLOCKED; inserir unblock_request incluindo blocked_event_sequence; atualizar runs por compare-and-set de BLOCKED/1 para PENDING/2. Verificar rowcount=1 e todas as invariantes antes do commit. Falha em qualquer ponto desfaz as três escritas. Duas chamadas iguais concorrentes obtêm resposta igual; distintas disputando a revisão produzem um sucesso e um conflito.

A resposta exata contém schema_version=3, request_id, run_id, from_state=BLOCKED, to_state=PENDING, revision=2, event_sequence, created_at e authorization_id. Nenhum campo é regenerado no replay. A resposta histórica do bloqueio usa sempre revision=1/to_state=BLOCKED e o result_schema_version do próprio pedido: 2 se migrado; 3 se criado em v3. Pode ser reproduzida após desbloqueio sem mudar PENDING/2. Uma chave nova de block conflita após esse ciclo; show e replay antigo de create consultam o estado atual.

Não há execução de agentes, efeitos externos, restauração de arquivos/Git, aceite ou entrega. Policy conserva identidade/corpo das regras e TRANSITION_RUN=DENY.

## Observação e experimento de preparo

O observador confiável descobre BEFORE_WRITE para cada escrita e AFTER_COMMIT; o controlador mata o processo após receber o marker e comprova remoção do runtime. Para unblock são três pontos pré-escrita e um pós-commit. Na migração, os 13 comandos de reconstrução acima mais `PRAGMA user_version=3` são candidatos a escrita; a lista efetiva vem da observação confiável, não de um hook público adicionado ao Core. O protocolo deve comprovar que todos os pontos foram alcançados e interrompidos. Transporte inválido, timeout, exit124/125 ou ausência de marker após sucesso são falhas de infraestrutura, nunca baseline funcional negativo. Hooks cooperativos sozinhos não provam kill/recuperação.

Após interrupção, o SQLite confiável recupera hot journal por conexão rw antes da inspeção ro, com árvore privada validada contra symlinks/arquivos especiais. Antes de commit exige-se o snapshot v2/estado BLOCKED original; depois de commit exige-se v3/PENDING2 persistido e replay estável. Não confundir resposta ausente com transação perdida.

Experimento em 22/10/2026: stdlib sqlite3 3.46.1, conexão `:memory:`, sem importar/executar módulos do Core. Três bases sintéticas (vazia, PENDING/0 e BLOCKED/1) receberam cópia, drops e renames na transação acima. Resultado: empty: PASS, pending: PASS, blocked: PASS. Em todos os casos: SQL final de sqlite_master igual literalmente ao DDL publicado, FKs vazias, quick_check=ok e tuplas legadas preservadas; pedido BLOCKED marcado2.

O primeiro ensaio teve aridade de INSERT sintético incorreta (11 placeholders para dez colunas); corrigido no próprio ensaio, sem mudança de contrato ou produto. O experimento prova a mecânica dos renames/FKs e o texto final nessa versão SQLite. Não valida o Core candidato, interrupção real, concorrência, isolamento, testes de aceite ou durabilidade em disco. Esses resultados dependem do runner protegido, regressões e revisão independente R1/R2.
