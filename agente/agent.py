#!/usr/bin/env python3
"""Agente A2A: host MCP por dentro (SDK oficial), servidor A2A por fora."""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import mcp_types
from mcp import Client
from mcp.shared.exceptions import MCPError

MCP_URL = os.environ.get("MCP_URL", "http://localhost:7301/mcp")
PORT = int(os.environ.get("AGENT_PORT", "7300"))
BASE_URL = os.environ.get("AGENT_URL", f"http://localhost:{PORT}")
TERMINAIS = {"TASK_STATE_COMPLETED", "TASK_STATE_CANCELED", "TASK_STATE_FAILED"}

TASKS: dict[str, dict[str, Any]] = {}
CLIENT: Client | None = None
LOOP: asyncio.AbstractEventLoop | None = None
PRONTO = threading.Event()

# Descoberta em runtime: preenchidas pelo primeiro tools/list, nunca hardcoded.
TOOLS: set[str] = set()
POLICY_VERSION: str | None = None
DESCOBERTA = threading.Lock()


async def _never_called(*_args: Any, **_kwargs: Any) -> Any:
    """Declara a capability de elicitation em form mode sem nunca responder por conta propria.

    O SDK so anuncia `elicitation` quando ha um callback; como todas as chamadas
    usam `allow_input_required=True`, o driver de MRTR do cliente nao roda e o
    `input_required` chega cru ate a camada A2A, que e onde a Task pausa.
    """
    raise AssertionError("o agente nunca responde a elicitation sozinho")


def _loop_thread() -> None:
    async def main() -> None:
        global CLIENT, LOOP
        LOOP = asyncio.get_running_loop()
        async with Client(MCP_URL, elicitation_callback=_never_called) as client:
            CLIENT = client
            PRONTO.set()
            await asyncio.Event().wait()

    asyncio.run(main())


def submit(coro: Any) -> Any:
    PRONTO.wait(30)
    assert LOOP is not None
    return asyncio.run_coroutine_threadsafe(coro, LOOP).result(30)


def meta_de(traceparent: str | None) -> dict[str, Any] | None:
    """O trace-id do cliente A2A segue para dentro do _meta de todo request MCP."""
    return {"traceparent": traceparent} if traceparent else None


def descobrir(traceparent: str | None) -> None:
    global POLICY_VERSION
    with DESCOBERTA:
        if TOOLS and POLICY_VERSION:
            return
        assert CLIENT is not None
        params = mcp_types.PaginatedRequestParams(_meta=meta_de(traceparent))
        listadas = submit(CLIENT.session.list_tools(params=params))
        TOOLS.update(tool.name for tool in listadas.tools)
        if not {"listar_salas", "consultar_disponibilidade", "reservar_sala"} <= TOOLS:
            raise RuntimeError("servidor MCP nao publicou as tres tools")
        recurso = submit(CLIENT.session.read_resource("politica://uso", meta=meta_de(traceparent)))
        texto = getattr(recurso.contents[0], "text", "") or ""
        POLICY_VERSION = texto.splitlines()[0].split(":", 1)[1].strip()


def reservar(
    args: dict[str, Any],
    traceparent: str | None,
    input_responses: dict[str, Any] | None = None,
    request_state: str | None = None,
) -> Any:
    assert CLIENT is not None
    if "reservar_sala" not in TOOLS:
        raise RuntimeError("reservar_sala nao foi descoberta")
    return submit(
        CLIENT.session.call_tool(
            "reservar_sala",
            args,
            input_responses=input_responses,
            request_state=request_state,
            meta=meta_de(traceparent),
            allow_input_required=True,
        )
    )


def parse_request(text: str) -> dict[str, str]:
    valores = {}
    for parte in text.split():
        if "=" in parte:
            chave, valor = parte.split("=", 1)
            valores[chave] = valor
    return valores


def message(text: str, role: str, task: dict[str, Any]) -> dict[str, Any]:
    return {
        "messageId": "msg-" + secrets.token_hex(6),
        "role": role,
        "parts": [{"text": text}],
        "taskId": task["id"],
        "contextId": task["contextId"],
    }


def public_task(task: dict[str, Any]) -> dict[str, Any]:
    """O requestState e interno: nunca sai numa resposta A2A."""
    privados = {"request_state", "mcp_key", "args", "choices", "traceparent", "status_message"}
    resultado = {k: v for k, v in task.items() if k not in privados}
    resultado["status"] = {"state": task["status"]}
    if task.get("status_message"):
        resultado["status"]["message"] = task["status_message"]
    return resultado


def task_response(task: dict[str, Any]) -> dict[str, Any]:
    return {"task": public_task(task)}


def anuncia(task: dict[str, Any], text: str, estado: str) -> dict[str, Any]:
    task["status"] = estado
    task["status_message"] = message(text, "ROLE_AGENT", task)
    task["history"].append(task["status_message"])
    return task_response(task)


def fail(task: dict[str, Any], text: str) -> dict[str, Any]:
    return anuncia(task, text, "TASK_STATE_FAILED")


def texto_de(result: Any) -> str:
    return " ".join(getattr(bloco, "text", "") or "" for bloco in (result.content or []))


