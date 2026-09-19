# A Ponte: um agente A2A com MCP por dentro

*Onde termina a profundidade e começa o alcance*

**Projeto:** MBA Engenharia de Software com IA, curso de MCP e A2A
**Fase do projeto:** Desafio inicial

## Descrição

Neste desafio você vai construir as duas pontas da fronteira que o curso inteiro vem desenhando: um servidor MCP que expõe as capacidades de um domínio, e um agente que consome esse servidor por dentro (como host MCP) e se oferece ao mundo por fora (como servidor A2A).

O que torna isto um desafio de arquitetura, e não um exercício de API, é o estado. Nenhum dos dois protocolos tem sessão. O MCP resolve isso com o `requestState` do MRTR, devolvido ao cliente e reenviado no retry. O A2A resolve com a Task, que tem identidade, estado e produto. No meio do seu agente, esses dois mecanismos precisam se encontrar: quando o servidor MCP responde que precisa de mais informação, o seu agente não pode responder por conta própria nem travar esperando. Ele precisa interromper a Task, devolver a pergunta ao cliente A2A, guardar o `requestState` ligado àquela Task e, quando a resposta chegar, repetir o request MCP original com um id novo.

Essa costura é a ponte que dá nome ao desafio. É ela que você vai ser avaliado por construir.

Cenário: a Hill Valley Tech tem cinco salas de reunião e uma planilha compartilhada que ninguém respeita. O time decidiu expor as salas como capacidade de agente, para que qualquer assistente da empresa consiga reservar sem abrir a planilha. A decisão de arquitetura já foi tomada e não está em discussão: as ferramentas de sala viram um servidor MCP, e a capacidade de reservar vira um agente A2A que qualquer outro agente da empresa possa descobrir e chamar. Falta construir.

## Sobre o foco do desafio

O foco é protocolo. O domínio é deliberadamente bobo: cinco salas, uma lista de reservas e três regras de uso, tudo entregue pronto no starter em arquivos JSON. Você não vai gastar tempo modelando negócio, e não deve. Se a sua entrega tem um ORM, um banco e uma camada de serviços, você trabalhou no lugar errado.

O agente também não usa LLM. Ele interpreta um pedido em formato fixo e decide por regra. Isso é intencional por dois motivos: mantém a correção determinística, e prova na prática a tese da opacidade do A2A, porque quem chama o seu agente não tem como saber se por trás dele existe um modelo, um grafo ou um `if`.

Duas fricções propositais estão plantadas no contrato, e cada uma tem uma pista.

A primeira: no transporte stateless não existe canal de volta. Se você tentar fazer o servidor chamar o cliente no meio da execução da tool, vai receber um erro dizendo exatamente isso. Pista: o servidor não inicia request, ele termina a resposta pedindo informação. Procure como o seu SDK expressa MRTR, e não como ele expressa uma pergunta síncrona ao usuário.

A segunda: o servidor MCP não pode mandar uma elicitation para um cliente que não declarou essa capability. Pista: a capability viaja em `io.modelcontextprotocol/clientCapabilities`, dentro do `_meta` de cada request, e a spec define um código de erro específico para quando ela falta. Repare que o que se declara é a elicitation em form mode, não elicitation em geral.

A terceira: o `requestState` volta pelas mãos do cliente, então ele é entrada controlada por atacante. Pista: a spec manda proteger a integridade dele e rejeitar o que não passar na verificação, e os dois SDKs oferecem um utilitário pronto para isso. Um `requestState` que é só um JSON em base64, sem assinatura, atende ao caminho feliz e falha na verificação do avaliador. O conteúdo pode ser legível, o que não pode é ser adulterável.

## Objetivo

Entregar, em um repositório público no GitHub (fork do starter), os dois processos funcionando e verificáveis:

- Um servidor MCP em Streamable HTTP, com três tools, um resource, os dois tipos de erro e o ciclo completo de MRTR na tool de reserva
- Um agente que é host MCP por dentro, descobrindo e chamando as tools do seu próprio servidor
- O mesmo agente como servidor A2A por fora, com Agent Card publicado, `SendMessage` e `GetTask`, e Task passando por `TASK_STATE_INPUT_REQUIRED`
- A propagação do `traceparent` do cliente A2A até o servidor MCP, registrada em log
- Um `README.md` com os comandos de subida e a explicação de onde a ponte acontece
- A execução do validador do starter passando em todas as verificações

## Repositório base

https://github.com/devfullcycle/desafio-a2a-com-mcp

Faça o fork e entregue tudo na branch `main` do seu fork.

O starter não tem código de aplicação para você estender: ele tem os dados do domínio, a política de uso, os exemplos de wire dos dois protocolos e o validador que o avaliador vai rodar contra a sua entrega. As pastas `servidor-mcp/` e `agente/` chegam vazias, com um `.gitkeep`. Esse vácuo é proposital.

O validador roda com Python 3.10 ou superior e não tem dependência externa:

```bash
python3 validador/validar.py --agente http://localhost:7300 --mcp http://localhost:7301
```

## Contexto

### O que o starter entrega

`dados/salas.json`, a lista fixa de salas, cada uma com `id`, `nome`, `capacidade` e `recursos`:

