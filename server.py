from __future__ import annotations

import base64
import mimetypes
import os
import re
import uvicorn
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

import httpx
from fastapi import FastAPI
from fastmcp import FastMCP


# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------
JIRA_BASE_URL = os.getenv("JIRA_BASE_URL", "").rstrip("/")
JIRA_EMAIL = os.getenv("JIRA_EMAIL", "")
JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN", "")

# Render provides PORT automatically.
PORT = int(os.getenv("PORT", "8000"))

# Safety limit: user said attachments will be < 10 MB.
MAX_ATTACHMENT_BYTES = int(os.getenv("MAX_ATTACHMENT_BYTES", str(10 * 1024 * 1024)))
HTTP_TIMEOUT_SECONDS = float(os.getenv("HTTP_TIMEOUT_SECONDS", "120"))


# -----------------------------------------------------------------------------
# MCP server
# -----------------------------------------------------------------------------
mcp = FastMCP(
    name="Jira Attachments MCP",
    instructions=(
        "Use this server to inspect Jira issue attachments and fetch attachment "
        "content from Jira. For binary files, the content is returned as base64 "
        "along with filename, mime type, and size."
    ),
)


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def _require_env() -> None:
    missing = []
    if not JIRA_BASE_URL:
        missing.append("JIRA_BASE_URL")
    if not JIRA_EMAIL:
        missing.append("JIRA_EMAIL")
    if not JIRA_API_TOKEN:
        missing.append("JIRA_API_TOKEN")

    if missing:
        raise ValueError(
            "Missing required environment variables: " + ", ".join(missing)
        )


def _auth_headers() -> dict[str, str]:
    _require_env()
    token = base64.b64encode(f"{JIRA_EMAIL}:{JIRA_API_TOKEN}".encode("utf-8")).decode("ascii")
    return {
        "Authorization": f"Basic {token}",
        "Accept": "application/json",
    }


def _jira_api_url(path: str) -> str:
    _require_env()
    return f"{JIRA_BASE_URL}/rest/api/3/{path.lstrip('/')}"


def _sanitize_filename(filename: str) -> str:
    # Keep only the final path component and normalize unsafe characters.
    name = Path(filename).name.strip()
    name = re.sub(r"[^A-Za-z0-9._()\-\s]", "_", name)
    return name or "attachment"


def _guess_mime_type(filename: str, fallback: str | None = None) -> str:
    guessed, _ = mimetypes.guess_type(filename)
    return fallback or guessed or "application/octet-stream"


def _is_text_like(content_type: str, filename: str) -> bool:
    content_type = (content_type or "").lower()
    filename = filename.lower()
    if content_type.startswith("text/"):
        return True
    text_types = {
        "application/json",
        "application/xml",
        "application/javascript",
        "application/x-javascript",
        "application/yaml",
        "application/x-yaml",
    }
    if content_type in text_types:
        return True
    text_exts = {
        ".txt", ".md", ".csv", ".json", ".xml", ".yaml", ".yml", ".log", ".sql", ".py", ".java", ".js", ".ts",
    }
    return Path(filename).suffix.lower() in text_exts


async def _get_issue_attachments(issue_key: str) -> list[dict[str, Any]]:
    url = _jira_api_url(f"issue/{quote(issue_key, safe='')}?fields=attachment")
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=True) as client:
        response = await client.get(url, headers=_auth_headers())
        response.raise_for_status()
        data = response.json()
    return data.get("fields", {}).get("attachment", [])


def _find_attachment(attachments: list[dict[str, Any]], attachment_id: str) -> dict[str, Any]:
    for item in attachments:
        if str(item.get("id")) == str(attachment_id):
            return item
    raise ValueError(f"Attachment id '{attachment_id}' was not found on the issue.")


async def _download_attachment_bytes(content_url: str) -> bytes:
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=True) as client:
        async with client.stream("GET", content_url, headers=_auth_headers()) as response:
            response.raise_for_status()
            collected = bytearray()
            async for chunk in response.aiter_bytes():
                collected.extend(chunk)
                if len(collected) > MAX_ATTACHMENT_BYTES:
                    raise ValueError(
                        f"Attachment exceeds the configured limit of {MAX_ATTACHMENT_BYTES} bytes."
                    )
            return bytes(collected)


# -----------------------------------------------------------------------------
# Tools
# -----------------------------------------------------------------------------
@mcp.tool()
async def health() -> dict[str, Any]:
    """Check whether the server has the minimum Jira configuration required."""
    missing = []
    if not JIRA_BASE_URL:
        missing.append("JIRA_BASE_URL")
    if not JIRA_EMAIL:
        missing.append("JIRA_EMAIL")
    if not JIRA_API_TOKEN:
        missing.append("JIRA_API_TOKEN")

    return {
        "status": "ok" if not missing else "misconfigured",
        "missing_env_vars": missing,
        "max_attachment_bytes": MAX_ATTACHMENT_BYTES,
    }


