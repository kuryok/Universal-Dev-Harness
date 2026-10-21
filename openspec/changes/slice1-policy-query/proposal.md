# Slice 1 — tarefa 2: consulta determinística de política

Estado: contrato aprovado pelo usuário em 21/10/2026 ("Aprovo!"). Implementação aguarda o preparo independente de AT e selo, conforme dependência abaixo. A extensão protegida está delimitada em [acceptance-handoff.md](acceptance-handoff.md) e aguarda autorização específica.

## Objetivo e decisão de escopo

Acrescentar uma consulta de política ao mesmo Core. O operador consulta uma execução existente e recebe decisão, motivo e próxima ação em JSON. A avaliação usa regras fixas/versionadas e o estado lido do SQLite, nunca parecer de LLM ou alegação de aprovação do solicitante.

Referências: arquitetura consolidada v2.5.1, ADR-002/011/013/024 e F-03; Core `b26fd96`, selo bootstrap v1 `e6056d5bec095d4023b1a30286dc978d000e3d0b3e8ab19f4642da8c84bab704`. Não ratifica ADR-018 A.

Este recorte é somente leitura: não aplica transições nem emite credencial de execução. É uma decisão determinística do Core, não um Judge consultivo. O comando informa o limite atual; não representa enforcement de um executor ainda não integrado.

## Por que preservar a v1

SQLite v1 restringe runs.state a PENDING, eventos a RUN_CREATED; domínio e leitura exigem exatamente um evento. Os AT vigentes também exigem init com schema_version=1. Alterar essas regras silenciosamente seria regressão. O publicador de selo só implementa geração 1.

Transições persistidas exigem recorte versionado próprio: semântica de estados, migração explícita, journal transacional, concorrência/idempotência, novos AT e próximo selo. IN_PROGRESS/BLOCKED/DONE de progresso não serão confundidos com IMPLEMENTED/TESTED/ACCEPTED/DELIVERED de certificação/entrega.

## Interface e contrato propostos

`python3 -m udh policy --db CAMINHO --run-id UUID --action AÇÃO`

- Identidade e estado vêm da consulta atual do Core. A referência de spec permanece opaca e não prova aprovação.
- Ações são identificadores exatos, sem normalização silenciosa. Texto vazio ou composto apenas de whitespace é inválido; identificador desconhecido recebe DENY.
- Saída: um objeto JSON UTF-8 com policy_version (inteiro 1), policy_sha256, run_id, state, action, decision, reason_code, message e next_action. Textos humanos em pt-BR; códigos em inglês. Nenhuma data variável: repetição com os mesmos dados retorna o mesmo objeto.
- policy_sha256 é SHA-256 do corpo declarativo canônico das regras e da versão, sem fatos da execução ou segredo. Não é assinatura nem aprovação humana. Versão de policy, esquema SQLite e at_version são identidades distintas.
- ALLOW: exit 0; DENY: exit 3. Ambos retornam JSON no stdout, stderr vazio. Falha operacional conserva o contrato atual (stdout vazio, stderr em pt-BR, exit 1); argumentos inválidos, exit 2.
- Ordem de validação: parsing/flags e ação vazia (exit 2), UUID pelo domínio (exit 1, como show), consulta ao banco/execução (exit 1), avaliação da ação. Uma ação desconhecida só recebe DENY após obter uma execução válida. Nenhuma etapa anterior cria banco.
- Não aceitar --approved, --verified, --role ou JSON arbitrário de fatos. Flags desconhecidas são erros de argumento. Nenhuma alegação altera o veredito.

| Ação | Decisão | Código | Próxima ação segura |
| --- | --- | --- | --- |
| READ_RUN | ALLOW | READ_ONLY_METADATA | Consultar metadados; não executar trabalho |
| EXECUTE_AGENT | DENY | EXECUTOR_NOT_READY | Preparar integração e evidências confiáveis do executor |
| TRANSITION_RUN | DENY | TRANSITIONS_NOT_ENABLED | Aprovar contrato versionado das transições |
| ACCEPT_ARTIFACT | DENY | ACCEPTANCE_GATE_UNAVAILABLE | Implementar gates/evidências de aceite |
| DELIVER_ARTIFACT | DENY | DELIVERY_GATE_UNAVAILABLE | Implementar gates de entrega e smoke |
| Qualquer outra | DENY | UNKNOWN_ACTION | Escolher operação suportada; não ampliar privilégios |

