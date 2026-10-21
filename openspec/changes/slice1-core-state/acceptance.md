# Slice 1 — cenários de aceite da tarefa 1

Estado: proposta documental em contexto separado do autor da implementação; **pendente de aprovação humana**. Não contém AT executáveis, selo, execução oficial ou autorização de implementação. Complementa os critérios C1–C8 da [proposta](proposal.md); não amplia a Slice 1 nem presume exceção de bootstrap. Referências: arquitetura v2.5.1, B4 e F-02.

## Contrato público proposto para aprovação

Para impedir que o futuro autor dos AT escolha a semântica, propõe-se o seguinte contrato mínimo, a incorporar à proposta aprovada:

- Sucesso: código 0, stdout com um único objeto JSON UTF-8, stderr vazio. Espaçamento e ordem de propriedades não importam.
- `init`: objeto com `schema_version`, inteiro 1; tanto na primeira chamada quanto na repetição.
- `create` e `show`: objeto com `run_id`, `request_id`, `spec_id`, `spec_version`, `state`, `created_at` e `events`. Os cinco primeiros campos são strings; `state` é `PENDING`; `created_at` é timestamp UTC RFC 3339 terminado em `Z`. `run_id` é UUID válido. `events` é uma lista com exatamente um objeto contendo `sequence` (inteiro positivo), `kind` (`RUN_CREATED`) e `created_at` (mesmo formato UTC). Não se exige que a sequência comece em 1 em cada execução, nem que ambos os timestamps sejam idênticos.
- A repetição idempotente e a consulta devolvem os mesmos valores persistidos, inclusive timestamps e sequência; não geram novos identificadores.
- Erro: código diferente de zero, stdout vazio, mensagem não vazia em pt-BR no stderr, sem traceback. Não se exige texto literal nem código numérico específico nesta tarefa.
- IDs de especificação, versão e requisição vazios ou compostos apenas de espaços são inválidos. Valores válidos conservam literalmente seu conteúdo, inclusive espaços nas extremidades. Não há normalização silenciosa.

Essas escolhas ainda não são requisitos aprovados. Se rejeitadas, ajustar contrato e cenários antes da geração de AT. Persistência, restrições de esquema e módulos seguem a proposta; JSON não expõe nomes de tabelas.

## Cenários para aprovação

Cada cenário parte de diretório temporário dentro do projeto, banco exclusivo e processos CLI independentes. Nenhum banco pessoal ou credencial é usado.

| Cenário / critério | Ação e exemplo concreto | Resultado observável esperado |
| --- | --- | --- |
| A1 / C1 | Inicializar banco novo; criar execução com spec `slice1`, versão `v1` e requisição `req-1`; inicializar novamente e consultar a execução. | Ambas as inicializações informam versão 1. A consulta conserva integralmente a execução e seu único evento. |
| A2 / C1 | Tentar inicializar fixtures separadas: versão superior a 1, banco com tabela alheia, versão 1 com esquema incompleto. | Cada chamada falha. Esquema, versão e dados da fixture permanecem iguais ao estado anterior; nenhuma migração é aplicada. |
| A3 / C2 | Criar `slice1`/`v1`/`req-1`, encerrar o processo e consultar o UUID retornado em novo processo. | UUID válido, referência literal, `PENDING`, timestamp UTC e exatamente um `RUN_CREATED`; consulta reproduz os valores retornados na criação. |
| A4 / C3 | Em banco inicializado, fixture rejeita a inserção do evento depois da tentativa de inserir a execução; chamar `create`. | Erro sem sucesso parcial. Inspeção independente confirma que nenhuma das duas tabelas ganhou registro; execuções anteriores permanecem intactas. |
| A5 / C4 | Para cada campo obrigatório, tentar string vazia e apenas espaços; consultar UUID desconhecido; criar e consultar usando caminhos de banco inexistentes. | Todas as chamadas falham, sem criar execução/evento. Caminhos inexistentes continuam sem arquivo de banco. Banco inicializado conserva dados anteriores. |
| A6 / C5 | Criar e consultar referência `spec'); DROP TABLE runs; --`, versão ` v1 ` e requisição `req-'1`. | Strings voltam literalmente no JSON; criação e consulta posteriores de outra execução funcionam e os dados anteriores permanecem. |
| A7 / C6 | Tentar comando de promoção; inspecionar a restrição persistente tentando inserir estado `ACCEPTED` em fixture. | CLI recusa comando desconhecido; SQLite recusa estado distinto de `PENDING`; nenhuma promoção é registrada. |
| A8 / C7 | Revisar snapshot identificável dos cinco módulos previstos e seus usos reais. | CLI trata argumentos e apresentação sem SQL; domínio não depende de SQLite; serviço concentra o caso de uso; store trata persistência. Não existem módulos vazios de antecipação. |
| A9 / C8 | Criar duas vezes, em processos distintos, `req-1` com a mesma referência. Depois repetir `req-1` com versão `v2`. | As duas primeiras respostas são iguais; existe uma execução e um evento. A terceira falha e conserva integralmente o registro original. |
| A10 / C8 | Disparar duas criações concorrentes com mesmo request-id e mesma referência; repetir em banco exclusivo com referências divergentes. | No primeiro caso ambas têm sucesso e retornam a mesma execução, sem duplicação. No segundo, uma vence e outra falha; a referência vencedora permanece com exatamente um evento. A ordem da vencedora não é predeterminada. |

