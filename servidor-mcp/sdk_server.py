#!/usr/bin/env python3
"""Servidor MCP oficial da entrega, usando o SDK mcp v2."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.context import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.request_state import RequestStateSecurity
from mcp.shared.exceptions import MCPError
from mcp_types import CallToolResult, ElicitRequest, ElicitRequestFormParams
from mcp_types import ElicitRequestedSchema, InputRequiredResult
from pydantic import BaseModel, ConfigDict, Field
from starlette.types import Receive, Scope, Send

ROOT = Path(__file__).resolve().parents[1]
PROTO = "2026-07-28"
SECRET = os.environ.get("REQUEST_STATE_SECRET", "")
if len(SECRET.encode()) < 32:
    raise SystemExit("REQUEST_STATE_SECRET deve ter pelo menos 32 bytes")

ROOMS = json.loads((ROOT / "dados/salas.json").read_text())
RESERVATIONS = json.loads((ROOT / "dados/reservas.json").read_text())
POLICY = (ROOT / "dados/politica-de-uso.md").read_text()
POLICY_VERSION = POLICY.splitlines()[0].split(":", 1)[1].strip()
COUNTER = len(RESERVATIONS) + 1


class RoomList(BaseModel):
    salas: list[dict[str, Any]]


class Availability(BaseModel):
    sala: str
    inicio: str
    fim: str
    disponivel: bool
    conflitos: list[dict[str, Any]]


class ReservationResult(BaseModel):
    model_config = ConfigDict(extra="allow")
    reserva: str | None = None
    reservado: bool
    sala: str | None = None
    inicio: str | None = None
    fim: str | None = None
    responsavel: str | None = None
    politica: str | None = None
    motivo: str | None = None


def room(room_id: str) -> dict[str, Any] | None:
    return next((item for item in ROOMS if item["id"] == room_id), None)


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value)


def validate(args: dict[str, Any]) -> str | None:
    if room(args.get("sala", "")) is None:
        return f"Sala inexistente: {args.get('sala')}"
    try:
        start, end = parse_time(args["inicio"]), parse_time(args["fim"])
    except (KeyError, TypeError, ValueError):
        return "Intervalo invalido: fim deve ser posterior a inicio"
    if end <= start:
        return "Intervalo invalido: fim deve ser posterior a inicio"
    if start.hour < 8 or end.hour > 20 or (end.hour == 20 and end.minute > 0):
        return "Fora da janela de uso: a politica permite reservas entre 08:00 e 20:00"
    if end - start > timedelta(hours=2):
        return "Duracao acima do limite: a politica permite no maximo 2 horas"
    return None


def conflicts(args: dict[str, Any], room_id: str | None = None) -> list[dict[str, Any]]:
    start, end = parse_time(args["inicio"]), parse_time(args["fim"])
    selected = room_id or args["sala"]
    return [
        item for item in RESERVATIONS
        if item["sala"] == selected
        and parse_time(item["inicio"]) < end
        and parse_time(item["fim"]) > start
    ]


def alternatives(args: dict[str, Any]) -> list[str]:
    requested = room(args["sala"])
    assert requested is not None
    available = [
        candidate for candidate in ROOMS
        if candidate["capacidade"] >= requested["capacidade"]
        and not conflicts(args, candidate["id"])
    ]
    available.sort(key=lambda item: (item["capacidade"], item["id"]))
    return [item["id"] for item in available[:3]]


def capability_declared(ctx: Context[Any, Any]) -> bool:
    capabilities = ctx.client_capabilities
    elicitation = getattr(capabilities, "elicitation", None) if capabilities else None
    return elicitation is not None and getattr(elicitation, "form", None) is not None


def response_value(response: Any, name: str, default: Any = None) -> Any:
    if isinstance(response, dict):
        return response.get(name, default)
    return getattr(response, name, default)


def complete(data: dict[str, Any]) -> CallToolResult:
    return CallToolResult(
        content=[{"type": "text", "text": json.dumps(data, ensure_ascii=False)}],
        resultType="complete",
        structuredContent=data,
    )


server = MCPServer(
    name="central-de-salas",
    version="1.0.0",
    request_state_security=RequestStateSecurity(
        keys=[SECRET],
        ttl=900,
        bind_principal=None,
        audience="central-de-salas",
    ),
)


@server.tool(name="listar_salas", description="Lista as salas disponíveis.", structured_output=True)
def listar_salas() -> RoomList:
    data = {"salas": ROOMS}
    return complete(data)  # type: ignore[return-value]


@server.tool(
    name="consultar_disponibilidade",
    description="Consulta a disponibilidade de uma sala.",
    structured_output=True,
)
def consultar_disponibilidade(sala: str, inicio: str, fim: str) -> Availability:
    args = {"sala": sala, "inicio": inicio, "fim": fim}
    error = validate(args)
    if error:
        raise ToolError(error)
    return complete({
        **args,
        "disponivel": not conflicts(args),
        "conflitos": conflicts(args),
    })  # type: ignore[return-value]


@server.tool(
    name="reservar_sala",
    description="Reserva uma sala de reunião.",
    structured_output=True,
)
def reservar_sala(
    sala: str,
    inicio: str,
    fim: str,
    responsavel: str,
    ctx: Context[Any, Any],
) -> ReservationResult:
    global COUNTER
    args = {"sala": sala, "inicio": inicio, "fim": fim, "responsavel": responsavel}

    if ctx.request_state:
        try:
            sealed_args = json.loads(ctx.request_state)["arguments"]
        except (TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
            raise MCPError(code=-32602, message="Invalid or expired requestState") from exc
        responses = ctx.input_responses or {}
        response = next(iter(responses.values()), None)
        action = response_value(response, "action")
        if action in {"decline", "cancel"}:
            return complete({
                "reserva": None, "reservado": False, "sala": None,
                "inicio": None, "fim": None, "responsavel": None,
                "politica": None, "motivo": "recusado",
            })  # type: ignore[return-value]
        selected_content = response_value(response, "content", {}) or {}
        selected = response_value(selected_content, "sala")
        choices = alternatives(sealed_args)
        if selected not in choices:
            raise ToolError("Sem alternativas disponiveis no intervalo")
        args = {**sealed_args, "sala": selected}

    error = validate(args)
    if error:
        raise ToolError(error)
    if conflicts(args):
        choices = alternatives(args)
        if not choices:
            raise ToolError("Sem alternativas disponiveis no intervalo")
        if not capability_declared(ctx):
            raise MCPError(
                code=-32021,
                message="Client did not declare the form elicitation capability",
                data={"requiredCapabilities": {"elicitation": {"form": {}}}},
            )
        key = "central:escolha_de_sala"
        request = ElicitRequest(
            method="elicitation/create",
            params=ElicitRequestFormParams(
                mode="form",
                message="A sala pedida esta ocupada nesse intervalo. Escolha uma alternativa.",
                requestedSchema=ElicitRequestedSchema(
                    type="object",
                    properties={"sala": {"type": "string", "enum": choices}},
                    required=["sala"],
                ),
            ),
        )
        return InputRequiredResult(
            resultType="input_required",
            inputRequests={key: request},
            requestState=json.dumps({"arguments": args}, separators=(",", ":")),
        )  # type: ignore[return-value]

    reservation = {
        "id": f"res-{COUNTER:04d}",
        "sala": args["sala"],
        "inicio": args["inicio"],
        "fim": args["fim"],
        "responsavel": args["responsavel"],
    }
    COUNTER += 1
    RESERVATIONS.append(reservation)
    return complete({
        "reserva": reservation["id"],
        "reservado": True,
        "sala": reservation["sala"],
        "inicio": reservation["inicio"],
        "fim": reservation["fim"],
        "responsavel": reservation["responsavel"],
        "politica": POLICY_VERSION,
        "motivo": None,
    })  # type: ignore[return-value]


@server.resource(
    "politica://uso",
    name="politica-de-uso",
    description="Política de uso das salas.",
    mime_type="text/markdown",
)
def politica_de_uso() -> str:
    return POLICY


MCP_APP = server.streamable_http_app(
    streamable_http_path="/mcp",
    json_response=True,
    stateless_http=True,
)


async def guarded(scope: Scope, receive: Receive, send: Send) -> None:
    """Valida metadados/header do contrato antes de entregar ao SDK."""
    if scope["type"] != "http" or scope.get("method") != "POST":
        await MCP_APP(scope, receive, send)
        return

    chunks: list[bytes] = []
    while True:
        message = await receive()
        chunks.append(message.get("body", b""))
        if not message.get("more_body", False):
            break
    raw = b"".join(chunks)
    try:
        request = json.loads(raw)
        req_id = request.get("id")
        params = request.get("params") or {}
        meta = params.get("_meta") or {}
        headers = {key.decode().lower(): value.decode() for key, value in scope.get("headers", [])}
        method = request.get("method")
        print(json.dumps({
            "method": method, "id": req_id, "traceparent": meta.get("traceparent"),
        }), file=sys.stderr, flush=True)
        if headers.get("mcp-method") != method or (
            method == "tools/call" and headers.get("mcp-name") != params.get("name")
        ) or (
            method == "resources/read" and headers.get("mcp-name") != params.get("uri")
        ):
            await send_json(send, req_id, -32020, "Headers MCP nao correspondem ao corpo")
            return
        if (
            "io.modelcontextprotocol/protocolVersion" not in meta
            or "io.modelcontextprotocol/clientCapabilities" not in meta
        ):
            await send_json(send, req_id, -32602, "Campos _meta obrigatorios ausentes")
            return
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, AttributeError):
        await send_json(send, None, -32600, "JSON-RPC invalido")
        return

    consumed = False

    async def replay() -> dict[str, Any]:
        nonlocal consumed
        if consumed:
            return {"type": "http.request", "body": b"", "more_body": False}
        consumed = True
        return {"type": "http.request", "body": raw, "more_body": False}

    await MCP_APP(scope, replay, send)


async def send_json(send: Send, req_id: Any, code: int, message: str) -> None:
    raw = json.dumps({
        "jsonrpc": "2.0", "id": req_id,
        "error": {"code": code, "message": message},
    }).encode()
    await send({"type": "http.response.start", "status": 400,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(raw)).encode())]})
    await send({"type": "http.response.body", "body": raw})


def run() -> None:
    uvicorn.run(guarded, host="0.0.0.0", port=int(os.environ.get("MCP_PORT", "7301")))


if __name__ == "__main__":
    run()
