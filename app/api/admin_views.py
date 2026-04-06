import logging
from fastapi import APIRouter, Request, Depends
from fastapi.templating import Jinja2Templates
from pathlib import Path

from app.dependencies import get_current_admin

logger = logging.getLogger(__name__)

templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))

router = APIRouter(prefix="/admin", tags=["admin-views"])


@router.get("/")
async def dashboard(request: Request, admin: dict = Depends(get_current_admin)):
    return templates.TemplateResponse("admin/dashboard.html", {
        "request": request,
        "admin": admin,
    })


@router.get("/returns")
async def returns_list(request: Request, admin: dict = Depends(get_current_admin)):
    return templates.TemplateResponse("admin/returns_list.html", {
        "request": request,
        "admin": admin,
    })


@router.get("/returns/{id}")
async def return_detail(request: Request, id: int, admin: dict = Depends(get_current_admin)):
    return templates.TemplateResponse("admin/return_detail.html", {
        "request": request,
        "admin": admin,
        "return_id": id,
    })


@router.get("/complaints")
async def complaints_list(request: Request, admin: dict = Depends(get_current_admin)):
    return templates.TemplateResponse("admin/complaints_list.html", {
        "request": request,
        "admin": admin,
    })


@router.get("/complaints/{id}")
async def complaint_detail(request: Request, id: int, admin: dict = Depends(get_current_admin)):
    return templates.TemplateResponse("admin/complaint_detail.html", {
        "request": request,
        "admin": admin,
        "complaint_id": id,
    })
