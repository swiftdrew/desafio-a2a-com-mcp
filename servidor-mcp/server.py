#!/usr/bin/env python3
import base64
import hashlib
import hmac
import json
import os
import sys
import time
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROTO = "2026-07-28"
SECRET = os.environ.get("REQUEST_STATE_SECRET", "")
if len(SECRET.encode()) < 32:
    raise SystemExit("REQUEST_STATE_SECRET deve ter pelo menos 32 bytes")
KEY = SECRET.encode()
ROOMS = json.loads((ROOT / "dados/salas.json").read_text())
RESERVATIONS = json.loads((ROOT / "dados/reservas.json").read_text())
POLICY = (ROOT / "dados/politica-de-uso.md").read_text()
POLICY_VERSION = POLICY.splitlines()[0].split(":", 1)[1].strip()
COUNTER = len(RESERVATIONS) + 1

def rpc_error(req_id, code, message, data=None):
    error = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": error}

def seal(value):
    payload = json.dumps({"exp": int(time.time()) + 900, "value": value},
                         separators=(",", ":"), sort_keys=True).encode()
    body = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    sig = hmac.new(KEY, body.encode(), hashlib.sha256).digest()
    return "v1." + body + "." + base64.urlsafe_b64encode(sig).decode().rstrip("=")

def unseal(token):
    try:
        version, body, signature = token.split(".", 2)
        if version != "v1":
            raise ValueError()
        expected = hmac.new(KEY, body.encode(), hashlib.sha256).digest()
        supplied = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
        if not hmac.compare_digest(expected, supplied):
            raise ValueError()
        decoded = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        if decoded["exp"] < time.time():
            raise ValueError()
        return decoded["value"]
    except Exception as exc:
        raise ValueError("requestState invalido ou expirado") from exc

def room(room_id):
    return next((r for r in ROOMS if r["id"] == room_id), None)

def parse_time(value):
    return datetime.fromisoformat(value)

def validate(args):
    r = room(args.get("sala"))
    if not r:
        return "Sala inexistente: " + str(args.get("sala"))
    try:
        start, end = parse_time(args["inicio"]), parse_time(args["fim"])
    except Exception:
        return "Intervalo invalido: fim deve ser posterior a inicio"
    if end <= start:
        return "Intervalo invalido: fim deve ser posterior a inicio"
    if start.hour < 8 or end.hour > 20 or (end.hour == 20 and end.minute > 0):
        return "Fora da janela de uso: a politica permite reservas entre 08:00 e 20:00"
    if end - start > __import__("datetime").timedelta(hours=2):
        return "Duracao acima do limite: a politica permite no maximo 2 horas"
    return None

def conflicts(args, room_id=None):
    start, end = parse_time(args["inicio"]), parse_time(args["fim"])
    rid = room_id or args["sala"]
    return [x for x in RESERVATIONS if x["sala"] == rid and
            parse_time(x["inicio"]) < end and parse_time(x["fim"]) > start]

def alternatives(args):
    requested = room(args["sala"])
    free = []
    for candidate in ROOMS:
        if candidate["capacidade"] >= requested["capacidade"] and not conflicts(args, candidate["id"]):
            free.append(candidate)
    return [r["id"] for r in sorted(free, key=lambda x: (x["capacidade"], x["id"]))[:3]]

def complete(data, is_error=False):
    result = {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}],
              "structuredContent": data, "resultType": "complete"}
    if is_error:
        result["isError"] = True
    return result