```json
[
  { "id": "sala-aquario",  "nome": "Aquario",  "capacidade": 4,  "recursos": ["tv"] },
  { "id": "sala-porao",    "nome": "Porao",    "capacidade": 6,  "recursos": ["quadro"] },
  { "id": "sala-garagem",  "nome": "Garagem",  "capacidade": 12, "recursos": ["tv", "quadro"] },
  { "id": "sala-fusca",    "nome": "Fusca",    "capacidade": 12, "recursos": ["tv"] },
  { "id": "sala-mirante",  "nome": "Mirante",  "capacidade": 20, "recursos": ["tv", "quadro", "camera"] }
]
```

`dados/reservas.json`, as reservas que já existem quando o processo sobe:

```json
[
  { "id": "res-0001", "sala": "sala-garagem", "inicio": "2026-11-03T14:00:00-03:00", "fim": "2026-11-03T15:00:00-03:00", "responsavel": "Marty" },
  { "id": "res-0002", "sala": "sala-fusca",   "inicio": "2026-11-03T16:00:00-03:00", "fim": "2026-11-03T17:00:00-03:00", "responsavel": "Jennifer" }
]
```

`dados/politica-de-uso.md`, o texto que o resource do seu servidor vai expor. A primeira linha declara a versão, e esse valor precisa aparecer no artifact que o agente devolve:

```
versao: 2026-11-01

- Reservas somente entre 08:00 e 20:00, horario de Sao Paulo (-03:00).
- Duracao maxima de 2 horas por reserva.
- Uma sala nao pode ter duas reservas sobrepostas.
```

`validador/validar.py`, o cliente de conformidade. São 36 verificações: as 20 primeiras falam HTTP direto com o seu servidor MCP, as 16 seguintes falam A2A com o seu agente. Cada uma imprime PASS ou FAIL com o motivo, e o processo termina com código de saída 0 somente se todas passarem. Ele imprime, na primeira linha, o trace-id que vai usar no header `traceparent`.

`exemplos/wire/`, os JSON de request e response de cada passo dos dois protocolos, na forma exata que o validador envia e espera.

### Portas

O validador assume o agente em `7300`, com o endpoint JSON-RPC em `/a2a` e o card em `/.well-known/agent-card.json`, e o servidor MCP em `7301`, com o endpoint Streamable HTTP em `/mcp`. Você pode parametrizar por variável de ambiente, desde que os valores padrão sejam esses.

## Tecnologias obrigatórias

- SDK oficial do MCP na v2, alinhado à revisão `2026-07-28` da spec: `@modelcontextprotocol/server` (Node.js 20 ou superior, ESM) ou o pacote `mcp` do Python (3.10 ou superior). A escolha da linguagem é sua e não influencia a nota
- A2A v1.0. Binding JSON-RPC 2.0 sobre HTTP
- Python 3.10 ou superior para executar o validador
- Versões travadas no `package.json` ou no `pyproject.toml`

É proibido alterar `dados/`, `validador/` e `exemplos/`. O avaliador roda o validador a partir do repositório original, então alterar esses arquivos no fork não muda nada além de invalidar a sua entrega.

É proibido usar LLM no caminho de execução do agente. Dado o mesmo pedido, o agente deve produzir sempre o mesmo resultado.

As duas stacks foram verificadas contra este enunciado e ambas dão conta dele, por caminhos diferentes. Onde os SDKs divergem entre si, ou divergem da letra da spec, os critérios aceitam as duas formas, e isso está dito no ponto em que aparece. Não tente forçar o seu SDK a imitar o outro.

## Comportamento esperado

O fluxo completo, de ponta a ponta:

1. Um cliente A2A busca o Agent Card do seu agente, descobre a skill de reserva e envia um `SendMessage` com o pedido em texto.
2. O agente abre uma Task, chama o seu servidor MCP e, no caminho em que a sala pedida está livre, conclui a Task com um artifact descrevendo a reserva criada.
3. Quando a sala pedida está ocupada no horário, o servidor MCP responde `input_required`, com uma elicitation pedindo a escolha entre as salas alternativas e um `requestState` opaco.
4. O agente transforma isso em pausa: a Task vai para `TASK_STATE_INPUT_REQUIRED` com a lista de alternativas, e o `requestState` fica guardado ligado àquela Task.
5. O cliente responde com um novo `SendMessage` referenciando a mesma Task. O agente repete o `tools/call` original com um id de JSON-RPC novo, levando `inputResponses` e o `requestState`, e o servidor conclui a reserva.
6. A Task termina em `TASK_STATE_COMPLETED`, com o artifact da reserva. Se o cliente recusar as alternativas, a Task termina em `TASK_STATE_CANCELED`.

Decisões de produto já fechadas, para não haver interpretação criativa:

- O pedido chega em formato fixo, não em linguagem natural: `reservar sala=<id> inicio=<iso8601> fim=<iso8601> responsavel=<nome>`
- A resposta à pausa também é fixa: `escolha=<id da sala>` para aceitar, ou `escolha=recusar` para recusar
- As alternativas oferecidas são as salas livres naquele intervalo com capacidade igual ou maior que a da sala pedida, no máximo três, ordenadas por capacidade crescente e, em empate, por `id` em ordem alfabética
- Nenhuma regra depende da data de hoje. Reservar no passado é permitido
- A persistência de reservas pode ser em memória. Reservas criadas durante a execução precisam ser visíveis para as consultas seguintes do mesmo processo, e não precisam sobreviver a um restart. O `requestState` é a exceção: ele precisa continuar válido depois de um restart, porque o estado viaja nele e não no servidor