## Exceções à caixa-preta e revisão

A2, A4 e a parte persistente de A7 exigem preparação/inspeção SQLite: pela CLI sozinha não se prova atomicidade entre inserções nem restrições do esquema. A1/A9/A10 podem complementar observação pública com contagem independente para detectar registros ocultos. Essas exceções precisam ser documentadas e aprovadas no perfil de stack conforme B4.3; o perfil/proteção ainda não existem. Não se presume acesso gravável do implementador aos futuros AT.

A8 é revisão estrutural, não resultado comportamental CLI. Conforme F-02, deve ocorrer em contexto separado da implementação, sobre commit/digest, spec e evidências identificados, com achados e resolução registrados. O presente documento não executa essa revisão nem substitui VERIFY.

## Protocolo futuro dos AT — ainda não implementado

1. Aprovação humana dos cenários e do contrato; registro do mapeamento C1–C8 → cenários → AT. Critério sem AT bloqueia início do ticket de produto (B4.2); nenhuma exceção está aprovada aqui.
2. Geração dos AT pelo Core em contexto distinto do implementador, conforme B4.4, sem agente permanente adicional. Este documento é preparação independente, não demonstra essa chamada do Core.
3. Identificação de stub como linha de base do primeiro ticket. AT de comportamento novo devem falhar nele por ausência do comportamento esperado; falha apenas de infraestrutura não demonstra discriminação. `check` documental/estrutural é isento da linha de base negativa, conforme B4.5; registrar a classificação e cobertura de cada critério.
4. Antes do selo: conferir cobertura e linha de base; registrar SHA-256 por arquivo, manifesto, `at_version` imutável encadeada por hash e aprovação. Manter AT e registros em store confiável fora do workspace gravável pelo agente. Este arquivo versionado em Git não constitui essa store ou selo.
5. Executor confiável executa AT da store sobre commit limpo; runner, configuração e versões protegidos. Registrar candidato, versões e resultados oficiais. IT do implementador permanecem complementares; não concedem `ACCEPTED`.
6. Alterações seguem B4.8; mudança de versão de AT ou digest do artefato invalida evidência anterior conforme B4.9. Correção de teste não é autorização para enfraquecer cenário. Nenhum desses mecanismos é declarado existente neste recorte documental.

## Decisões necessárias antes do handoff

- Aprovar ou ajustar o contrato JSON acima, sobretudo formato UTC, saída idempotente e resultados das duas chamadas concorrentes em A10; sincronizar a proposta antes de gerar AT.
- Aprovar cenários e documentar as exceções no perfil de stack. Atomicidade e revisão modular não podem ser certificadas apenas por saída CLI.
- Definir e prover o caminho confiável para autoria, proteção, selo e execução dos AT. Enquanto isso não existir, a preparação documental não libera implementação nem constitui aceite.

Há um impedimento de bootstrap concreto: o Core que deveria gerar/selar AT ainda não existe. O próximo passo operacional deve propor uma autorização transitória e limitada para preparação confiável dos primeiros AT, ou uma exceção explícita de protótipo; nenhuma é presumida. Não instalar ferramentas, escrever fora do projeto ou construir um segundo Core para contornar esse impedimento.