def call_tool(params):
    global COUNTER
    name, args = params.get("name"), params.get("arguments") or {}
    if name not in {"listar_salas", "consultar_disponibilidade", "reservar_sala"}:
        return None
    if name == "listar_salas":
        return complete({"salas": ROOMS})
    if name == "consultar_disponibilidade":
        error = validate(args)
        if error:
            return complete({"erro": error}, True)
        matches = conflicts(args)
        data = {"sala": args["sala"], "inicio": args["inicio"], "fim": args["fim"],
                "disponivel": not matches, "conflitos": matches}
        return complete(data)

    state = params.get("requestState")
    if state:
        try:
            sealed = unseal(state)
        except ValueError as exc:
            raise ProtocolError(-32602, str(exc))
        original = sealed["arguments"]
        responses = params.get("inputResponses") or {}
        response = next(iter(responses.values()), {})
        if response.get("action") in {"decline", "cancel"}:
            return complete({"reserva": None, "reservado": False, "sala": None,
                             "inicio": None, "fim": None, "responsavel": None,
                             "politica": None, "motivo": "recusado"})
        selected = (response.get("content") or {}).get("sala")
        if selected not in alternatives(original):
            return complete({"erro": "Sem alternativas disponiveis no intervalo"}, True)
        original = dict(original)
        original["sala"] = selected
        args = original
    error = validate(args)
    if error:
        return complete({"erro": error}, True)
    if conflicts(args):
        choices = alternatives(args)
        if not choices:
            return complete({"erro": "Sem alternativas disponiveis no intervalo"}, True)
        caps = ((params.get("_meta") or {}).get(
            "io.modelcontextprotocol/clientCapabilities") or {})
        if not ((caps.get("elicitation") or {}).get("form") is not None):
            raise ProtocolError(-32021, "Elicitation capability ausente",
                                {"requiredCapabilities": {"elicitation": {"form": {}}}})
        key = "central:escolha_de_sala"
        request = {"method": "elicitation/create", "params": {
            "mode": "form",
            "message": "A sala pedida esta ocupada nesse intervalo. Escolha uma alternativa.",
            "requestedSchema": {"type": "object", "properties": {
                "sala": {"type": "string", "enum": choices}},
                "required": ["sala"]}}}
        return {"resultType": "input_required", "inputRequests": {key: request},
                "requestState": seal({"arguments": args})}
    reservation = {"id": f"res-{COUNTER:04d}", "sala": args["sala"],
                   "inicio": args["inicio"], "fim": args["fim"],
                   "responsavel": args["responsavel"]}
    COUNTER += 1
    RESERVATIONS.append(reservation)
    return complete({"reserva": reservation["id"], "reservado": True,
                     "sala": reservation["sala"], "inicio": reservation["inicio"],
                     "fim": reservation["fim"], "responsavel": reservation["responsavel"],
                     "politica": POLICY_VERSION, "motivo": None})

class ProtocolError(Exception):
    def __init__(self, code, message, data=None):
        self.code, self.message, self.data = code, message, data

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            req = json.loads(self.rfile.read(length))
            method, req_id = req.get("method"), req.get("id")
            params = req.get("params") or {}
            meta = params.get("_meta") or {}
            print(json.dumps({"method": method, "id": req_id,
                              "traceparent": meta.get("traceparent")}), file=sys.stderr, flush=True)
            if self.headers.get("Mcp-Method") != method or (
                method == "tools/call" and self.headers.get("Mcp-Name") != params.get("name")):
                raise ProtocolError(-32020, "Headers MCP nao correspondem ao corpo")
            if "io.modelcontextprotocol/protocolVersion" not in meta or \
               "io.modelcontextprotocol/clientCapabilities" not in meta:
                raise ProtocolError(-32602, "Campos _meta obrigatorios ausentes")
            if method == "tools/list":
                result = {"tools": [
                    {"name": "listar_salas", "description": "Lista as salas.",
                     "inputSchema": {"type": "object", "properties": {}},
                     "outputSchema": {"type": "object"}},
                    {"name": "consultar_disponibilidade", "description": "Consulta disponibilidade.",
                     "inputSchema": {"type": "object", "properties": {"sala":{"type":"string"},"inicio":{"type":"string"},"fim":{"type":"string"}}, "required":["sala","inicio","fim"]}},
                    {"name": "reservar_sala", "description": "Reserva uma sala.",
                     "inputSchema": {"type": "object", "properties": {"sala":{"type":"string"},"inicio":{"type":"string"},"fim":{"type":"string"},"responsavel":{"type":"string"}}, "required":["sala","inicio","fim","responsavel"]}}
                ], "capabilities": {"tools": {}, "resources": {}}}
            elif method == "resources/read":
                if params.get("uri") != "politica://uso":
                    raise ProtocolError(-32602, "Resource inexistente")
                result = {"contents": [{"uri": "politica://uso", "mimeType": "text/markdown", "text": POLICY}]}
            elif method == "tools/call":
                result = call_tool(params)
                if result is None:
                    raise ProtocolError(-32602, "Tool inexistente")
            else:
                raise ProtocolError(-32601, "Metodo inexistente")
            response, status = {"jsonrpc":"2.0","id":req_id,"result":result}, 200
        except ProtocolError as exc:
            response, status = rpc_error(locals().get("req_id"), exc.code, exc.message, exc.data), 400
        except Exception as exc:
            response, status = rpc_error(locals().get("req_id"), -32602, str(exc)), 400
        raw = json.dumps(response, ensure_ascii=False).encode()
        self.send_response(status); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)

if __name__ == "__main__":
    from sdk_server import run

    run()