def process_mcp(task: dict[str, Any], result: Any) -> dict[str, Any]:
    """A ponte: o input_required do MCP vira a pausa da Task A2A."""
    if isinstance(result, mcp_types.InputRequiredResult):
        chave = next(iter(result.input_requests))
        pedido = result.input_requests[chave].model_dump(by_alias=True, mode="json")
        campo = pedido["params"]["requestedSchema"]["properties"]["sala"]
        task["mcp_key"] = chave
        task["request_state"] = result.request_state
        task["choices"] = campo.get("enum") or [campo["const"]]
        return anuncia(task, "alternativas: " + ", ".join(task["choices"]), "TASK_STATE_INPUT_REQUIRED")

    if result.is_error:
        return fail(task, texto_de(result))

    dados = dict(result.structured_content or {})
    dados["politica"] = POLICY_VERSION
    task["artifacts"] = [{
        "artifactId": "art-" + secrets.token_hex(6),
        "name": "reserva",
        "parts": [{"text": json.dumps(dados, ensure_ascii=False)}],
    }]
    return anuncia(
        task,
        f"Reserva {dados.get('reserva')} confirmada na {dados.get('sala')}.",
        "TASK_STATE_COMPLETED",
    )


def run_new(task: dict[str, Any], text: str) -> dict[str, Any]:
    task["history"].append({
        "messageId": "msg-" + secrets.token_hex(6),
        "role": "ROLE_USER",
        "parts": [{"text": text}],
    })
    valores = parse_request(text)
    task["args"] = {campo: valores.get(campo, "") for campo in ("sala", "inicio", "fim", "responsavel")}
    try:
        descobrir(task["traceparent"])
        result = reservar(task["args"], task["traceparent"])
    except MCPError as exc:
        return fail(task, exc.error.message)
    except Exception as exc:
        return fail(task, str(exc))
    return process_mcp(task, result)


def continue_task(task: dict[str, Any], text: str) -> dict[str, Any]:
    task["history"].append({
        "messageId": "msg-" + secrets.token_hex(6),
        "role": "ROLE_USER",
        "parts": [{"text": text}],
        "taskId": task["id"],
    })
    escolha = parse_request(text).get("escolha")

    if escolha == "recusar":
        resposta = {"action": "decline"}
    elif escolha in task["choices"]:
        resposta = {"action": "accept", "content": {"sala": escolha}}
    else:
        # Fora do enum: a Task segue pausada e a lista de alternativas e repetida.
        return anuncia(
            task,
            "alternativas: " + ", ".join(task["choices"]),
            "TASK_STATE_INPUT_REQUIRED",
        )

    try:
        # Retry com id JSON-RPC novo (o SDK cunha um por request), a mesma chave
        # do inputRequests e o requestState ecoado sem qualquer modificacao.
        result = reservar(
            task["args"],
            task["traceparent"],
            input_responses={task["mcp_key"]: resposta},
            request_state=task["request_state"],
        )
    except MCPError as exc:
        return fail(task, exc.error.message)
    except Exception as exc:
        return fail(task, str(exc))

    for chave in ("request_state", "mcp_key", "choices"):
        task.pop(chave, None)

    if escolha == "recusar":
        return anuncia(task, "Reserva recusada.", "TASK_STATE_CANCELED")
    return process_mcp(task, result)


AGENT_CARD = {
    "name": "Central de Salas",
    "description": "Agente de reservas de salas de reuniao da Hill Valley Tech.",
    "url": BASE_URL,
    "version": "1.0.0",
    "supportedInterfaces": [
        {"url": f"{BASE_URL}/a2a", "protocolBinding": "JSONRPC", "protocolVersion": "1.0"}
    ],
    "capabilities": {},
    "skills": [{
        "id": "reservar-sala",
        "name": "Reservar sala",
        "description": "Reserva salas de reuniao respeitando a politica de uso.",
    }],
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:
        pass

    def responder(self, status: int, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        if self.path != "/.well-known/agent-card.json":
            self.send_error(404)
            return
        self.responder(200, AGENT_CARD)

    def do_POST(self) -> None:
        if self.path != "/a2a":
            self.send_error(404)
            return
        req_id = None
        try:
            req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            req_id = req.get("id")
            metodo, params = req.get("method"), req.get("params") or {}

            if metodo == "GetTask":
                task = TASKS.get(params.get("id"))
                if not task:
                    raise ValueError("Task inexistente")
                resultado = task_response(task)
            elif metodo == "SendMessage":
                msg = params.get("message") or {}
                text = " ".join(parte.get("text", "") for parte in msg.get("parts", []))
                task_id = msg.get("taskId")
                if task_id:
                    task = TASKS.get(task_id)
                    if not task:
                        raise ValueError("Task inexistente")
                    if task["status"] in TERMINAIS:
                        raise ValueError("Task em estado terminal")
                    resultado = continue_task(task, text)
                else:
                    task = {
                        "id": "task-" + secrets.token_hex(6),
                        "contextId": "ctx-" + secrets.token_hex(6),
                        "status": "TASK_STATE_SUBMITTED",
                        "history": [],
                        "artifacts": [],
                        "traceparent": self.headers.get("traceparent"),
                    }
                    TASKS[task["id"]] = task
                    task["status"] = "TASK_STATE_WORKING"
                    resultado = run_new(task, text)
            else:
                raise ValueError("Metodo inexistente")
            self.responder(200, {"jsonrpc": "2.0", "id": req_id, "result": resultado})
        except Exception as exc:
            self.responder(400, {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32602, "message": str(exc)},
            })


if __name__ == "__main__":
    threading.Thread(target=_loop_thread, daemon=True).start()
    PRONTO.wait(30)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
