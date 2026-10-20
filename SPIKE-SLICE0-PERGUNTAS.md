# Spike da Slice 0: perguntas, critérios e decisão

**Natureza:** experimento descartável. Nada aqui é código de produto.
**Objetivo:** descobrir, com evidência, se a fundação de isolamento e inferência se sustenta e qual agente entra primeiro.
**Regra de ouro:** cada linha termina com um resultado honesto: **passou**, **falhou**, **parcial** ou **não executado**, mais a evidência (saída, log ou observação). Resultado ruim é informação, não fracasso.

## Preparação: anote antes de começar

- Versão do Windows e do WSL2, e a distribuição Linux usada.
- Runtime de contêiner escolhido e sua versão, e se está rodando sem privilégios de administrador.
- Se o cgroup v2 está disponível e delegado ao usuário.
- Qual agente será testado primeiro e sua versão.
- O mesmo roteiro será repetido num Linux de referência (máquina física, VM ou outra distribuição), para comparar com o WSL2.

## A. Ambiente

| ID | Pergunta | Passou se | Resultado | Evidência |
|---|---|---|---|---|
| A1 | O runtime sobe contêineres sem privilégios de administrador? | Um contêiner trivial roda como usuário comum | | |
| A2 | Os limites de memória, CPU e processos do contêiner são respeitados? | Um consumo acima do limite é interrompido pelo runtime | | |
| A3 | O contêiner enxerga só a pasta do projeto? | Arquivos fora da pasta autorizada não aparecem lá dentro | | |
| A4 | Os discos do Windows (montagens automáticas do WSL2) ficam fora do alcance do agente? | Dentro do contêiner não existe caminho para o disco C: | | |
| A5 | Executáveis do Windows podem ser chamados de dentro do contêiner? | Não podem | | |

## B. Isolamento (matriz negativa: tente fazer o que não deveria dar certo)

| ID | Pergunta | Passou se | Resultado | Evidência |
|---|---|---|---|---|
| B1 | Um arquivo sentinela fora da pasta autorizada pode ser lido? | Não pode | | |
| B2 | Um atalho simbólico dentro da pasta apontando para fora permite ler o que está fora? | Não permite | | |
| B3 | O socket do próprio runtime de contêiner está acessível por dentro? | Não está | | |
| B4 | O contêiner consegue qualquer conexão de rede de saída (nome, IP direto, outras portas)? | Nenhuma conexão sai | | |
| B5 | O agente consegue escrever fora da pasta de trabalho dele? | Não consegue | | |
| B6 | O agente enxerga variáveis de ambiente ou arquivos de credencial do usuário? | Não enxerga | | |

## C. Mediador de inferência

| ID | Pergunta | Passou se | Resultado | Evidência |
|---|---|---|---|---|
| C1 | Com a rede totalmente desligada, o agente consegue falar com o modelo apenas pelo caminho do mediador? | Uma resposta real do modelo chega ao agente | | |
| C2 | O mediador só aceita as rotas e finalidades autorizadas? | Uma rota não autorizada é recusada | | |
| C3 | A resposta em fluxo contínuo (streaming) chega corretamente? | Texto chega em partes, sem travar nem truncar | | |
| C4 | Se o mediador for desligado no meio de uma chamada, o agente falha de forma limpa? | Erro claro, sem ficar pendurado indefinidamente | | |
| C5 | O mediador registra o que passou sem gravar a credencial nos registros? | Nenhuma chave aparece nos logs | | |

## D. Autenticação (a pergunta mais arriscada)

| ID | Pergunta | Passou se | Resultado | Evidência |
|---|---|---|---|---|
| D1 | O agente aceita apontar para um endereço de inferência customizado? | Aceita e funciona | | |
| D2 | Funciona sem a chave real dentro do contêiner (o mediador injeta a credencial por fora)? | Funciona; nada sensível dentro do contêiner | | |
| D3 | Funciona para quem usa login por assinatura, e não chave de API? | Funciona, ou fica registrado claramente que não funciona | | |
| D4 | Se não funciona, qual é a alternativa mínima para o iniciante? | Alternativa descrita (ex.: só chave de API para esse agente) | | |

## E. Controle do próprio agente

| ID | Pergunta | Passou se | Resultado | Evidência |
|---|---|---|---|---|
| E1 | Busca web nativa do agente pode ser desligada ou fica bloqueada pela rede? | Não consegue sair | | |
| E2 | Telemetria e autoatualização podem ser desligadas ou ficam bloqueadas? | Não conseguem sair | | |
| E3 | Ferramentas e servidores externos embutidos do agente podem ser desativados? | Ficam desativados | | |
| E4 | O agente consegue alterar a própria configuração de permissões? | Não consegue | | |

## F. Evidência e honestidade

| ID | Pergunta | Passou se | Resultado | Evidência |
|---|---|---|---|---|
| F1 | Um relatório de sucesso escrito pelo agente é distinguível do resultado coletado por código confiável? | O resultado só vale quando vem da coleta externa | | |
| F2 | Se o agente tentar alterar um arquivo protegido (simulando um teste de aceite), isso é detectado? | A alteração é detectada e rejeitada | | |
| F3 | Se o processo for morto no meio de uma gravação, é possível saber onde parou? | O estado depois do corte é identificável | | |

## G. Comparação entre ambientes

| ID | Pergunta | Passou se | Resultado | Evidência |
|---|---|---|---|---|
| G1 | Cada item acima deu o mesmo resultado no WSL2 e no Linux de referência? | Mesmo resultado, ou a diferença está explicada | | |
| G2 | Existe algo que só falha no WSL2? | Registrado com a causa provável | | |

## Como decidir o que fazer com os resultados

- **Tudo ou quase tudo passou, com pelo menos um agente:** siga para a Slice 1 com esse agente como primeiro.
- **O isolamento (grupo B) falhou de forma que não dá para corrigir com configuração:** pare. Isso é um problema de fundação, e vale reabrir o desenho nesse ponto, com a evidência na mão.
- **A autenticação (grupo D) falhou para o login por assinatura:** não é um bloqueio. Registre que esse agente só serve a quem usa chave de API e decida qual caminho oferecer ao iniciante.
- **Só o WSL2 falhou:** registre o que é preciso mudar no ambiente e o que o `doctor` (a verificação de instalação) precisará checar. Não descarte o WSL2 sem entender a causa.
- **Um agente falhou em E ou D, outro passou:** escolha o que passou como primeiro e deixe o outro para depois.
- **Itens "não executado":** não contam como passou. Resolva ou justifique antes de seguir.

## Saída esperada do spike

Uma página com: a tabela preenchida, o agente escolhido como primeiro e por quê, os riscos que sobraram, e as mudanças de desenho propostas, **se houver**, cada uma ligada a uma linha da tabela como evidência.
