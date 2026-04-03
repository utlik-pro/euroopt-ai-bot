"""API-релей для обхода санкционных ограничений.

Деплоится на Render/Railway/Fly.io (EU/US).
Проксирует запросы от бота в РБ → к санкционным LLM API.

Поддерживаемые провайдеры:
- /v1/anthropic/* → api.anthropic.com
- /v1/openai/*   → api.openai.com
- /v1/gemini/*   → generativelanguage.googleapis.com

Защита: Bearer-токен (RELAY_SECRET) — только наш бот может стучаться.
"""

import os
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, HTTPException, Header
from fastapi.responses import JSONResponse

RELAY_SECRET = os.environ.get("RELAY_SECRET", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")

# Маппинг провайдеров на их API
PROVIDERS = {
    "anthropic": {
        "base_url": "https://api.anthropic.com",
        "auth_header": "x-api-key",
        "auth_value": ANTHROPIC_API_KEY,
    },
    "openai": {
        "base_url": "https://api.openai.com",
        "auth_header": "Authorization",
        "auth_value": f"Bearer {OPENAI_API_KEY}",
    },
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com",
        "auth_header": "x-goog-api-key",
        "auth_value": GOOGLE_API_KEY,
    },
}

http_client: httpx.AsyncClient | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global http_client
    http_client = httpx.AsyncClient(timeout=120)
    yield
    await http_client.aclose()


app = FastAPI(title="LLM API Relay", lifespan=lifespan)


def verify_secret(authorization: str = Header(None)):
    if not RELAY_SECRET:
        return  # No auth configured (dev mode)
    if authorization != f"Bearer {RELAY_SECRET}":
        raise HTTPException(status_code=401, detail="Unauthorized")


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "providers": list(PROVIDERS.keys()),
        "anthropic_configured": bool(ANTHROPIC_API_KEY),
        "openai_configured": bool(OPENAI_API_KEY),
        "gemini_configured": bool(GOOGLE_API_KEY),
    }


@app.api_route("/v1/{provider}/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def relay(provider: str, path: str, request: Request, authorization: str = Header(None)):
    """Проксирует запрос к LLM API."""
    verify_secret(authorization)

    if provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider}. Available: {list(PROVIDERS.keys())}")

    config = PROVIDERS[provider]

    # Собираем URL
    target_url = f"{config['base_url']}/{path}"

    # Копируем заголовки, заменяем авторизацию
    headers = dict(request.headers)
    headers.pop("host", None)
    headers.pop("authorization", None)
    headers[config["auth_header"]] = config["auth_value"]

    # Читаем тело
    body = await request.body()

    start = time.monotonic()
    try:
        resp = await http_client.request(
            method=request.method,
            url=target_url,
            headers=headers,
            content=body,
            params=dict(request.query_params),
        )
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Upstream timeout")
    except httpx.ConnectError:
        raise HTTPException(status_code=502, detail="Cannot connect to upstream")

    elapsed = int((time.monotonic() - start) * 1000)

    # Возвращаем ответ
    return JSONResponse(
        content=resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {"raw": resp.text},
        status_code=resp.status_code,
        headers={"X-Relay-Time-Ms": str(elapsed), "X-Relay-Provider": provider},
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
