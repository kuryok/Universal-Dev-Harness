# Spike da Slice 0: perguntas, critérios e decisão

**Natureza:** experimento descartável. Nada aqui é código de produto.
**Objetivo:** descobrir, com evidência, se a fundação de isolamento e inferência se sustenta e qual agente entra primeiro.
**Regra de ouro:** cada linha termina com um resultado honesto: **passou**, **falhou**, **parcial** ou **não executado**, mais a evidência (saída, log ou observação). Resultado ruim é informação, não fracasso.

## Preparação: anote antes de começar

- Ambiente: Debian GNU/Linux 13 (trixie), versão completa 13.7; Linux 6.12.111+deb13-amd64, x86_64. Usuário: `debian` (UID/GID 1000). Evidência: `/etc/os-release`, `uname -srmo`, `id`.
- Windows/WSL2: não aplicável neste ambiente; este ambiente é uma VM Linux, conforme informado pelo usuário nesta sessão. Não foi testada máquina física; os resultados se restringem a esta VM.
- Atualização em 21/10/2026: Podman 5.4.2, rootless confirmado por `podman info` e execução com UID/GID 1000 no contêiner. O erro anterior de filesystem somente leitura em `/run/user/1000/libpod` não se repetiu após a mudança de permissões da sessão. Imagem local Alpine 3.20, digest `sha256:d9e853e87e55526f6b2917df91a2115c36dd7c696a35be12163d44e6e2a4b6bc`; nenhum download. Evidência: `spike/slice0/evidence/results.json`, registros `runtime`, `image`, `A1`.
- cgroup v2: disponível (filesystem `cgroup2fs`; `/proc/self/cgroup` registra hierarquia unificada `0::`). Delegação: evidência de escopo delegado ao usuário em `/sys/fs/cgroup/user.slice/user-1000.slice/user@1000.service`: `cgroup.controllers` contém `cpu memory pids`; `cgroup.subtree_control` e `cgroup.procs` pertencem a `debian`. Isso confirma delegação nesse escopo, não valida comportamento de um runtime.
- User namespaces: habilitados pelos valores observados (`user.max_user_namespaces=2147483647`; `kernel.unprivileged_userns_clone=1`).
- SubUID/subGID: `/etc/subuid` contém `debian:100000:65536`; `/etc/subgid` contém `debian:100000:65536`.
- Agente e versão: não selecionados; não testados.
- Comparação com WSL2: será feita depois, em outra máquina, conforme o contexto desta tarefa.

## A. Ambiente

| ID | Pergunta | Passou se | Resultado | Evidência |
|---|---|---|---|---|
| A1 | O runtime sobe contêineres sem privilégios de administrador? | Um contêiner trivial roda como usuário comum | passou | `A1`: exit 0, UID/GID 1000; runtime rootless; capabilities efetivas zeradas, NoNewPrivs=1 e seccomp ativo. |
| A2 | Os limites de memória, CPU e processos do contêiner são respeitados? | Um consumo acima do limite é interrompido pelo runtime | passou | Memória 64 MiB: exit 137 e OOMKilled=true. Processos: pids.max=32 e fork recusado, exit 2. CPU: cpu.max=25000/100000; sob carga, nr_throttled passou de 0 para 21 e usage_usec de 3178 para 522077 em cerca de 2 s. CPU limita taxa, não encerra processo. Registros `A2-*` e respectivas inspeções. |
| A3 | O contêiner enxerga só a pasta do projeto? | Arquivos fora da pasta autorizada não aparecem lá dentro | parcial | Único bind de dados de projeto: workspace sintético em /workspace; sentinela externa inacessível. Imagem, /proc, /sys, dispositivos e arquivos geridos pelo runtime também são visíveis. Não interpretar o critério literalmente como ausência de qualquer arquivo externo; ver registro combinado e mounts. |
| A4 | Os discos do Windows (montagens automáticas do WSL2) ficam fora do alcance do agente? | Dentro do contêiner não existe caminho para o disco C: | não executado | Linux nativo; ausência de discos Windows neste ambiente não demonstra isolamento no WSL2. |
| A5 | Executáveis do Windows podem ser chamados de dentro do contêiner? | Não podem | não executado | Linux nativo; interoperabilidade Windows/WSL2 não exercitada. |

