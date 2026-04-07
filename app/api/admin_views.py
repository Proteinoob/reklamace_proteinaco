import logging
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pathlib import Path

from app.dependencies import get_current_admin

logger = logging.getLogger(__name__)

_templates_dir = Path(__file__).parent.parent / "templates"
_jinja_env = Environment(
    loader=FileSystemLoader(str(_templates_dir)),
    autoescape=select_autoescape(["html"]),
    auto_reload=True,
)

router = APIRouter(prefix="/admin", tags=["admin-views"])


def _render(template_name: str, **ctx) -> HTMLResponse:
    template = _jinja_env.get_template(template_name)
    return HTMLResponse(template.render(**ctx))


@router.get("/")
async def dashboard(request: Request, admin: dict = Depends(get_current_admin)):
    return _render("admin/dashboard.html", request=request, admin=admin)


@router.get("/returns")
async def returns_list(request: Request, admin: dict = Depends(get_current_admin)):
    return _render("admin/returns_list.html", request=request, admin=admin)


@router.get("/returns/{id}")
async def return_detail(request: Request, id: int, admin: dict = Depends(get_current_admin)):
    return _render("admin/return_detail.html", request=request, admin=admin, return_id=id)


@router.get("/complaints")
async def complaints_list(request: Request, admin: dict = Depends(get_current_admin)):
    return _render("admin/complaints_list.html", request=request, admin=admin)


@router.get("/complaints/{id}")
async def complaint_detail(request: Request, id: int, admin: dict = Depends(get_current_admin)):
    return _render("admin/complaint_detail.html", request=request, admin=admin, complaint_id=id)