## Regras de negócio e semântica de erros

As mensagens abaixo são a fonte única de verdade. O validador exige que o texto exato apareça no conteúdo do resultado. Vários SDKs prefixam a mensagem com algo como `Error executing tool <nome>:`, e isso é aceito: o que não pode é a mensagem ser diferente.

Erros de execução da tool, devolvidos com `isError: true` dentro de um resultado `complete`:

- Sala que não existe em `dados/salas.json`: `Sala inexistente: <id informado>`
- Início fora da janela de uso, ou fim fora dela: `Fora da janela de uso: a politica permite reservas entre 08:00 e 20:00`
- Intervalo com mais de duas horas: `Duracao acima do limite: a politica permite no maximo 2 horas`
- Intervalo invertido ou vazio, com `fim` menor ou igual a `inicio`: `Intervalo invalido: fim deve ser posterior a inicio`
- Conflito de horário em que nenhuma sala alternativa atende à regra de alternativas: `Sem alternativas disponiveis no intervalo`

Erros de protocolo, devolvidos como `error` de JSON-RPC:

- Tool que não existe: a chamada precisa ser recusada. A spec trata isso como erro de protocolo `-32602`, mas alguns SDKs devolvem erro de execução com `isError: true`; os dois são aceitos
- Request sem `io.modelcontextprotocol/protocolVersion` ou sem `io.modelcontextprotocol/clientCapabilities` no `_meta`: `-32602`, e a resposta HTTP é `400`
- Request que exige elicitation vindo de cliente que não declarou a capability de elicitation em form mode: `-32021`, com `data.requiredCapabilities` listando o que faltou, e resposta HTTP `400`
- `requestState` que falha na verificação de integridade ou que expirou: `-32602`. A mensagem é a que o seu SDK produzir
- `resources/read` de uma URI que não existe: `-32602`. Devolver `contents` vazio é proibido pela spec

Não é erro, e deve concluir normalmente:

- Recusa do usuário na elicitation, com `action` igual a `decline` ou `cancel`. O resultado é `complete`, com `isError` ausente ou `false`, e o `structuredContent` traz `reservado` igual a `false` mais um `motivo`

## Requisitos

### 1. Servidor MCP

Conceitos do curso: os três papéis e as duas camadas (M2 aula 1), o modelo stateless com metadados por request (M2 aula 1), tools e resources separados por quem controla (M2 aula 2), os dois tipos de erro (M3 aula 6), stderr no lugar do logging depreciado (M3 aula 6), Streamable HTTP (M4 aulas 4 a 6).

- Transporte Streamable HTTP, endpoint único, atendendo na porta `7301`
- Rejeitar com `-32602` e HTTP `400` qualquer request sem os campos obrigatórios de `_meta`, sem tentar inferir versão ou capabilities de request anterior
- Declarar as capabilities de `tools` e `resources`
- Três tools, todas com `inputSchema` que é um objeto JSON Schema válido
- `listar_salas`, sem parâmetros, devolvendo as salas com `structuredContent` conforme um `outputSchema` declarado, e também o JSON serializado em um bloco de texto, por compatibilidade
- `consultar_disponibilidade`, com `sala`, `inicio` e `fim`, devolvendo se o intervalo está livre e, se não estiver, as reservas em conflito. Ela aplica as mesmas validações de sala e de política que a reserva, e devolve os mesmos erros de execução
- `reservar_sala`, com `sala`, `inicio`, `fim` e `responsavel`, criando a reserva quando o intervalo está livre e dentro da política
- As três tools devolvem `structuredContent` além do bloco de texto. No caso da reserva concluída, o `structuredContent` traz `reserva`, `reservado`, `sala`, `inicio`, `fim`, `responsavel` e `politica`, como no bloco de contratos
- Um resource na URI `politica://uso`, devolvendo o conteúdo de `dados/politica-de-uso.md` com `mimeType` `text/markdown`
- Cada request recebido é registrado no stderr com, no mínimo, o método, o id e o valor do `traceparent` quando ele vier no `_meta`

### 2. O ciclo de MRTR na reserva

Conceitos do curso: MRTR como resposta à ausência de sessão (M2 aula 3), elicitation em form mode e o que ela não pode carregar (M2 aula 3), capability negotiation por request (M2 aula 1), handle não é autenticação (M4 aula 1).

