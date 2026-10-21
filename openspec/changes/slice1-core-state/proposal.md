# Slice 1 — tarefa 1: estado persistente mínimo

Estado: cenários e preparo transitório aprovados em 21/10/2026. Integração externa e selo inicial do bootstrap verificados; Core mínimo + SQLite implementado e verificado no recorte abaixo; a Slice 1 completa permanece em construção.

## Objetivo

Criar e consultar uma execução local em SQLite, preservando identidade e evento inicial após reabrir o processo. Esta tarefa inicia a Slice 1; não entrega a Slice 1 inteira.

Referência: arquitetura consolidada v2.5.1, ADR-011 e F-03, arquivo local `UDH-v2.5-ARQUITETURA-CONSOLIDADA-v2.5.1.md`. Não ratifica ADR-018 A nem incorpora alterações preexistentes ao Git.

## Condições para iniciar

B4.2 exige cenários aprovados e AT independentes antes de iniciar ticket de produto. A rota proposta é cumprir esse contrato, sem presumir exceção de protótipo. Os [cenários independentes](acceptance.md) são a entrada de aceite aprovada para a preparação transitória; não são, por si, AT executáveis nem um selo de aceite.

Antes de implementar: aprovar os cenários, gerar AT em contexto independente do implementador, comprovar a linha de base negativa e proteger testes/runner/manifesto no domínio confiável conforme B4.4–B4.6. O implementador não pode escrever ou alterar esses AT. Se a fundação ainda não permitir isso, registrar bloqueio e decidir explicitamente o bootstrap, sem iniciar produto apenas com testes complementares.

A CLI OpenSpec está ausente: validação por ela não executada. A regra de bloqueio de handoff na seção de integração de `grill-me-with-docs` não será generalizada para esta preparação manual, que não incorpora a skill. Não alegar validação strict; eventual instalação depende de proposta e aprovação de dependência. A revisão documental não substitui aprovação semântica nem os AT.

## Escopo proposto

Python 3 e biblioteca padrão (sqlite3, argparse, unittest), sem dependências, rede ou credenciais. Nesta máquina usar `python3`.

- `python3 -m udh init --db CAMINHO`: inicializar esquema versão 1 em caminho novo ou banco vazio; repetição preserva dados. Banco não vazio sem esquema UDH reconhecido, esquema incompatível ou versão desconhecida é recusado sem alterações/migração automática. Inicialização do esquema e da versão é transacional.
- `python3 -m udh create --db CAMINHO --spec-id ID --spec-version V --request-id ID`: criar UUID, referência não vazia à especificação e estado PENDING. Referenciar uma spec não comprova sua aprovação; nenhuma execução de trabalho será permitida nesta tarefa.
- `python3 -m udh show --db CAMINHO --run-id UUID`: consultar execução e seu evento inicial. Banco inexistente e execução desconhecida falham; consulta/criação não inicializam banco implicitamente.
- `request-id` é obrigatório e não vazio: repetição com a mesma referência retorna a execução existente sem novo evento; reutilização com referência diferente falha sem alterar dados. Unicidade é garantida no SQLite, inclusive entre processos concorrentes. Não generaliza idempotência de efeitos externos.
- Contrato de saída e cenários: [aceite independente](acceptance.md), seção Contrato público proposto. Sucesso retorna JSON; erro retorna mensagem em pt-BR no stderr e código diferente de zero. Não mostrar traceback como saída normal.
- Bancos e fixtures desta tarefa ficam dentro do projeto, em diretórios temporários de teste ou caminho local explicitamente informado. Não reutilizar bancos pessoais existentes. Esta é restrição operacional dos comandos e testes deste recorte, não uma proteção implementada pela CLI contra caminhos externos/symlinks. Não alegar sandbox por causa dela.

A CLI é interface técnica interna para este recorte, não a experiência final do iniciante. O banco temporário não será montado para agente algum. No produto, SQLite operacional pertence ao domínio confiável do Core, fora do workspace gravável por agentes; persistir um arquivo nesta etapa não demonstra essa proteção.

## Core modular

Um Core significa autoridade determinística única, não arquivo único. Criar apenas responsabilidades utilizadas agora:

