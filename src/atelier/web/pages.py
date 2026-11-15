from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from atelier.web.templating import templates

router = APIRouter(tags=["pages"], default_response_class=HTMLResponse)


@router.get("/")
async def show_home(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "index.html")