- Quando o intervalo pedido conflita com uma reserva existente, `reservar_sala` responde com `resultType` igual a `input_required`
- O `inputRequests` é um mapa com uma única entrada, cujo valor é um request `elicitation/create` em `mode` form. A chave é atribuída pelo servidor e o cliente devolve exatamente a mesma chave no `inputResponses`. Não fixamos o texto dela, porque cada SDK a gera de um jeito
- O `requestedSchema` da elicitation é plano, com uma propriedade `sala` do tipo string restrita às salas alternativas calculadas pela regra já fechada acima. Com duas ou mais alternativas isso é um `enum`; com uma só, alguns geradores de schema emitem `const`, e ambos são aceitos
- Se não houver nenhuma alternativa, não há elicitation: a tool devolve `isError: true` com a mensagem definida nas regras de negócio
- O `requestState` é protegido por integridade, com HMAC ou AEAD, e carrega uma expiração entre 5 e 30 minutos. Assinar é obrigatório, cifrar é opcional: o que o avaliador testa é que uma adulteração seja detectada
- O `requestState` carrega tudo que o servidor precisa para reconstruir o pedido. O servidor não guarda nada em memória entre o `input_required` e o retry, e um retry apresentado depois de o processo ser reiniciado precisa funcionar
- A chave de integridade vem da variável de ambiente `REQUEST_STATE_SECRET`, nunca do código, e tem no mínimo 32 bytes de aleatoriedade. Gere a sua com `python3 -c "import secrets; print(secrets.token_hex(32))"`. Documente no README como gerá-la e exportá-la, nunca o valor que você usou: o repositório é público
- Um `requestState` adulterado ou expirado é rejeitado com `-32602`
- No retry, os argumentos que o cliente reenvia não são confiáveis. Se eles divergirem do que foi selado, a divergência não pode tomar efeito: ou o servidor rejeita o estado, ou usa os valores selados. Os dois caminhos são aceitos
- O servidor não envia elicitation para cliente que não declarou a capability de elicitation em form mode: nesse caso responde `-32021`
- No retry, o servidor lê `inputResponses` e `requestState` de dentro de `params`, reconstrói o pedido original a partir do próprio `requestState` e conclui a operação
- `action` igual a `decline` ou `cancel` conclui sem reservar, conforme a semântica já fechada

### 3. O agente como host MCP

Conceitos do curso: host cria clientes e agrega contexto (M2 aula 1), descoberta em runtime contra schema hardcoded (M1 aula 4), resource é escolha da aplicação (M2 aula 2), trace context reservado no `_meta` (M6 aula 4).

- O agente descobre as ferramentas por `tools/list` antes da primeira chamada, e não carrega uma lista fixa no código
- O agente lê o resource `politica://uso` e extrai dele a versão declarada na primeira linha
- Todo request MCP emitido pelo agente carrega os campos obrigatórios de `_meta` e declara a capability de elicitation em form mode, na forma `{"elicitation": {"form": {}}}`
- Todo request MCP emitido pelo agente carrega os headers que o transporte espelha do corpo: `MCP-Protocol-Version`, `Mcp-Method` e, em `tools/call` e `resources/read`, `Mcp-Name`. Header que não bate com o corpo é recusado com `-32020`
- Quando o cliente A2A envia o header `traceparent`, o agente propaga o mesmo trace-id no `traceparent` dentro do `_meta` de todos os requests MCP daquela Task. O span-id pode ser novo, o trace-id não
- Um erro de execução da tool, com `isError: true`, termina a Task em `TASK_STATE_FAILED` com a mensagem exata da tool visível no histórico da Task. É assim que um pedido de sala inexistente chega de volta ao cliente A2A

### 4. O agente como servidor A2A

Conceitos do curso: Agent Card como identidade pública e descoberta por well-known URI (M5 aula 2), Task com identidade, estado e produto (M5 aulas 3 e 4), opacidade do agente (M6 aula 1).

- `GET /.well-known/agent-card.json` devolve o Agent Card, na forma da v1.0
- O card declara a identidade, a interface JSON-RPC com a URL do endpoint A2A e a versão de protocolo `1.0`, as capabilities do agente, e uma skill com `id` igual a `reservar-sala`. A forma exata dos campos é a da v1.0, e o exemplo fiel está em `exemplos/wire/07-a2a-agent-card.json`
- O endpoint A2A, em `/a2a`, aceita `SendMessage` e `GetTask` no binding JSON-RPC
- Um `SendMessage` sem `taskId` abre uma Task nova, com `id` e `contextId` próprios
- A Task transita por `TASK_STATE_SUBMITTED` e `TASK_STATE_WORKING` antes de terminar, e `GetTask` reflete o estado corrente a qualquer momento
- A conclusão com sucesso produz um artifact de `name` igual a `reserva`, cujo conteúdo é o JSON da reserva criada, incluindo o campo `politica` com a versão lida do resource
- Estado terminal é definitivo: uma Task `COMPLETED`, `CANCELED` ou `FAILED` não volta a `WORKING`. Um `SendMessage` referenciando uma Task em estado terminal é recusado com erro

### 5. A ponte

Conceitos do curso: estado nomeado e passado explicitamente nas duas camadas (M2 aula 2, M5 aula 4), estado interrompido contra estado terminal (M5 aula 4), o triplo papel do agente (M6 aula 1).

