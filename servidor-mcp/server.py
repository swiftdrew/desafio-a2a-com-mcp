#!/usr/bin/env python3
"""Servidor MCP da central de salas, sobre o SDK oficial mcp v2 (2026-07-28)."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.context import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.request_state import RequestStateSecurity
from mcp.shared.exceptions import MCPError
from mcp_types import (
    CallToolResult,
    ElicitRequest,
    ElicitRequestedSchema,
    ElicitRequestFormParams,
    InputRequiredResult,
)
from pydantic import BaseModel, ConfigDict
from starlette.types import Receive, Scope, Send

ROOT = Path(__file__).resolve().parents[1]
SAO_PAULO = timezone(timedelta(hours=-3))
SECRET = os.environ.get("REQUEST_STATE_SECRET", "")
if len(SECRET.encode()) < 32:
    raise SystemExit(
        "REQUEST_STATE_SECRET ausente ou com menos de 32 bytes. Gere com:\n"
        '  python3 -c "import secrets; print(secrets.token_hex(32))"'
    )

ROOMS = json.loads((ROOT / "dados/salas.json").read_text())
RESERVATIONS = json.loads((ROOT / "dados/reservas.json").read_text())
POLICY = (ROOT / "dados/politica-de-uso.md").read_text()
POLICY_VERSION = POLICY.splitlines()[0].split(":", 1)[1].strip()
COUNTER = len(RESERVATIONS) + 1

ERRO_JANELA = "Fora da janela de uso: a politica permite reservas entre 08:00 e 20:00"
ERRO_DURACAO = "Duracao acima do limite: a politica permite no maximo 2 horas"
ERRO_INTERVALO = "Intervalo invalido: fim deve ser posterior a inicio"
ERRO_SEM_ALTERNATIVA = "Sem alternativas disponiveis no intervalo"


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
    """Devolve sempre um instante com fuso; sem offset explicito, assume -03:00."""
    momento = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return momento if momento.tzinfo else momento.replace(tzinfo=SAO_PAULO)


def validate(args: dict[str, Any]) -> str | None:
    if room(args.get("sala", "")) is None:
        return f"Sala inexistente: {args.get('sala')}"
    try:
        start, end = parse_time(args["inicio"]), parse_time(args["fim"])
    except (KeyError, TypeError, ValueError):
        return ERRO_INTERVALO
    if end <= start:
        return ERRO_INTERVALO
    # A janela da politica e horario de Sao Paulo, qualquer que seja o offset recebido.
    abertura, fechamento = start.astimezone(SAO_PAULO), end.astimezone(SAO_PAULO)
    if abertura.hour < 8 or (fechamento.hour, fechamento.minute, fechamento.second) > (20, 0, 0):
        return ERRO_JANELA
    if end - start > timedelta(hours=2):
        return ERRO_DURACAO
    return None


def conflicts(args: dict[str, Any], room_id: str | None = None) -> list[dict[str, Any]]:
    start, end = parse_time(args["inicio"]), parse_time(args["fim"])
    selected = room_id or args["sala"]
    return [
        item
        for item in RESERVATIONS
        if item["sala"] == selected
        and parse_time(item["inicio"]) < end
        and parse_time(item["fim"]) > start
    ]


def alternatives(args: dict[str, Any]) -> list[str]:
    requested = room(args["sala"])
    if requested is None:
        return []
    available = [
        candidate
        for candidate in ROOMS
        if candidate["capacidade"] >= requested["capacidade"]
        and not conflicts(args, candidate["id"])
    ]
    available.sort(key=lambda item: (item["capacidade"], item["id"]))
    return [item["id"] for item in available[:3]]


def capability_declared(ctx: Context[Any, Any]) -> bool:
    """Form mode declarado; elicitation url-only ou ausente não serve."""
    capabilities = ctx.client_capabilities
    elicitation = getattr(capabilities, "elicitation", None) if capabilities else None
    return elicitation is not None and getattr(elicitation, "form", None) is not None


def field_of(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def complete(data: dict[str, Any]) -> CallToolResult:
    """structuredContent e o mesmo JSON serializado em bloco de texto."""
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


@server.tool(name="listar_salas", description="Lista as salas.", structured_output=True)
def listar_salas() -> RoomList:
    return complete({"salas": ROOMS})  # type: ignore[return-value]


@server.tool(
    name="consultar_disponibilidade",
    description="Consulta se uma sala esta livre no intervalo.",
    structured_output=True,
)
def consultar_disponibilidade(sala: str, inicio: str, fim: str) -> Availability:
    args = {"sala": sala, "inicio": inicio, "fim": fim}
    error = validate(args)
    if error:
        raise ToolError(error)
    conflitos = conflicts(args)
    return complete({**args, "disponivel": not conflitos, "conflitos": conflitos})  # type: ignore[return-value]


@server.tool(
    name="reservar_sala",
    description="Reserva uma sala de reuniao.",
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
        # O boundary do SDK ja verificou integridade e validade; os argumentos
        # reenviados pelo cliente sao descartados em favor dos valores selados.
        try:
            sealed = json.loads(ctx.request_state)["arguments"]
        except (TypeError, ValueError, KeyError) as exc:
            raise MCPError(code=-32602, message="Invalid or expired requestState") from exc
        response = next(iter((ctx.input_responses or {}).values()), None)
        if field_of(response, "action") in {"decline", "cancel"}:
            return complete({
                "reserva": None,
                "reservado": False,
                "sala": None,
                "inicio": None,
                "fim": None,
                "responsavel": None,
                "politica": None,
                "motivo": "recusado",
            })  # type: ignore[return-value]
        escolhida = field_of(field_of(response, "content", {}) or {}, "sala")
        if escolhida not in alternatives(sealed):
            raise ToolError(ERRO_SEM_ALTERNATIVA)
        args = {**sealed, "sala": escolhida}

    error = validate(args)
    if error:
        raise ToolError(error)

    if conflicts(args):
        choices = alternatives(args)
        if not choices:
            raise ToolError(ERRO_SEM_ALTERNATIVA)
        if not capability_declared(ctx):
            raise MCPError(
                code=-32021,
                message="Client did not declare the form elicitation capability",
                data={"requiredCapabilities": {"elicitation": {"form": {}}}},
            )
        pedido = ElicitRequest(
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
            inputRequests={"central:escolha_de_sala": pedido},
            requestState=json.dumps({"arguments": args}, separators=(",", ":")),
        )  # type: ignore[return-value]

    reserva = {
        "id": f"res-{COUNTER:04d}",
        "sala": args["sala"],
        "inicio": args["inicio"],
        "fim": args["fim"],
        "responsavel": args["responsavel"],
    }
    COUNTER += 1
    RESERVATIONS.append(reserva)
    return complete({
        "reserva": reserva["id"],
        "reservado": True,
        "sala": reserva["sala"],
        "inicio": reserva["inicio"],
        "fim": reserva["fim"],
        "responsavel": reserva["responsavel"],
        "politica": POLICY_VERSION,
        "motivo": None,
    })  # type: ignore[return-value]


@server.resource(
    "politica://uso",
    name="politica-de-uso",
    description="Politica de uso das salas.",
    mime_type="text/markdown",
)
def politica_de_uso() -> str:
    return POLICY


MCP_APP = server.streamable_http_app(
    streamable_http_path="/mcp",
    json_response=True,
    stateless_http=True,
)


async def error_response(send: Send, req_id: Any, code: int, message: str) -> None:
    raw = json.dumps({"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}).encode()
    await send({
        "type": "http.response.start",
        "status": 400,
        "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(raw)).encode())],
    })
    await send({"type": "http.response.body", "body": raw})


async def app(scope: Scope, receive: Receive, send: Send) -> None:
    """Loga o request e aplica as checagens por-request antes do SDK.

    Cada request e avaliado isoladamente: nada e inferido de request anterior.
    """
    if scope["type"] != "http" or scope.get("method") != "POST":
        await MCP_APP(scope, receive, send)
        return

    chunks: list[bytes] = []
    while True:
        event = await receive()
        chunks.append(event.get("body", b""))
        if not event.get("more_body", False):
            break
    raw = b"".join(chunks)

    try:
        request = json.loads(raw)
        method = request.get("method")
        req_id = request.get("id")
        params = request.get("params") or {}
        meta = params.get("_meta") or {}
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
        await error_response(send, None, -32700, "JSON-RPC invalido")
        return

    print(
        json.dumps({"method": method, "id": req_id, "traceparent": meta.get("traceparent")}),
        file=sys.stderr,
        flush=True,
    )

    headers = {key.decode().lower(): value.decode() for key, value in scope.get("headers", [])}
    esperado = {"tools/call": params.get("name"), "resources/read": params.get("uri")}.get(method)
    if headers.get("mcp-method") != method or (esperado is not None and headers.get("mcp-name") != esperado):
        await error_response(send, req_id, -32020, "Headers MCP nao correspondem ao corpo")
        return
    if (
        "io.modelcontextprotocol/protocolVersion" not in meta
        or "io.modelcontextprotocol/clientCapabilities" not in meta
    ):
        await error_response(send, req_id, -32602, "Campos _meta obrigatorios ausentes")
        return

    consumed = False

    async def replay() -> dict[str, Any]:
        nonlocal consumed
        if consumed:
            return {"type": "http.request", "body": b"", "more_body": False}
        consumed = True
        return {"type": "http.request", "body": raw, "more_body": False}

    await MCP_APP(scope, replay, send)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("MCP_PORT", "7301")))