| Arquivo | Responsabilidade |
| --- | --- |
| `udh/domain.py` | Dados da execução, estado inicial e validação |
| `udh/store.py` | SQLite, versão do esquema, transações e SQL parametrizado |
| `udh/service.py` | Criação/consulta e identidade; decisões não duplicadas na CLI |
| `udh/__main__.py` | Argumentos, JSON e códigos de saída |
| `udh/__init__.py` | Pacote sem efeitos colaterais |

CLI sem SQL; domínio sem SQLite. Não criar módulos vazios, plugins ou classes-base para o futuro. Separar responsabilidades quando crescerem, evitando um módulo genérico que acumule todo o sistema.

## Contrato persistente

Versão em PRAGMA user_version. Tabela runs: id (chave primária), request_id (único), spec_id, spec_version, state, created_at UTC. Campos obrigatórios usam NOT NULL; referências e request_id rejeitam texto vazio ou só espaços, preservando os bytes válidos sem normalização silenciosa. Tabela events: sequência inteira, run_id com chave estrangeira, kind RUN_CREATED e created_at UTC; um único RUN_CREATED por execução. Habilitar chaves estrangeiras em cada conexão. O esquema só permite estado PENDING neste recorte.

Criar execução e evento na mesma transação; retornar sucesso somente após commit. Falha em qualquer inserção reverte ambos. Consultar eventos por sequência. Não oferecer atualização, exclusão ou promoção de estado. Integridade transacional não equivale a armazenamento protegido contra agentes nem implementa reconciliação Git/evidências de F-03.

## Critérios e testes complementares

| ID | Critério | Verificação prevista |
| --- | --- | --- |
| C1 | Inicialização repetível e versão desconhecida recusada | Inicializar duas vezes preservando execução; fixtures com versão superior, esquema alheio e versão 1 incompleta conservam esquema, versão e dados após recusa |
| C2 | Execução e evento duráveis | Criar por subprocesso e consultar em outro; conferir UUID, spec, PENDING e único evento |
| C3 | Falha entre inserções é atômica | Trigger de fixture rejeita evento; criação falha e SQL confirma zero registros novos em ambas as tabelas |
| C4 | Entradas inválidas não criam estado | Campos vazios, execução ausente e banco ausente retornam erro; conferir dados e ausência de banco implícito |
| C5 | Referências são dados | Referência com aspas/metacaracteres volta literalmente; tabelas íntegras |
| C6 | Sem promoção/aceite | Comando de promoção recusado; esquema rejeita estado diferente de PENDING |
| C7 | Responsabilidades separadas | Revisar imports e casos de uso: CLI sem SQL, domínio sem persistência |
| C8 | Repetição segura | Repetir request-id em processos separados e concorrentes: um run/evento; payload divergente falha sem alterar o original |

Testes do implementador são complementares; não criar ou modificar AT independentes. Estes testes não certificam aceite oficial do produto.

## Arquivos permitidos após aprovação

Somente os cinco módulos acima, `tests/test_core_state.py`, `tests/__init__.py`, esta proposta e `MEMORY.md`. O documento de cenários é preparado separadamente nesta etapa; testes oficiais e runner não pertencem à lista de escrita do implementador. Bancos temporários não entram no Git. Não tocar em arquivos protegidos do harness nem promover scripts do spike a produto.

## Sequência e pronto

1. Após satisfazer as condições para iniciar e registrar aprovação: domínio e persistência; verificar C1, C3, C5 e C6.
2. Serviço e CLI; verificar C2/C4/C8 em processos distintos e revisar C7.
3. Executar `python3 -m unittest discover -s tests -v` e `git diff --check`; atualizar memória e fazer commit pequeno.

Registrar commit candidato, comandos e resultados da sessão; obter revisão em contexto independente antes de promover o recorte. Conclusão técnica deste recorte exige verificações passando na sessão; não permite declarar ACCEPTED/DELIVERED. Sem tools/harness.py: fast/full não executados. Sem CLI OpenSpec: validação por ela não executada.

## Fora de escopo e próximo passo

A implementação de policy, autorizações, executor confiável, store protegida e isolamento do Core, transições, checkpoints completos, efeitos UNKNOWN, integração Git, agentes, gateway, UI, Memorix, Judge e instalador.