- Ao receber `input_required` do servidor MCP, o agente coloca a Task em `TASK_STATE_INPUT_REQUIRED` e devolve, como mensagem de texto da Task, exatamente a linha `alternativas: <ids separados por virgula e espaco>`, na mesma ordem em que vieram no `enum` da elicitation. Sem prefixo, sem saudação, porque o avaliador compara duas pausas iguais byte a byte
- O `requestState` recebido fica guardado no agente associado àquela Task, e não é exposto no card, no artifact nem em nenhuma mensagem devolvida ao cliente A2A
- O `SendMessage` de continuação traz `taskId` e o texto `escolha=<valor>`. Uma escolha fora do `enum` mantém a Task em `TASK_STATE_INPUT_REQUIRED` e repete a lista de alternativas
- O retry ao servidor MCP usa um id de JSON-RPC diferente do request inicial, e leva `inputResponses` com a mesma chave que veio no `inputRequests` e o `requestState` ecoado sem modificação
- `escolha=recusar` vira `action` igual a `decline` na elicitation, e a Task termina em `TASK_STATE_CANCELED`
- O estado pausado é por Task: dois pedidos em conflito, pausados ao mesmo tempo, terminam cada um com a sua própria reserva, sem trocar de `requestState`

### 6. README

Substitua o `README.md` do starter pela documentação da sua entrega, com estas seções:

- Como rodar: os comandos exatos para subir os dois processos e para rodar o validador, a partir de um clone limpo
- Onde a ponte acontece: um parágrafo apontando o ponto do código em que o `input_required` do MCP vira `TASK_STATE_INPUT_REQUIRED`, e o ponto em que o `requestState` volta para o servidor
- Decisões técnicas: como você protegeu o `requestState`, por quanto tempo ele vale, e onde guardou o estado das Tasks
- Saída do validador: a saída completa da última execução, colada em bloco de código

## Restrições não negociáveis

- Dois processos separados. O agente fala com o servidor MCP por HTTP, como um cliente MCP de verdade. Importar a função da tool direto no código do agente descaracteriza a entrega
- O agente não implementa regra de negócio de sala. Conflito, política e alternativas são decisão do servidor MCP. O agente traduz protocolo, não domínio
- Nada de sessão. Nenhum dos dois lados pode inferir versão, capabilities ou contexto a partir de request anterior ou de conexão aberta. Isso é sobre estado de protocolo, não sobre o seu processo: manter um objeto cliente vivo entre chamadas é normal e recomendado
- O `requestState` é opaco para o agente: guardar, ecoar, nunca abrir, interpretar nem reconstruir, mesmo que o conteúdo seja legível
- Sem LLM no caminho de execução
- Se você encontrar uma limitação real no SDK que impeça algum item, documente no README com o trecho da evidência em vez de contornar reescrevendo o protocolo

## Fora de escopo

- Autenticação e autorização em qualquer uma das camadas. Sem OAuth, sem token, sem `securitySchemes` preenchido de verdade no card
- Streaming. Nada de `SendStreamingMessage`, SSE ou push notification
- Refinamento de Task, `referenceTaskIds` e continuidade entre Tasks diferentes
- Card estendido autenticado e assinatura JWS do card
- Prompts como primitivo do MCP, subscriptions e progress
- Container, compose, deploy e gateway
- Spans e atributos de OpenTelemetry. O que se exige é a propagação do `traceparent` e o registro dele em log, nada além disso
- Concorrência de escrita, transação e corrida entre duas reservas do mesmo intervalo criadas no mesmo milissegundo
- Persistência em disco das reservas, e qualquer expectativa de que elas sobrevivam a um restart
- Interface gráfica de qualquer tipo

## Contratos

Os exemplos abaixo omitem campos que a spec exige mas que não mudam o entendimento. A forma completa está em `exemplos/wire/`.

Conflito, com o servidor pedindo a escolha:

```json
{
  "jsonrpc": "2.0",
  "id": 7,
  "result": {
    "resultType": "input_required",
    "inputRequests": {
      "<chave atribuida pelo servidor>": {
        "method": "elicitation/create",
        "params": {
          "mode": "form",
          "message": "A sala pedida esta ocupada nesse intervalo. Escolha uma alternativa.",
          "requestedSchema": {
            "type": "object",
            "properties": {
              "sala": { "type": "string", "enum": ["sala-fusca", "sala-mirante"] }
            },
            "required": ["sala"]
          }
        }
      }
    },
    "requestState": "<opaco>"
  }
}
```

O retry, com id novo:

```json
{
  "jsonrpc": "2.0",
  "id": 8,
  "method": "tools/call",
  "params": {
    "name": "reservar_sala",
    "arguments": {
      "sala": "sala-garagem",
      "inicio": "2026-11-03T14:00:00-03:00",
      "fim": "2026-11-03T15:00:00-03:00",
      "responsavel": "Marty"
    },
    "inputResponses": {
      "<a mesma chave que veio no inputRequests>": { "action": "accept", "content": { "sala": "sala-fusca" } }
    },
    "requestState": "<opaco>",
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientCapabilities": { "elicitation": { "form": {} } },
      "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    }
  }
}
```

O artifact da Task concluída:

```json
{
  "name": "reserva",
  "parts": [
    {
      "text": "{\"reserva\":\"res-0003\",\"sala\":\"sala-fusca\",\"inicio\":\"2026-11-03T14:00:00-03:00\",\"fim\":\"2026-11-03T15:00:00-03:00\",\"responsavel\":\"Marty\",\"politica\":\"2026-11-01\"}"
    }
  ]
}
```

## Critérios de Aceite

Todos obrigatórios.

**Servidor MCP**

