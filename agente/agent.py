#!/usr/bin/env python3
import json
import os
import secrets
import urllib.request
import urllib.error
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MCP_URL = os.environ.get("MCP_URL", "http://localhost:7301/mcp")
PORT = int(os.environ.get("AGENT_PORT", "7300"))
PROTO = "2026-07-28"
TASKS = {}
DISCOVERED = False
POLICY_VERSION = None

def post_mcp(method, params, name, traceparent):
    global DISCOVERED, POLICY_VERSION
    meta = {"io.modelcontextprotocol/protocolVersion": PROTO,
            "io.modelcontextprotocol/clientCapabilities": {"elicitation": {"form": {}}},
            "traceparent": traceparent} if traceparent else {
                "io.modelcontextprotocol/protocolVersion": PROTO,
                "io.modelcontextprotocol/clientCapabilities": {"elicitation": {"form": {}}}}
    body = {"jsonrpc":"2.0", "id": secrets.token_hex(6), "method": method,
            "params": {**params, "_meta": meta}}
    headers = {"Content-Type":"application/json", "Accept":"application/json, text/event-stream",
               "MCP-Protocol-Version": PROTO, "Mcp-Method": method, "Mcp-Name": name}
    req = urllib.request.Request(MCP_URL, data=json.dumps(body).encode(),
                                 headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return json.loads(exc.read())

def ensure_context(traceparent):
    global DISCOVERED, POLICY_VERSION
    if not DISCOVERED:
        listed = post_mcp("tools/list", {}, "", traceparent)
        names = {t["name"] for t in (listed.get("result") or {}).get("tools", [])}
        if not {"listar_salas", "consultar_disponibilidade", "reservar_sala"} <= names:
            raise RuntimeError("servidor MCP nao publicou as tres tools")
        resource = post_mcp("resources/read", {"uri": "politica://uso"}, "politica://uso", traceparent)
        text = (((resource.get("result") or {}).get("contents") or [{}])[0].get("text") or "")
        POLICY_VERSION = text.splitlines()[0].split(":", 1)[1].strip()
        DISCOVERED = True

def parse_request(text):
    values = {}
    for part in text.split():
        if "=" in part:
            key, value = part.split("=", 1)
            values[key] = value
    return values

def message(text, role, task):
    return {"messageId": "msg-" + secrets.token_hex(6), "role": role,
            "parts": [{"text": text}], "taskId": task["id"], "contextId": task["contextId"]}

def public_task(task):
    result = {k: v for k, v in task.items()
              if k not in {"request_state", "mcp_key", "args", "choices", "traceparent",
                           "status_message"}}
    result["status"] = {"state": task["status"]}
    if task.get("status_message"):
        result["status"]["message"] = task["status_message"]
    return result

def task_response(task):
    return {"task": public_task(task)}

def run_new(task, text):
    task["history"].append({"messageId":"msg-"+secrets.token_hex(6), "role":"ROLE_USER",
                            "parts":[{"text":text}]})
    values = parse_request(text)
    args = {k: values.get(k, "") for k in ("sala", "inicio", "fim", "responsavel")}
    task["args"] = args
    try:
        ensure_context(task["traceparent"])
        response = post_mcp("tools/call", {"name":"reservar_sala", "arguments":args},
                            "reservar_sala", task["traceparent"])
    except Exception as exc:
        return fail(task, str(exc))
    return process_mcp(task, response)

def fail(task, text):
    task["status"] = "TASK_STATE_FAILED"
    task["status_message"] = message(text, "ROLE_AGENT", task)
    task["history"].append(task["status_message"])
    return task_response(task)

def process_mcp(task, response):
    if response.get("error"):
        return fail(task, response["error"].get("message", "erro MCP"))
    result = response.get("result") or {}
    if result.get("resultType") == "input_required":
        task["status"] = "TASK_STATE_INPUT_REQUIRED"
        task["request_state"] = result["requestState"]
        task["mcp_key"] = next(iter(result["inputRequests"]))
        schema = result["inputRequests"][task["mcp_key"]]["params"]["requestedSchema"]
        task["choices"] = schema["properties"]["sala"].get("enum") or [schema["properties"]["sala"]["const"]]
        text = "alternativas: " + ", ".join(task["choices"])
        task["status_message"] = message(text, "ROLE_AGENT", task)
        task["history"].append(task["status_message"])
        return task_response(task)
    if result.get("isError"):
        text = " ".join(x.get("text", "") for x in result.get("content", []))
        return fail(task, text)
    data = result.get("structuredContent") or {}
    artifact = {"artifactId":"art-"+secrets.token_hex(6), "name":"reserva",
                "parts":[{"text":json.dumps(data, ensure_ascii=False)}]}
    task["artifacts"] = [artifact]
    task["status"] = "TASK_STATE_COMPLETED"
    task["status_message"] = message(f"Reserva {data.get('reserva')} confirmada na {data.get('sala')}.",
                                     "ROLE_AGENT", task)
    task["history"].append(task["status_message"])
    return task_response(task)

def continue_task(task, text):
    task["history"].append({"messageId":"msg-"+secrets.token_hex(6), "role":"ROLE_USER",
                            "parts":[{"text":text}], "taskId":task["id"]})
    choice = parse_request(text).get("escolha")
    if choice == "recusar":
        response = post_mcp("tools/call", {"name":"reservar_sala", "arguments":task["args"],
            "inputResponses":{task["mcp_key"]:{"action":"decline"}}, "requestState":task["request_state"]},
            "reservar_sala", task["traceparent"])
        if response.get("error"):
            return fail(task, response["error"].get("message", "erro MCP"))
        task["status"] = "TASK_STATE_CANCELED"
        task["status_message"] = message("Reserva recusada.", "ROLE_AGENT", task)
        task["history"].append(task["status_message"])
        return task_response(task)
    if choice not in task["choices"]:
        return task_response(task)
    response = post_mcp("tools/call", {"name":"reservar_sala", "arguments":task["args"],
        "inputResponses":{task["mcp_key"]:{"action":"accept", "content":{"sala":choice}}},
        "requestState":task["request_state"]}, "reservar_sala", task["traceparent"])
    task.pop("request_state", None); task.pop("mcp_key", None); task.pop("choices", None)
    return process_mcp(task, response)

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_GET(self):
        if self.path != "/.well-known/agent-card.json":
            self.send_error(404); return
        card = {"name":"Central de Salas", "description":"Agente de reservas de salas.",
                "url":"http://localhost:7300", "version":"1.0.0",
                "supportedInterfaces":[{"url":"http://localhost:7300/a2a",
                    "protocolBinding":"JSONRPC", "protocolVersion":"1.0"}],
                "capabilities":{}, "skills":[{"id":"reservar-sala","name":"Reservar sala",
                    "description":"Reserva salas de reuniao."}]}
        raw = json.dumps(card).encode()
        self.send_response(200); self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def do_POST(self):
        if self.path != "/a2a":
            self.send_error(404); return
        try:
            req = json.loads(self.rfile.read(int(self.headers.get("Content-Length",0))))
            method, req_id, params = req.get("method"), req.get("id"), req.get("params") or {}
            if method == "GetTask":
                task = TASKS.get(params.get("id"))
                if not task:
                    raise ValueError("Task inexistente")
                result = task_response(task)
            elif method == "SendMessage":
                msg = params.get("message") or {}
                text = " ".join(p.get("text","") for p in msg.get("parts",[]))
                task_id = msg.get("taskId")
                if task_id:
                    task = TASKS.get(task_id)
                    if not task: raise ValueError("Task inexistente")
                    if task["status"] in {"TASK_STATE_COMPLETED","TASK_STATE_CANCELED","TASK_STATE_FAILED"}:
                        raise ValueError("Task terminal")
                    result = continue_task(task, text)
                else:
                    task = {"id":"task-"+secrets.token_hex(6), "contextId":"ctx-"+secrets.token_hex(6),
                            "status":"TASK_STATE_SUBMITTED", "history":[], "artifacts":[],
                            "traceparent":self.headers.get("traceparent")}
                    TASKS[task["id"]] = task
                    task["status"] = "TASK_STATE_WORKING"
                    result = run_new(task, text)
            else:
                raise ValueError("Metodo inexistente")
            out = {"jsonrpc":"2.0","id":req_id,"result":result}; status=200
        except Exception as exc:
            out = {"jsonrpc":"2.0","id":locals().get("req_id"),"error":{"code":-32602,"message":str(exc)}}; status=400
        raw=json.dumps(out,ensure_ascii=False).encode()
        self.send_response(status); self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(raw))); self.end_headers(); self.wfile.write(raw)

if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
