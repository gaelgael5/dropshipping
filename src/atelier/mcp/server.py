"""Protocole MCP (JSON-RPC 2.0) en HTTP « streamable », sans état et sans flux SSE.

Choix d'une implémentation minimale plutôt que du SDK officiel : l'atelier n'expose que
des outils à réponse immédiate ; le SDK apporterait sessions, flux et sa propre pile
d'authentification, à concilier avec celle de l'atelier pour aucun usage actuel.
"""

from __future__ import annotations

import json
from typing import Any

import structlog

from atelier.connectors.auth import Principal
from atelier.mcp.tools import TOOLS, ToolContext, ToolInputError, check_known_args

log = structlog.get_logger(__name__)

SUPPORTED_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER_INFO = {"name": "atelier-annonces", "version": "0.1.0"}
INSTRUCTIONS = (
    "Atelier d'annonces candidates : articles repérés chez des fournisseurs, à préparer"
    " en annonces. Lire avec list_candidates / get_candidate, vérifier avec"
    " validate_candidate. Aucun outil ne publie ni ne supprime."
)


def _error(msg_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _result(msg_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


async def handle_message(ctx: ToolContext, who: Principal, message: Any) -> dict[str, Any] | None:
    """Traite un message ; rend la réponse, ou None pour une notification."""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _error(None, -32600, "requête JSON-RPC 2.0 attendue")
    method, msg_id = message.get("method"), message.get("id")
    if "id" not in message:
        return None  # notification (ex. notifications/initialized) : pas de réponse
    params = message.get("params") or {}
    if method == "initialize":
        asked = params.get("protocolVersion")
        version = asked if asked in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[0]
        return _result(msg_id, {
            "protocolVersion": version, "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO, "instructions": INSTRUCTIONS,
        })  # fmt: skip
    if method == "ping":
        return _result(msg_id, {})
    if method == "tools/list":
        return _result(msg_id, {"tools": [t.definition() for t in TOOLS.values()]})
    if method == "tools/call":
        return _result(msg_id, await _call_tool(ctx, who, params))
    return _error(msg_id, -32601, f"méthode inconnue : {method}")


async def _call_tool(ctx: ToolContext, who: Principal, params: dict[str, Any]) -> dict[str, Any]:
    name, args = params.get("name"), params.get("arguments") or {}
    tool = TOOLS.get(name) if isinstance(name, str) else None
    try:
        if tool is None:
            raise ToolInputError(f"outil inconnu : {name}")
        if not isinstance(args, dict):
            raise ToolInputError("« arguments » doit être un objet")
        check_known_args(tool, args)
        data = await tool.handler(ctx, args)
    except ToolInputError as exc:
        # Erreur métier = résultat lisible (isError), pas une erreur de protocole :
        # l'agent la voit et peut corriger son appel.
        log.info("outil_mcp_refuse", outil=name, appelant=who.label, raison=str(exc))
        return {"content": [{"type": "text", "text": str(exc)}], "isError": True}
    log.info("outil_mcp_appele", outil=name, appelant=who.label, voie=who.kind)
    text = json.dumps(data, ensure_ascii=False)
    return {"content": [{"type": "text", "text": text}], "structuredContent": data,
            "isError": False}  # fmt: skip
