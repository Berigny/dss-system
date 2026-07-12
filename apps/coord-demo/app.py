"""Minimal FastHTML COORD decoder demo.

Provides a single-page UI and a POST /resolve endpoint that forwards a COORD
JSON payload to the middleware resolver.
"""

import json
import os

import httpx
from fasthtml.common import Button, Div, Form, H1, P, Pre, Textarea, Titled, fast_app

MIDDLEWARE_URL = (
    os.getenv("MIDDLEWARE_URL")
    or os.getenv("MIDDLEWARE_BASE_URL")
    or os.getenv("API_BASE")
    or "http://middleware:8001"
).rstrip("/")

app, rt = fast_app(secret_key=os.getenv("FASTHTML_SECRET_KEY", "coord-demo-secret"))


@rt("/")
def index():
    return Titled(
        "COORD Demo",
        Div(
            H1("Resolve COORD"),
            P("Paste a COORD JSON payload and submit it to the middleware resolver."),
            Form(
                Textarea(
                    name="coordinate",
                    placeholder='chat-demo:WX-1',
                    rows=10,
                    style="width:100%;",
                ),
                Button("Resolve", type="submit"),
                action="/resolve",
                method="post",
            ),
            Div(Pre(id="result"), id="result-container"),
        ),
    )


@rt("/resolve", methods=["post"])
def resolve(coordinate: str):
    payload = {"coordinate": coordinate.strip()}
    try:
        response = httpx.post(
            f"{MIDDLEWARE_URL}/api/decode_coordinate",
            json=payload,
            timeout=30.0,
        )
        response.raise_for_status()
        return Pre(json.dumps(response.json(), indent=2), id="result")
    except httpx.HTTPError as exc:
        return Pre(f"Resolver error: {exc}", id="result")


@rt("/health")
def health():
    return {"status": "ok"}
