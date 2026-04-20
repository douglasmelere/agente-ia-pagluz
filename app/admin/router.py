"""Rotas do painel admin (Jinja2, sem JS)."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .. import conversations as conv_store
from .. import settings_store
from .. import triggers as trig_store
from .auth import require_admin

_templates_dir = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_templates_dir))

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])


# ---------------------------------------------------------------------------
# Redirect raiz
# ---------------------------------------------------------------------------
@router.get("", include_in_schema=False)
@router.get("/", include_in_schema=False)
async def admin_root() -> RedirectResponse:
    return RedirectResponse(url="/admin/conversations", status_code=303)


# ---------------------------------------------------------------------------
# Conversas
# ---------------------------------------------------------------------------
@router.get("/conversations", response_class=HTMLResponse)
async def list_conversations(request: Request, filter: str = "all") -> HTMLResponse:
    only_active = filter == "active"
    convs = await conv_store.list_all(only_active=only_active)
    return templates.TemplateResponse(
        request,
        "conversations.html",
        {"conversations": convs, "filter": filter},
    )


@router.post("/conversations/{remote_jid}/toggle")
async def toggle_conversation(remote_jid: str) -> RedirectResponse:
    conv = await conv_store.get_or_create(remote_jid)
    if conv.ai_active:
        await conv_store.deactivate(remote_jid, reason="manual_painel")
    else:
        await conv_store.activate(remote_jid, reason="manual_painel")
    return RedirectResponse(url="/admin/conversations", status_code=303)


# ---------------------------------------------------------------------------
# Triggers
# ---------------------------------------------------------------------------
@router.get("/triggers", response_class=HTMLResponse)
async def list_triggers(request: Request) -> HTMLResponse:
    triggers = await trig_store.list_all()
    return templates.TemplateResponse(
        request,
        "triggers.html",
        {"triggers": triggers},
    )


@router.post("/triggers")
async def create_trigger(phrase: str = Form(...)) -> RedirectResponse:
    phrase = phrase.strip()
    if phrase:
        await trig_store.create(phrase)
    return RedirectResponse(url="/admin/triggers", status_code=303)


@router.post("/triggers/{trigger_id}/toggle")
async def toggle_trigger(trigger_id: int) -> RedirectResponse:
    await trig_store.toggle(trigger_id)
    return RedirectResponse(url="/admin/triggers", status_code=303)


@router.post("/triggers/{trigger_id}/delete")
async def delete_trigger(trigger_id: int) -> RedirectResponse:
    await trig_store.delete(trigger_id)
    return RedirectResponse(url="/admin/triggers", status_code=303)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
@router.get("/settings", response_class=HTMLResponse)
async def view_settings(request: Request) -> HTMLResponse:
    items = await settings_store.all_items()
    return templates.TemplateResponse(
        request,
        "settings.html",
        {"settings": items, "valid_modes": sorted(settings_store.VALID_DEFAULT_MODES)},
    )


@router.post("/settings")
async def save_settings(default_mode: str = Form(...)) -> RedirectResponse:
    if default_mode in settings_store.VALID_DEFAULT_MODES:
        await settings_store.set("default_mode", default_mode)
    return RedirectResponse(url="/admin/settings", status_code=303)
