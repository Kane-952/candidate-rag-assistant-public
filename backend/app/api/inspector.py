"""Admin-only RAG inspection UI and read APIs."""
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse

from ..config import ROOT


router = APIRouter()


def require_admin(request: Request):
    if not request.state.auth_email:
        raise HTTPException(status_code=401, detail='请先登录。')
    if request.state.auth_email not in request.app.state.settings.inspector_admin_emails:
        raise HTTPException(status_code=403, detail='仅管理员可访问。')
    return request.app.state.inspector


@router.get('/inspector', include_in_schema=False)
def inspector_page(request: Request):
    require_admin(request)
    return FileResponse(ROOT / 'frontend' / 'inspector.html')


@router.get('/inspector/style.css', include_in_schema=False)
def inspector_style(request: Request):
    require_admin(request)
    return FileResponse(ROOT / 'frontend' / 'inspector.css', media_type='text/css')


@router.get('/inspector/app.js', include_in_schema=False)
def inspector_script(request: Request):
    require_admin(request)
    return FileResponse(ROOT / 'frontend' / 'inspector.js', media_type='text/javascript')


@router.get('/inspector/access-log')
def access_log(request: Request, email: str | None = Query(default=None, max_length=254)):
    require_admin(request)
    normalized = email.strip().lower() if email else None
    return {'records': request.app.state.auth.recent_logins(normalized or None)}


@router.get('/inspector/records')
def recent_records(request: Request, email: str | None = Query(default=None, max_length=254)):
    normalized = email.strip().lower() if email else None
    return {'records': require_admin(request).recent(normalized or None)}


@router.get('/inspector/records/{request_id}')
def record_detail(request_id: str, request: Request):
    store = require_admin(request)
    if len(request_id) != 12 or not all(c in '0123456789abcdef' for c in request_id):
        raise HTTPException(status_code=404, detail='记录不存在。')
    record = store.get(request_id)
    if record is None:
        raise HTTPException(status_code=404, detail='记录不存在。')
    return record
