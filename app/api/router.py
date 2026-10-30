from fastapi import APIRouter

from app.api.routes import chat, health, metrics, portal_auth, portal_keys, portal_members, usage

api_router = APIRouter()
api_router.include_router(chat.router)
api_router.include_router(health.router)
api_router.include_router(usage.router)
api_router.include_router(portal_auth.router)
api_router.include_router(portal_members.router)
api_router.include_router(portal_keys.router)
api_router.include_router(metrics.router)
