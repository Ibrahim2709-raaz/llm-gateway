from fastapi import APIRouter

from app.api.routes import (
    chat,
    health,
    metrics,
    portal_audit,
    portal_auth,
    portal_keys,
    portal_members,
    portal_routing,
    portal_usage,
    usage,
)

api_router = APIRouter()
api_router.include_router(chat.router)
api_router.include_router(health.router)
api_router.include_router(usage.router)
api_router.include_router(portal_auth.router)
api_router.include_router(portal_audit.router)
api_router.include_router(portal_members.router)
api_router.include_router(portal_keys.router)
api_router.include_router(portal_usage.router)
api_router.include_router(portal_routing.router)
api_router.include_router(metrics.router)
