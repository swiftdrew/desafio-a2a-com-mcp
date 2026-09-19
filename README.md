# A Ponte: um agente A2A com MCP por dentro

Servidor MCP da central de salas (Streamable HTTP, porta 7301) e agente que é host MCP
por dentro e servidor A2A por fora (porta 7300). Ambos em Python, sobre o SDK oficial
`mcp` v2, alinhado à revisão `2026-07-28` da spec.

## Como rodar

A partir de um clone limpo:

```bash
git clone git@github.com:swiftdrew/desafio-a2a-com-mcp.git
cd desafio-a2a-com-mcp
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Gere a chave de integridade do `requestState` **uma vez** e guarde-a no ambiente. Ela
tem 32 bytes de aleatoriedade (64 caracteres hex) e nunca fica no repositório:

```bash
export REQUEST_STATE_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
```

Use **a mesma** chave em toda subida do servidor MCP. Um `requestState` emitido antes de
um restart só continua válido se o processo novo subir com a mesma `REQUEST_STATE_SECRET`
— é isso que faz o estado viajar no token, e não na memória do servidor.

Terminal 1, servidor MCP (deixe o stderr visível):

```bash
source .venv/bin/activate
export REQUEST_STATE_SECRET=<a chave gerada acima>
python servidor-mcp/server.py
```

Terminal 2, agente A2A:

```bash
source .venv/bin/activate
python agente/agent.py
```

Terminal 3, validador:

```bash
python3 validador/validar.py --agente http://localhost:7300 --mcp http://localhost:7301
```

Portas e URLs são parametrizáveis por `MCP_PORT`, `AGENT_PORT`, `AGENT_URL` e `MCP_URL`,
com os valores padrão que o validador assume.

## Onde a ponte acontece

A costura mora em `process_mcp()`, em `agente/agent.py`. Toda chamada de tool passa por
`reservar()`, que usa `client.session.call_tool(..., allow_input_required=True)`: esse
`allow_input_required` é o que faz o `InputRequiredResult` chegar **cru** ao agente em vez
de ser resolvido pelo driver de MRTR do próprio SDK. Quando `process_mcp()` reconhece um
`mcp_types.InputRequiredResult`, ele lê a chave única de `inputRequests` e o enum de salas
do `requestedSchema`, guarda `request_state`, `mcp_key` e `choices` no dicionário da Task, e
move a Task para `TASK_STATE_INPUT_REQUIRED` com a linha `alternativas: <ids>`. É aí que o
`input_required` do MCP vira pausa de Task no A2A.

O caminho de volta está em `continue_task()`. O `escolha=<id>` do cliente A2A vira
`{"action": "accept", "content": {"sala": ...}}`, e `escolha=recusar` vira
`{"action": "decline"}`; os dois seguem para `reservar()` com `input_responses` na mesma
chave que o servidor atribuiu e o `request_state` ecoado byte a byte, sem nunca ser aberto
nem interpretado. O SDK cunha um id de JSON-RPC novo a cada request, então o retry nunca
reaproveita o id da chamada inicial. No servidor, `reservar_sala()` em
`servidor-mcp/server.py` detecta `ctx.request_state`, reconstrói o pedido a partir dos
argumentos selados e conclui a reserva.

## Decisões técnicas

**Integridade do `requestState`.** Quem sela e abre o token é o utilitário do próprio SDK,
`RequestStateSecurity`, configurado no construtor do `MCPServer` com
`keys=[REQUEST_STATE_SECRET]` e `audience="central-de-salas"`. Ele aplica AEAD
(AES-256-GCM com chave derivada por HKDF-SHA256), então adulterar um caractere quebra a
verificação e o request é recusado com `-32602` e a mensagem
`Invalid or expired requestState`. A chave vem só da variável de ambiente; não há segredo
no código. **Validade: 900 segundos (15 minutos)**, dentro da janela de 5 a 30 exigida.

**Argumentos não confiáveis no retry.** O servidor ignora os `arguments` reenviados pelo
cliente e reconstrói o pedido a partir dos valores selados dentro do `requestState`; só a
sala escolhida vem do `inputResponses`, e ainda é validada contra a lista de alternativas
recalculada. Argumento adulterado não toma efeito.

**Onde mora o estado.** As reservas ficam em memória no processo do servidor MCP (lista
`RESERVATIONS`), e não sobrevivem a restart — o enunciado dispensa persistência. O estado
das Tasks A2A fica em memória no agente, no dicionário `TASKS`, indexado por `taskId`;
`request_state`, `mcp_key` e `choices` são campos privados dessa entrada, filtrados por
`public_task()` para que o `requestState` jamais apareça em resposta A2A. Como o pausado é
por Task, duas reservas em conflito pausadas ao mesmo tempo não trocam de estado. O
servidor MCP, por outro lado, não guarda nada entre o `input_required` e o retry: tudo que
ele precisa volta dentro do token.

**Sem sessão, por request.** Um middleware ASGI em `servidor-mcp/server.py` registra no
stderr o método, o id e o `traceparent` de cada request, e recusa com `-32602` e HTTP 400
qualquer request sem `io.modelcontextprotocol/protocolVersion` ou
`io.modelcontextprotocol/clientCapabilities` no `_meta`, sem inferir nada de request
anterior. Header que não bate com o corpo é recusado com `-32020`.

**Capability de elicitation.** O agente declara elicitation em form mode passando um
`elicitation_callback` ao `Client` do SDK — é a presença do callback que faz o SDK anunciar
a capability no `_meta` de cada request. Como toda chamada usa `allow_input_required=True`,
esse callback nunca é invocado: ele existe só para a negociação, e o agente continua
enxergando o `input_required` cru. Vale registrar uma divergência do SDK: ao declarar
elicitation, o `mcp` v2 emite `{"elicitation": {"form": {}, "url": {}}}`, anunciando também
o url mode, sem permitir declarar apenas `form`
(`mcp/client/session.py`, em `_build_capabilities`:
`types.ElicitationCapability(form=types.FormElicitationCapability(), url=types.UrlElicitationCapability())`).
O form mode exigido está declarado; o `url` extra vem do SDK.

**Sem LLM.** O pedido chega em formato fixo e é interpretado por `parse_request()`, um
split por `=`. O agente traduz protocolo e não decide domínio: conflito, política e
alternativas são todos do servidor MCP.

## Saída do validador

```
trace-id desta execucao: e146bf54990192de554f610826d1a07f
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

Código de saída `0`, com os dois processos recém-iniciados.