Próximo recorte após esta tarefa: especificar policy e transições autorizadas antes de executar agentes. A proteção e autoria independente dos AT desta tarefa são pré-condições, não algo adiado para depois da implementação. A Slice 1 completa continua Core + SQLite + policy + executor/AT.

## Autorização transitória — 21/10/2026

Após o pedido explícito para aprovar cenários e autorizar procedimento transitório, o usuário respondeu “Prossiga”. Registro: cenários A1–A10 e contrato aprovados para preparação dos primeiros candidatos a AT por contexto independente. A autoria transitória substitui somente a chamada do Core ainda inexistente; não dispensa proteção, selo, linha de base ou executor e não autoriza implementação do produto sem essas pré-condições.

Escopo desta sessão: `bootstrap/slice1/acceptance/` (somente autor independente), `bootstrap/slice1/baseline/` (stub deliberadamente incorreto), `bootstrap/slice1/run_baseline.py` (experimento transitório), `bootstrap/slice1/evidence/`, documentos desta mudança e `MEMORY.md`. Sem alterações em AT oficiais, ferramentas protegidas existentes ou arquivos fora do projeto. Não instalar dependências nem baixar imagem: usar Alpine local identificado por digest e Python existente montado RO. Execução em contêiner rootless sem rede, sem credenciais, com fontes somente leitura e diretório temporário gravável. O runner é descartável e não se torna segundo Core nem executor oficial.

Critério deste preparo: cobertura documentada, candidatos sintaticamente válidos e baseline executável com falhas comportamentais identificadas por cenário. Resultado parcial ou falha de infraestrutura não será apresentado como comprovação da linha de base. A8 permanece revisão independente sobre o futuro candidato, não aprovação automatizada.

## Resultado do preparo transitório

- Autor distinto gerou 10 métodos de teste, cobrindo cenários comportamentais A1–A7/A9/A10, com mapeamento em `bootstrap/slice1/acceptance/coverage.json`. A8 continua revisão sobre futuro código de produto; idioma pt-BR das mensagens exige inspeção humana.
- Baseline executado em Podman rootless, imagem local por digest, `--pull=never`, rede desativada e fontes RO. Python/SQLite existentes funcionaram sem instalação.
- Resultado negativo final: 10 testes executados, 18 falhas de assertiva (incluem subtestes), zero erros de infraestrutura, exit 1 esperado. O stub retorna sucesso sem persistir, aceita banco incompatível e permite ACCEPTED; as falhas detectam esses comportamentos incompletos. Não são testes de produto passando.
- Evidência com hashes dos candidatos/stub e comando: [baseline final](../../../bootstrap/slice1/evidence/baseline.json). Primeira execução preservada em `bootstrap/slice1/evidence/baseline-initial.json`; ResourceWarning de conexões foi corrigido pelo autor independente sem mudar critérios. Revisão independente também levou à validação estrita RFC 3339 e `--pull=never`; evidência regenerada após ajustes.
- Limite: cada método pode parar na primeira falha. O stub não prova que todas as assertivas discriminam cada defeito, especialmente persistência parcial em A4 e normalização/SQL em A6. Não foi criada implementação de referência para simular aceite.
- Na conclusão daquele preparo, estavam ausentes Core de produto, linha de base de produto, store selada, manifesto/at_version aprovado e executor confiável. Fontes RO no contêiner não protegem contra esta sessão com escrita no host; teste e stub compartilham namespace. Nenhum resultado oficial de ACCEPTED/DELIVERED.

Encaminhamento daquele preparo: resolver a fronteira de confiança para armazenar/selar candidatos e executar verificações fora do alcance do implementador antes de iniciar o ticket de produto. Se for necessário desenvolver a primeira fundação como protótipo sem esses mecanismos, isso exige autorização humana explícita; o “Prossiga” deste preparo não foi usado como dispensa de B4.2/B4.6.

Continuação aprovada em 21/10/2026: [fronteira do bootstrap](bootstrap-executor.md) implementada e verificada, T1–T8 PASS, revisão independente registrada. A integração externa e o selo inicial do bootstrap foram concluídos na continuação registrada abaixo; nenhuma dessas provas libera automaticamente produto.