O corpo usado no hash é `{"version":1,"rules":{AÇÃO:{"decision":DECISÃO,"reason_code":CÓDIGO},...},"default":{"decision":"DENY","reason_code":"UNKNOWN_ACTION"}}`, com as cinco ações nomeadas da tabela (a linha “qualquer outra” é o default). Serialização JSON com chaves ordenadas, separadores `,`/`:` sem espaços, UTF-8, sem NaN e sem quebra de linha final. policy_sha256 é o digest hexadecimal minúsculo de 64 caracteres. Mensagens/next_action são apresentação; não alteram as regras nem concedem permissões. Esta identidade não inclui UUID/estado/ação solicitada.

ALLOW vale exclusivamente para ler metadados sob o contexto administrativo local já usado por show. Não autentica o operador, não aprova capabilities e não pode ser reutilizado para executar, escrever ou promover um artefato. init/create/show atuais permanecem com seus contratos; este recorte não adiciona uma barreira fictícia a operações já administrativas.

## Organização e arquivos permitidos após aprovação

- Novo `udh/policy.py`: regras/versão/hash e avaliação sem I/O, banco, rede ou subprocessos. Sem mecanismo de plugins ou configuração executável.
- `udh/service.py`: obtém Run real pelo store e invoca avaliação, sem aceitar fatos de autoridade vindos do solicitante.
- `udh/__main__.py`: argumentos e apresentação da decisão; sem SQL e sem duplicar regras.
- Novo `tests/test_policy.py`: testes complementares; não substituem AT.
- Documentos desta mudança e MEMORY.md: requisitos, aprovação, resultados e retomada curta.

Não modificar domain/store, esquema, AT originais, selo v1, publicador ou controladores existentes. Nenhuma dependência, credencial, rede ou escrita fora do projeto. Não criar serviço de policy separado do Core.

## Cenários para aprovação e futuros AT independentes

| ID | Comportamento | Verificação prevista |
| --- | --- | --- |
| P1 | READ_RUN permitido só como leitura de execução existente | Criar execução, consultar policy, conferir identidade/estado/reason_code/exit e ausência de efeitos |
| P2 | Execução, transição, aceite e entrega negados | Exercitar as quatro ações, conferir DENY, código específico, exit 3 e próxima ação sem bypass |
| P3 | Desconhecido/alegação não amplia privilégios | Ação desconhecida, variação de caixa, texto de instrução e flags de aprovação: DENY ou argumento inválido; nenhum ALLOW de escrita |
| P4 | Consulta não inicializa nem modifica estado | Banco ausente/incompatível, UUID inválido/desconhecido: erro; arquivo ausente continua ausente; banco válido mantém versão, esquema, runs e events |
| P5 | Política identificada e resposta estável | Mesma consulta em processos distintos retorna JSON igual; hash confere com regras canônicas e versão |
| P6 | Regressão v1 preservada | Nove testes complementares e dez AT do Core continuam passando; I1–I7/T1–T8 e selo v1 preservados |
| P7 | Separação e alcance claros | Revisão independente: policy sem I/O, CLI sem SQL/regra duplicada; nenhum executor ou transição liberado |

Estes são cenários propostos, não AT executáveis. O implementador não cria ou altera AT. Antes do código, cenários precisam de aprovação, autoria em contexto independente e linha de base negativa sobre o Core v1, com proteção e identidade verificadas conforme B4.2–B4.6. Não enfraquecer nem sobrescrever o selo existente para incluir cobertura nova.

## Dependência de handoff e verificação

O preparo do próximo conjunto/versão de AT é pré-condição real. O suporte à geração seguinte do selo ainda não existe; sua extensão precisa de escopo e autorização explícitos para os componentes protegidos. Esta proposta não autoriza modificar esses arquivos nem dispensa o gate. Não iniciar implementação apenas com testes complementares.

Depois de resolvida essa dependência, executar novos testes complementares, AT independentes, regressões vigentes e revisão dos hashes do candidato. Relatar PASS/FAIL/NOT_RUN/INFRA_ERROR, sem tratar passo não executado como aprovação. OpenSpec CLI e fast/full do harness não executados porque ausentes.

Próximo recorte posterior: uma transição persistida específica com migração e autorização confiável, antes de integrar execução de agentes. A política deste recorte não certifica a fundação inteira nem encerra a Slice 1.

## Revisão e verificação desta preparação

Revisão em contexto independente: escopo somente leitura e compatibilidade v1 confirmados; especificados os bytes do hash, whitespace inválido e precedência de validação. Nenhuma fonte de produto foi alterada.

Nesta sessão: nove testes complementares do Core PASS; hashes dos cinco módulos coincidem com o candidato verificado; selo v1, AT e controladores íntegros; `git diff --check` PASS. Os AT anteriores não foram reexecutados nesta preparação documental; os novos cenários P1–P7 não foram executados, pois policy ainda não foi implementada. Não declarar policy testada por causa dos testes do Core.
