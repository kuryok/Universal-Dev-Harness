# Slice 1 — tarefa 1: estado persistente mínimo

Estado: aguardando aprovação humana antes de implementar.

## Objetivo

Criar e consultar uma execução local em SQLite, preservando identidade e evento inicial após reabrir o processo. Esta tarefa inicia a Slice 1; não entrega a Slice 1 inteira.

Referência: arquitetura consolidada v2.5.1, ADR-011 e F-03, arquivo local `UDH-v2.5-ARQUITETURA-CONSOLIDADA-v2.5.1.md`. Não ratifica ADR-018 A nem incorpora alterações preexistentes ao Git.

## Escopo proposto

Python 3 e biblioteca padrão (sqlite3, argparse, unittest), sem dependências, rede ou credenciais. Nesta máquina usar `python3`.

- `python3 -m udh init --db CAMINHO`: inicializar esquema versão 1; repetição preserva dados. Versão desconhecida é recusada sem migração automática.
- `python3 -m udh create --db CAMINHO --spec-id ID --spec-version V`: criar UUID, referência não vazia à especificação e estado PENDING. Referenciar uma spec não comprova sua aprovação; nenhuma execução de trabalho será permitida nesta tarefa.
- `python3 -m udh show --db CAMINHO --run-id UUID`: consultar execução e seu evento inicial. Banco inexistente e execução desconhecida falham; consulta/criação não inicializam banco implicitamente.
- Sucesso retorna JSON; erro retorna mensagem em pt-BR no stderr e código diferente de zero. Não mostrar traceback como saída normal.
- Bancos e fixtures desta tarefa ficam dentro do projeto, em diretórios temporários de teste ou caminho local explicitamente informado. Não reutilizar bancos pessoais existentes.

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

Versão em PRAGMA user_version. Tabela runs: id, spec_id, spec_version, state, created_at UTC. Tabela events: sequência inteira, run_id com chave estrangeira, kind RUN_CREATED e created_at UTC. Habilitar chaves estrangeiras em cada conexão. O esquema só permite estado PENDING neste recorte.

Criar execução e evento na mesma transação; retornar sucesso somente após commit. Falha em qualquer inserção reverte ambos. Consultar eventos por sequência. Não oferecer atualização, exclusão ou promoção de estado. Integridade transacional não equivale a armazenamento protegido contra agentes nem implementa reconciliação Git/evidências de F-03.

## Critérios e testes complementares

| ID | Critério | Verificação prevista |
| --- | --- | --- |
| C1 | Inicialização repetível e versão desconhecida recusada | Inicializar duas vezes preservando execução; fixture com versão superior mantém versão/dados após recusa |
| C2 | Execução e evento duráveis | Criar por subprocesso e consultar em outro; conferir UUID, spec, PENDING e único evento |
| C3 | Falha entre inserções é atômica | Trigger de fixture rejeita evento; criação falha e SQL confirma zero registros novos em ambas as tabelas |
| C4 | Entradas inválidas não criam estado | Campos vazios, execução ausente e banco ausente retornam erro; conferir dados e ausência de banco implícito |
| C5 | Referências são dados | Referência com aspas/metacaracteres volta literalmente; tabelas íntegras |
| C6 | Sem promoção/aceite | Comando de promoção recusado; esquema rejeita estado diferente de PENDING |
| C7 | Responsabilidades separadas | Revisar imports e casos de uso: CLI sem SQL, domínio sem persistência |

Testes do implementador são complementares; não criar ou modificar AT independentes. Estes testes não certificam aceite oficial do produto.

## Arquivos permitidos após aprovação

Somente os cinco módulos acima, `tests/test_core_state.py`, `tests/__init__.py`, esta proposta e `MEMORY.md`. Bancos temporários não entram no Git. Não tocar em arquivos protegidos do harness nem promover scripts do spike a produto.

## Sequência e pronto

1. Após aprovação: domínio e persistência; verificar C1, C3, C5 e C6.
2. Serviço e CLI; verificar C2/C4 em processos distintos e revisar C7.
3. Executar `python3 -m unittest discover -s tests -v` e `git diff --check`; atualizar memória e fazer commit pequeno.

Conclusão exige verificações passando na sessão; não permite declarar ACCEPTED/DELIVERED. Sem tools/harness.py: fast/full não executados. Sem CLI OpenSpec: validação por ela não executada.

## Fora de escopo e próximo passo

Policy, autorizações, executor confiável, AT protegidos, isolamento do Core, transições, checkpoints completos, efeitos UNKNOWN, integração Git, agentes, gateway, UI, Memorix, Judge e instalador.

Próximo recorte: especificar policy e transições autorizadas, fronteira de confiança e AT independentes antes de executar agentes. A Slice 1 completa continua Core + SQLite + policy + executor/AT.