Integração autorizada e concluída em 21/10/2026: [resultado e selo inicial](integration-results.md), I1–I7 PASS com regressão T1–T8 PASS. O selo identifica os testes/componentes do bootstrap; não equivale a ACCEPTED do produto. Não modificar a especificação selada para anexar logs; resultados permanecem no documento próprio.

## Implementação do Core autorizada — 21/10/2026

O usuário solicitou explicitamente implementar Core mínimo + SQLite em domínio, persistência, serviço e CLI. Condições transitórias cumpridas: AT independentes, baseline negativo, transporte externo e selo bootstrap v1. Esta autorização não antecipa policy ou aceite de produto.

Verificação complementar: testes em `tests/` e comando já definido. Verificação comportamental: copiar somente os cinco módulos para snapshot temporário RO, executar os AT do selo v1 no host por meio do bridge selado e gravar a evidência em `openspec/changes/slice1-core-state/core-verification.json`. A revisão independente A8 e seus hashes ficam em `core-review.json` nesta mesma mudança. Esse arquivo documental adicional é necessário para identificar candidato, selo, resultados e auditoria; não contém novos requisitos nem é executável. Temporários de execução permanecem dentro do projeto e são removidos. Nenhum arquivo de AT, selo ou controlador será alterado.

## Resultado do Core mínimo — 21/10/2026

- Implementação identificada pelo commit `b26fd969e20a72b8494b815c57e984b279699714`: cinco módulos, maior com 163 linhas. Domínio sem SQLite, CLI sem SQL, serviço conduz identidade/validação, store mantém versão e transações.
- `init`, `create` e `show` retornam JSON; execuções apenas PENDING. Execução/evento são gravados atomicamente; repetição com mesma referência preserva identidade, timestamps e evento; conflito é recusado. Consulta/criação não inicializam banco ausente.
- Nove testes complementares PASS. Dez métodos de AT do selo v1 PASS sobre snapshot RO dos bytes do commit, via 49 chamadas externas isoladas, sem erros de infraestrutura. Não foram editados AT, selo ou controladores. A8/revisão independente PASS sem pendências.
- Regressão I1–I7 e T1–T8 PASS; selo v1 inalterado. Ajuda CLI em pt-BR, exit 0. A evidência é [core-verification.json](core-verification.json), com hashes, candidato, selo, relatório por método e auditoria; a revisão está em [core-review.json](core-review.json).
- Durante desenvolvimento, um teste complementar demonstrou que normalizar whitespace em todo o DDL confundia literais SQL diferentes; comparação corrigida antes do candidato final. Um caso adicional garante que tabela alheia com prefixo semelhante a sqlite_ não seja ignorada. Nenhum teste foi enfraquecido.
- Retomada: corrigir apenas a fase desatualizada do AGENTS.md, mantendo suas regras; MEMORY.md guarda o estado curto e o próximo passo.

### Como verificar

Rodar `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v` e `python3 -m udh --help`. Para experimentar, usar banco descartável dentro do projeto: `python3 -m udh init --db ./udh-state.sqlite`, depois `python3 -m udh create --db ./udh-state.sqlite --spec-id exemplo --spec-version v1 --request-id primeira`. Consultar com `python3 -m udh show --db ./udh-state.sqlite --run-id UUID_RETORNADO`. Não versionar o banco.

Os AT desta tarefa foram executados com os testes verificados do selo, bridge e launcher já selados, candidato copiado do commit para diretório temporário RO e configuração/auditoria fora do contêiner; a evidência identifica todas as chamadas. `integrate_acceptance.py` continua verificando a integração contra o stub: não confundir esse comando com uma nova execução de AT sobre o Core.

### Limites e próximo passo

Não executados: fast/full (harness oficial ausente) e CLI OpenSpec (ausente). Não implementados: policy, autorizações, transições, execução de agentes, checkpoints completos e reconciliação Git/evidências. Referências de spec continuam opacas, sem comprovar aprovação. Este resultado é IMPLEMENTED/TESTED neste recorte do bootstrap, não ACCEPTED/DELIVERED de produto nem conclusão da Slice 1.

Próximo passo: especificar policy e transições autorizadas de estado antes de permitir execução de agentes. SQLite fica como autoridade operacional; nenhum parecer de LLM promove estados.