- [ ] Sobe na porta `7301` em Streamable HTTP e responde a `tools/list` com as três tools
- [ ] Toda tool tem `inputSchema` que é um objeto JSON Schema válido
- [ ] `listar_salas` devolve `structuredContent` conforme o `outputSchema` declarado e o mesmo JSON serializado em bloco de texto
- [ ] Request sem `io.modelcontextprotocol/protocolVersion` ou sem `io.modelcontextprotocol/clientCapabilities` recebe `-32602` com HTTP `400`
- [ ] `tools/call` de tool inexistente é recusado, por `-32602` ou por `isError`
- [ ] `resources/read` de `politica://uso` devolve o conteúdo de `dados/politica-de-uso.md`, e de URI inexistente devolve `-32602`
- [ ] Sala inexistente, janela violada, duração acima do limite e intervalo invertido devolvem `isError: true` com as mensagens exatas definidas neste enunciado
- [ ] O stderr do processo registra método, id e `traceparent` de cada request

**MRTR**

- [ ] Reserva em intervalo ocupado devolve `resultType` igual a `input_required`, com uma única entrada em `inputRequests`, em form mode, e um `requestState` presente
- [ ] O `requestedSchema` é plano e restringe a escolha às alternativas calculadas pela regra do enunciado, na ordem definida
- [ ] O mesmo pedido, vindo de cliente que não declarou elicitation em form mode, recebe `-32021` com `data.requiredCapabilities` e HTTP `400`
- [ ] O retry com `inputResponses` e `requestState` conclui a reserva e devolve `resultType` igual a `complete`
- [ ] `requestState` adulterado recebe `-32602`
- [ ] Um retry com argumentos adulterados não produz reserva com os valores adulterados
- [ ] Um retry com `requestState` válido conclui a reserva mesmo que o processo do servidor MCP tenha sido reiniciado entre o `input_required` e o retry
- [ ] A chave de integridade vem de `REQUEST_STATE_SECRET`, com no mínimo 32 bytes, e não há segredo hardcoded no código
- [ ] Recusa com `action` igual a `decline` conclui sem reservar e sem `isError`

**Agente como host MCP**

- [ ] O log do servidor MCP mostra um `tools/list` antes do primeiro `tools/call` do agente
- [ ] O log do servidor MCP mostra o `traceparent` com o mesmo trace-id enviado pelo validador no header da chamada A2A
- [ ] Os requests do agente declaram a capability de elicitation em form mode

**Agente como servidor A2A**

- [ ] `GET /.well-known/agent-card.json` responde 200 com um card v1.0 válido
- [ ] O card declara a interface JSON-RPC com a URL do endpoint e a versão de protocolo `1.0`, e uma skill de `id` `reservar-sala`
- [ ] `SendMessage` com sala livre termina a Task em `TASK_STATE_COMPLETED` com o artifact `reserva`
- [ ] O artifact traz a reserva criada e o campo `politica` com o valor lido do resource
- [ ] `GetTask` devolve `id`, `contextId`, estado corrente e o artifact quando existir
- [ ] `SendMessage` referenciando Task em estado terminal é recusado com erro
- [ ] `SendMessage` pedindo uma sala inexistente termina a Task em `TASK_STATE_FAILED`, com a mensagem exata da tool visível no histórico

**A ponte**

- [ ] `SendMessage` pedindo sala ocupada deixa a Task em `TASK_STATE_INPUT_REQUIRED` com a linha `alternativas: ` listando os ids na ordem do `enum`
- [ ] A continuação com `escolha=<id>` conclui a Task em `TASK_STATE_COMPLETED` com a reserva na sala escolhida
- [ ] A continuação com `escolha=recusar` termina a Task em `TASK_STATE_CANCELED`
- [ ] Escolha fora do `enum` mantém a Task em `TASK_STATE_INPUT_REQUIRED`
- [ ] Duas Tasks pausadas ao mesmo tempo concluem cada uma com a sua reserva, sem trocar de `requestState`
- [ ] O `requestState` não aparece em nenhuma resposta A2A

**Validação e entrega**

- [ ] `python3 validador/validar.py` passa nas 36 verificações, com os dois processos recém-iniciados, e termina com código de saída 0
- [ ] `dados/`, `validador/` e `exemplos/` não foram alterados
- [ ] O agente é determinístico: o mesmo pedido, enviado duas vezes, produz o mesmo resultado, e não há dependência de SDK de provedor de LLM no `package.json` ou no `pyproject.toml`
- [ ] O README tem as quatro seções exigidas, e os comandos funcionam a partir de um clone limpo

## Fluxo do avaliador