@mcp.tool()
async def list_attachments(issue_key: str) -> dict[str, Any]:
    """
    List all attachments on a Jira issue.

    Args:
        issue_key: Jira issue key like STAR-12345.
    """
    try:
        attachments = await _get_issue_attachments(issue_key)
        items = []
        for item in attachments:
            filename = _sanitize_filename(item.get("filename", "attachment"))
            mime_type = _guess_mime_type(filename, item.get("mimeType"))
            items.append(
                {
                    "attachment_id": str(item.get("id", "")),
                    "filename": filename,
                    "size_bytes": item.get("size"),
                    "mime_type": mime_type,
                    "created": item.get("created"),
                    "author": {
                        "display_name": item.get("author", {}).get("displayName"),
                        "email_address": item.get("author", {}).get("emailAddress"),
                    },
                }
            )

        return {
            "issue_key": issue_key,
            "attachment_count": len(items),
            "attachments": items,
        }
    except httpx.HTTPStatusError as exc:
        return {
            "error": "jira_http_error",
            "status_code": exc.response.status_code,
            "message": exc.response.text[:1000],
            "issue_key": issue_key,
        }
    except Exception as exc:
        return {
            "error": "internal_error",
            "message": str(exc),
            "issue_key": issue_key,
        }


@mcp.tool()
async def get_attachment_content(issue_key: str, attachment_id: str) -> dict[str, Any]:
    """
    Fetch a Jira attachment and return its content in base64.

    This is suitable for remote MCP clients such as Claude Cowork because the
    file contents are returned directly instead of being written to a server-side
    path that Claude cannot access.

    Args:
        issue_key: Jira issue key like STAR-12345.
        attachment_id: Jira attachment id from list_attachments().
    """
    try:
        attachments = await _get_issue_attachments(issue_key)
        target = _find_attachment(attachments, attachment_id)

        original_filename = target.get("filename", "attachment")
        filename = _sanitize_filename(original_filename)
        content_url = target.get("content")
        if not content_url:
            raise ValueError("Jira did not provide a content URL for this attachment.")

        file_bytes = await _download_attachment_bytes(content_url)
        mime_type = _guess_mime_type(filename, target.get("mimeType"))
        encoded = base64.b64encode(file_bytes).decode("ascii")

        result: dict[str, Any] = {
            "issue_key": issue_key,
            "attachment_id": str(target.get("id")),
            "filename": filename,
            "mime_type": mime_type,
            "size_bytes": len(file_bytes),
            "content_encoding": "base64",
            "content_base64": encoded,
        }

        if _is_text_like(mime_type, filename):
            try:
                result["text_preview"] = file_bytes.decode("utf-8")[:10000]
            except UnicodeDecodeError:
                try:
                    result["text_preview"] = file_bytes.decode("latin-1")[:10000]
                except Exception:
                    pass

        return result
    except httpx.HTTPStatusError as exc:
        return {
            "error": "jira_http_error",
            "status_code": exc.response.status_code,
            "message": exc.response.text[:1000],
            "issue_key": issue_key,
            "attachment_id": attachment_id,
        }
    except Exception as exc:
        return {
            "error": "internal_error",
            "message": str(exc),
            "issue_key": issue_key,
            "attachment_id": attachment_id,
        }


@mcp.tool()
async def get_text_attachment(issue_key: str, attachment_id: str) -> dict[str, Any]:
    """
    Fetch a text-like Jira attachment and return decoded text.

    Best for TXT, CSV, JSON, XML, YAML, logs, and source files.
    Do not use this for binary office files like XLSX or PDF.
    """
    try:
        attachments = await _get_issue_attachments(issue_key)
        target = _find_attachment(attachments, attachment_id)

        filename = _sanitize_filename(target.get("filename", "attachment"))
        mime_type = _guess_mime_type(filename, target.get("mimeType"))
        if not _is_text_like(mime_type, filename):
            raise ValueError(
                f"Attachment '{filename}' is not a text-like file. Use get_attachment_content instead."
            )

        content_url = target.get("content")
        if not content_url:
            raise ValueError("Jira did not provide a content URL for this attachment.")

        file_bytes = await _download_attachment_bytes(content_url)
        try:
            text = file_bytes.decode("utf-8")
            encoding = "utf-8"
        except UnicodeDecodeError:
            text = file_bytes.decode("latin-1")
            encoding = "latin-1"

        return {
            "issue_key": issue_key,
            "attachment_id": str(target.get("id")),
            "filename": filename,
            "mime_type": mime_type,
            "size_bytes": len(file_bytes),
            "decoded_encoding": encoding,
            "text": text,
        }
    except httpx.HTTPStatusError as exc:
        return {
            "error": "jira_http_error",
            "status_code": exc.response.status_code,
            "message": exc.response.text[:1000],
            "issue_key": issue_key,
            "attachment_id": attachment_id,
        }
    except Exception as exc:
        return {
            "error": "internal_error",
            "message": str(exc),
            "issue_key": issue_key,
            "attachment_id": attachment_id,
        }


# -----------------------------------------------------------------------------
# HTTP app for Render / remote MCP
# -----------------------------------------------------------------------------
mcp_app = mcp.http_app(path="/", transport="sse")
app = FastAPI(lifespan=mcp_app.lifespan, title="Jira Attachments MCP")
app.mount("/mcp", mcp_app)


@app.get("/health")
async def http_health() -> dict[str, Any]:
    missing = []
    if not JIRA_BASE_URL:
        missing.append("JIRA_BASE_URL")
    if not JIRA_EMAIL:
        missing.append("JIRA_EMAIL")
    if not JIRA_API_TOKEN:
        missing.append("JIRA_API_TOKEN")

    return {
        "status": "ok" if not missing else "misconfigured",
        "missing_env_vars": missing,
        "mcp_url_hint": "/mcp",
        "max_attachment_bytes": MAX_ATTACHMENT_BYTES,
    }


if __name__ == "__main__":
    uvicorn.run("server:app", host="0.0.0.0", port=PORT)
