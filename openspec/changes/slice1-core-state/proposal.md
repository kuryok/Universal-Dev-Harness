# Slice 1 — tarefa 1: estado persistente mínimo

Estado: revisada em 21/10/2026; aguardando aprovação humana. Handoff para implementação bloqueado pelas condições abaixo.

## Objetivo

Criar e consultar uma execução local em SQLite, preservando identidade e evento inicial após reabrir o processo. Esta tarefa inicia a Slice 1; não entrega a Slice 1 inteira.

Referência: arquitetura consolidada v2.5.1, ADR-011 e F-03, arquivo local `UDH-v2.5-ARQUITETURA-CONSOLIDADA-v2.5.1.md`. Não ratifica ADR-018 A nem incorpora alterações preexistentes ao Git.

## Condições para iniciar

B4.2 exige cenários aprovados e AT independentes antes de iniciar ticket de produto. A rota proposta é cumprir esse contrato, sem presumir exceção de protótipo. Os [cenários independentes](acceptance.md) são a entrada de aceite e aguardam aprovação humana; não são AT executáveis nem um selo de aceite.

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