## B. Isolamento (matriz negativa: tente fazer o que não deveria dar certo)

| ID | Pergunta | Passou se | Resultado | Evidência |
|---|---|---|---|---|
| B1 | Um arquivo sentinela fora da pasta autorizada pode ser lido? | Não pode | passou | Sentinela sintética existente no host fora do bind não aparece no contêiner. Registro `A3-B1-B2-B3-B5-B6`, exit 0. |
| B2 | Um atalho simbólico dentro da pasta apontando para fora permite ler o que está fora? | Não permite | passou | Symlink no workspace aponta para sentinela externa existente no host e fica pendente dentro do contêiner; registro combinado, exit 0. |
| B3 | O socket do próprio runtime de contêiner está acessível por dentro? | Não está | passou | Socket do Podman existe no host segundo runtime; caminhos /run/user/1000/podman/podman.sock, /run/podman/podman.sock e /var/run/docker.sock ausentes dentro. Inspeção não mostra bind desses sockets. |
| B4 | O contêiner consegue qualquer conexão de rede de saída (nome, IP direto, outras portas)? | Nenhuma conexão sai | parcial | network=none, sem rota IPv4 externa; DNS recusado e TCP para 1.1.1.1 e 192.0.2.1 nas portas 53/80/443/8443 sem conexão. Não houve ensaio ativo UDP/IPv6 nem captura externa; não afirmar cobertura de qualquer conexão. Registro `B4`, exit 0. |
| B5 | O agente consegue escrever fora da pasta de trabalho dele? | Não consegue | parcial | Escrita em /workspace permitida; arquivos comuns em /, /tmp, /home e /etc recusados, raiz somente leitura e tmpfs automáticos desativados. /proc e dispositivos especiais existem; não foi provada impossibilidade de toda escrita fora do workspace. |
| B6 | O agente enxerga variáveis de ambiente ou arquivos de credencial do usuário? | Não enxerga | parcial | Variável sintética presente no ambiente do processo chamador não chegou ao contêiner. Não há bind do home do host. Credenciais reais não foram lidas; fixture de arquivo de credencial sintético e inspeção exaustiva de canais não executadas. |

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

## Evidências desta rodada — 21/10/2026

- Roteiro executável: `spike/slice0/run.py`. Saídas, argumentos, códigos de retorno e inspeções: `spike/slice0/evidence/results.json`.
- Reprodução: `python3 spike/slice0/run.py`, com Podman rootless utilizável e a mesma imagem já disponível localmente. Não baixa imagem (`--pull=never`). Usa nomes de contêiner prefixados com `udh-slice0-`; executar sem concorrência e sem contêineres preexistentes com esses nomes.
- Ambiente: VM informada pelo usuário; restrições da sessão/VM não serão atribuídas à máquina física sem reprodução.
- Escopo: contêiner Alpine com comandos sintéticos; nenhum CLI de agente foi executado. Os resultados não certificam agente nem produto.
- As evidências foram coletadas pelo processo deste experimento, fora do contêiner, mas ficam editáveis no repositório. Não constituem a store protegida ou o executor confiável do produto; F1–F3 continuam sem execução.
- Grupos C–G não executados nesta rodada. Nenhum agente selecionado; a Slice 0 permanece parcial, sem decisão de avançar para Slice 1.
- Não existe `tools/harness.py` ou suíte de gates configurada. Compilação sintática do script executada com sucesso. CLI OpenSpec ausente: validação OpenSpec não executada. Revisão independente não executada.
- Preservadas as mudanças preexistentes: exclusão do baseline anterior e arquivos de arquitetura/resultados já presentes no início da sessão.