1. Clonar o fork em uma pasta limpa e seguir apenas o README do aluno.
2. Subir o servidor MCP e o agente, cada um em um terminal, deixando o stderr do servidor MCP visível.
3. `curl http://localhost:7300/.well-known/agent-card.json` e conferir, contra `exemplos/wire/07-a2a-agent-card.json`, a interface JSON-RPC, a versão de protocolo e a skill `reservar-sala`.
4. Rodar `python3 validador/validar.py --agente http://localhost:7300 --mcp http://localhost:7301` e conferir que todas as verificações passam.
5. No stderr do servidor MCP, localizar o `tools/list` anterior ao primeiro `tools/call` e o `traceparent` com o trace-id que o validador imprimiu.
6. Ainda no stderr, localizar o par de `tools/call` de uma reserva que passou pela pausa e conferir que o id do retry é diferente do id do request inicial.
7. Enviar manualmente um `SendMessage` pedindo `sala=sala-garagem` das 14h às 15h do dia 03/11/2026, usando `curl` com o corpo de `exemplos/wire/08-a2a-send-message.json`, conferir que a Task fica em `TASK_STATE_INPUT_REQUIRED` e que a linha de alternativas lista `sala-fusca, sala-mirante` nessa ordem.
8. Continuar com `escolha=sala-mirante` e conferir, por `GetTask`, a Task `COMPLETED` com o artifact `reserva` apontando para `sala-mirante` e com o campo `politica`.
9. Repetir o passo 7 e responder `escolha=recusar`, conferindo `TASK_STATE_CANCELED`.
10. Enviar um `SendMessage` com `sala=sala-inexistente` e conferir a Task em `TASK_STATE_FAILED` com a mensagem `Sala inexistente: sala-inexistente` no histórico.
11. Com o corpo de `exemplos/wire/03-tools-call-conflito-input-required.json`, pegar o `requestState` direto do servidor MCP, trocar um caractere e reenviar, conferindo `-32602`.
12. Repetir o passo 11 sem alterar o `requestState`, mas reiniciando o processo do servidor MCP antes de enviar o retry, e conferir que a reserva é concluída assim mesmo.
13. Repetir o mesmo `tools/call` de conflito sem declarar `elicitation` nas capabilities e conferir `-32021` com HTTP `400`.

Um único `SendMessage` de sala ocupada precisa causar, em sequência, a abertura da Task, a chamada MCP, o `input_required`, a pausa da Task, a retomada com id novo e a reserva criada. Se qualquer um desses efeitos faltar, a entrega está incompleta.

## Estrutura obrigatória do entregável

```
.
├── README.md              (substituido por voce)
├── dados/                 (nao alterar)
│   ├── salas.json
│   ├── reservas.json
│   └── politica-de-uso.md
├── validador/             (nao alterar)
│   └── validar.py
├── exemplos/              (nao alterar)
│   └── wire/
├── servidor-mcp/          (voce preenche)
└── agente/                (voce preenche)
```

Arquivos de apoio que você criar, como scripts de subida, podem ficar na raiz.

## Entregável

- Link do repositório público no GitHub, fork do starter, com a entrega na branch `main`
- README com os comandos de subida verificados, a explicação de onde a ponte acontece, as decisões técnicas e a saída do validador

## Regras de entrega

- Repositório único, a partir do fork do starter
- Toda a entrega na branch `main`
- A estrutura do starter deve ser mantida. Entregas que movem os dados, reescrevem o validador ou colapsam os dois processos em um não serão aceitas

## Ordem de execução sugerida

1. Leia `exemplos/wire/` inteiro antes de escrever qualquer linha. São dez pares de request e response capturados de uma execução real, e é o contrato que o validador cobra.
2. Suba o servidor MCP com uma tool só, `listar_salas`, e abra o MCP Inspector nele. Confirme que o `_meta` de cada request aparece e que a resposta traz `resultType`.
3. Implemente `consultar_disponibilidade` e as regras da política, com os quatro erros de execução e as mensagens exatas.
4. Implemente `reservar_sala` só no caminho feliz, sem MRTR.
5. Adicione o MRTR: primeiro o `input_required` com a elicitation, depois o `requestState` protegido, por último a checagem de capability e as rejeições.
6. Construa o agente como cliente MCP puro, sem A2A ainda: um script que descobre as tools, lê o resource e completa o ciclo de MRTR respondendo a elicitation na mão.
7. Só então coloque o A2A por fora: card, `SendMessage`, `GetTask` e a máquina de estados da Task.
8. Costure a ponte: pausa da Task, guarda do `requestState`, retomada com id novo.
9. Rode o validador, corrija, repita.
10. Do zero: mate os dois processos, clone o seu próprio fork em uma pasta limpa, siga só o seu README e percorra a checklist de critérios item por item antes do push final.

## Dicas finais

O erro mais caro deste desafio é implementar o MRTR como se fosse callback. Não existe canal de volta: o servidor não chama o cliente, ele termina a resposta pedindo informação, e é o cliente quem volta com um request novo. Se você se pegar procurando como manter a chamada aberta esperando o usuário responder, pare, porque o caminho é o oposto do que você está tentando. O sintoma dessa tentativa é um erro dizendo que o transporte não tem canal de volta para requests iniciados pelo servidor, e ele aparece cedo, na primeira vez que a tool tenta perguntar algo.

Os dois SDKs têm MRTR de primeira classe, e o caminho não se chama elicitation: procure por `input_required` no SDK Python e por `inputRequired` no de TypeScript, e leia as docstrings do que aparecer. É de lá que sai o desenho do seu servidor.

Vale o mesmo aviso do lado do agente. Se você usar o cliente MCP do SDK com um callback de elicitation, o cliente responde a pergunta sozinho e o ciclo fecha sem nunca voltar para o cliente A2A. A Task nunca pausa, e metade do desafio evapora. O agente precisa enxergar o `input_required` cru.

O segundo erro mais comum é o id do retry. A spec é explícita que ele precisa ser diferente do request inicial, porque são requests independentes. Reaproveitar o id funciona em muitos SDKs e quebra na verificação.

Sobre o `requestState`, a pergunta que separa a entrega boa da entrega ingênua é simples: o que acontece se eu trocar um caractere dele? Se a resposta for "o servidor processa assim mesmo" ou "o servidor explode com erro interno", falta integridade. A spec manda tratar esse campo como entrada controlada por atacante, e o avaliador vai testar exatamente isso.

O instrumento de depuração deste desafio é o stderr do servidor MCP com o Inspector aberto ao lado. Quase todo problema de ponte aparece ali em segundos: o `tools/list` que nunca acontece, o `_meta` sem capability, o `traceparent` que o agente esqueceu de propagar, o retry com o id repetido.

No fim, o que este desafio cobra em uma frase: o seu agente precisa ter profundidade por dentro e alcance por fora, e a única coisa que atravessa essa fronteira é estado nomeado explicitamente.

## Como rodar a entrega

Requer Python 3.10 ou superior. A partir de um clone limpo:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
export REQUEST_STATE_SECRET="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
python3 servidor-mcp/server.py
```

Em outro terminal:

```bash
source .venv/bin/activate
python3 agente/agent.py
```

Depois, rode:

```bash
python3 validador/validar.py --agente http://localhost:7300 --mcp http://localhost:7301
```

O MCP atende `/mcp` na porta 7301. O agente atende `/a2a` na porta 7300
e publica `/.well-known/agent-card.json`.

## Onde a ponte acontece

Em `agente/agent.py`, `process_mcp()` converte `resultType=input_required` em
`TASK_STATE_INPUT_REQUIRED`, preservando a chave e o `requestState` associados
à Task. `continue_task()` transforma `escolha=<id>` em `inputResponses` e
envia um novo `tools/call` com o `requestState` ecoado sem interpretá-lo.
Esse estado nunca é incluído nas respostas A2A.

## Decisões técnicas da entrega

O servidor MCP sela o `requestState` com HMAC-SHA256 usando exclusivamente
`REQUEST_STATE_SECRET`, com expiração de 15 minutos. A chave deve ser gerada
com `python3 -c "import secrets; print(secrets.token_hex(32))"` e exportada,
nunca commitada. As Tasks ficam em memória do agente; as reservas ficam em
memória do servidor. O agente descobre `tools/list`, lê `politica://uso`,
propaga `traceparent` e envia a capability de elicitation em form mode a cada
request MCP.

## Saída do validador

Última execução, iniciada com os dois processos do zero:

```text
trace-id desta execucao: ac17293cc343fb10122596ea7cba5023
procure esse valor no stderr do servidor MCP para conferir a propagacao do traceparent.

PASS 01 tools/list traz as tres tools
PASS 02 toda tool tem inputSchema de objeto
PASS 03 listar_salas devolve structuredContent e o mesmo JSON em texto
PASS 04 _meta sem protocolVersion devolve -32602 e HTTP 400
PASS 05 _meta sem clientCapabilities devolve -32602 e HTTP 400
PASS 06 tool inexistente e recusada, por -32602 ou por isError
PASS 07 resources/read de politica://uso devolve a politica
PASS 08 resources/read de URI inexistente devolve -32602
PASS 09 sala inexistente devolve isError com a mensagem exata
PASS 10 fora da janela devolve isError com a mensagem exata
PASS 11 duracao acima de 2h devolve isError com a mensagem exata
PASS 12 intervalo invertido devolve isError com a mensagem exata
PASS 13 conflito devolve input_required com inputRequests e requestState
PASS 14 a elicitation e form mode e oferece as alternativas na ordem certa
PASS 15 conflito sem a capability elicitation devolve -32021 e HTTP 400
PASS 16 retry com inputResponses e requestState conclui a reserva
PASS 17 requestState adulterado e rejeitado com -32602
PASS 18 argumentos adulterados no retry nao tomam efeito
PASS 19 recusa conclui sem reservar e sem isError
PASS 20 conflito sem alternativa possivel devolve isError com a mensagem exata
PASS 21 agent card responde 200 no well-known com JSON
PASS 22 o card declara a interface JSON-RPC com url e versao 1.0
PASS 23 o card declara a skill reservar-sala
PASS 24 SendMessage com sala livre conclui a Task
PASS 25 o artifact chama reserva e traz a versao da politica
PASS 26 GetTask devolve id, contextId e estado corrente
PASS 27 SendMessage com sala ocupada pausa a Task
PASS 28 a Task pausada lista as alternativas na ordem certa
PASS 29 escolha fora do enum mantem a Task pausada
PASS 30 a continuacao conclui a Task na sala escolhida
PASS 31 SendMessage em Task terminal e recusado
PASS 32 a recusa termina a Task em CANCELED
PASS 33 duas Tasks pausadas ao mesmo tempo concluem cada uma com a sua reserva
PASS 34 nenhuma resposta A2A carrega o requestState
PASS 35 sala inexistente termina a Task em FAILED com a mensagem da tool
PASS 36 o agente e deterministico: o mesmo pedido produz a mesma pausa

resumo: 36 passaram, 0 falharam, de 36 verificacoes
```
