import base64
import io
import logging
import os
import re
import uuid
from dataclasses import dataclass
from typing import Any, Optional

import anthropic
import httpx
from pypdf import PdfReader
from supabase import Client, create_client


logger = logging.getLogger("buildingos.document_qa")

OCR_PROMPT = """Extract the readable text from this building document.

Rules:
- Preserve important headings, tables, labels, room names, equipment names, values, and dates.
- Do not summarize or explain the document.
- Return plain text only.
- If the document is mostly drawings or scans, extract every readable label you can find.
"""


@dataclass(frozen=True)
class RAGConfig:
    supabase_url: str = ""
    supabase_service_role_key: str = ""
    supabase_bucket: str = "documents"
    embedding_api_url: str = "https://api.openai.com/v1/embeddings"
    embedding_api_key: str = ""
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536


def load_rag_config() -> RAGConfig:
    return RAGConfig(
        supabase_url=os.getenv("SUPABASE_URL", "").strip(),
        supabase_service_role_key=os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip(),
        supabase_bucket=os.getenv("SUPABASE_STORAGE_BUCKET", "documents").strip() or "documents",
        embedding_api_url=os.getenv("EMBEDDING_API_URL", "https://api.openai.com/v1/embeddings").strip(),
        embedding_api_key=os.getenv("EMBEDDING_API_KEY", "").strip(),
        embedding_model=os.getenv("EMBEDDING_MODEL", "text-embedding-3-small").strip(),
        embedding_dimensions=int(os.getenv("EMBEDDING_DIMENSIONS", "1536")),
    )


def is_supabase_configured(config: Optional[RAGConfig] = None) -> bool:
    config = config or load_rag_config()
    return bool(config.supabase_url and config.supabase_service_role_key)


def is_embeddings_configured(config: Optional[RAGConfig] = None) -> bool:
    config = config or load_rag_config()
    return bool(config.embedding_api_url and config.embedding_api_key and config.embedding_model)


def is_rag_ready(config: Optional[RAGConfig] = None) -> bool:
    config = config or load_rag_config()
    return is_supabase_configured(config) and is_embeddings_configured(config)


def get_supabase_client(config: Optional[RAGConfig] = None) -> Client:
    config = config or load_rag_config()
    if not is_supabase_configured(config):
        raise ValueError("Supabase is not configured.")
    return create_client(config.supabase_url, config.supabase_service_role_key)


def sanitize_filename(filename: Optional[str]) -> str:
    base = (filename or "document").strip()
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", base)
    return base.strip("-") or "document"


def build_storage_path(document_id: str, filename: Optional[str]) -> str:
    return f"{document_id}/{sanitize_filename(filename)}"


def upload_file_to_storage(
    client: Client,
    config: RAGConfig,
    document_id: str,
    filename: Optional[str],
    file_bytes: bytes,
    content_type: str,
) -> str:
    storage_path = build_storage_path(document_id, filename)
    client.storage.from_(config.supabase_bucket).upload(
        storage_path,
        file_bytes,
        {"content-type": content_type, "x-upsert": "true"},
    )
    return storage_path


def create_document_record(
    client: Client,
    *,
    document_id: str,
    building_id: Optional[str],
    filename: str,
    storage_path: str,
    mime_type: str,
    size_bytes: int,
) -> dict[str, Any]:
    payload = {
        "id": document_id,
        "building_id": building_id,
        "filename": filename,
        "storage_path": storage_path,
        "mime_type": mime_type,
        "size_bytes": size_bytes,
        "status": "processing",
    }
    response = client.table("documents").insert(payload).execute()
    data = response.data or []
    if data:
        return data[0]
    row = client.table("documents").select("*").eq("id", document_id).limit(1).execute()
    return (row.data or [payload])[0]


def update_document_record(client: Client, document_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    payload = dict(updates)
    response = client.table("documents").update(payload).eq("id", document_id).execute()
    data = response.data or []
    if data:
        return data[0]
    row = client.table("documents").select("*").eq("id", document_id).limit(1).execute()
    return (row.data or [payload])[0]


def list_documents(client: Client, building_id: Optional[str] = None) -> list[dict[str, Any]]:
    query = client.table("documents").select("*").order("created_at", desc=True)
    if building_id:
        query = query.eq("building_id", building_id)
    response = query.execute()
    return response.data or []


def delete_document(client: Client, config: RAGConfig, document_id: str) -> None:
    row = client.table("documents").select("*").eq("id", document_id).limit(1).execute()
    document = (row.data or [None])[0]
    if not document:
        return

    storage_path = document.get("storage_path")
    if storage_path:
        try:
            client.storage.from_(config.supabase_bucket).remove([storage_path])
        except Exception as exc:
            logger.warning("Failed to remove storage object %s: %s", storage_path, exc)

    client.table("documents").delete().eq("id", document_id).execute()


def insert_document_chunks(client: Client, chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not chunks:
        return []
    response = client.table("document_chunks").insert(chunks).execute()
    return response.data or chunks


def save_document_question(
    client: Client,
    *,
    document_id: str,
    question: str,
    answer: str,
    sources_json: list[dict[str, Any]],
) -> None:
    client.table("document_questions").insert(
        {
            "id": str(uuid.uuid4()),
            "document_id": document_id,
            "question": question,
            "answer": answer,
            "sources_json": sources_json,
        }
    ).execute()


def match_document_chunks(
    client: Client,
    *,
    document_id: str,
    query_embedding: list[float],
    match_count: int = 8,
    match_threshold: float = 0.2,
) -> list[dict[str, Any]]:
    response = client.rpc(
        "match_document_chunks",
        {
            "query_embedding": query_embedding,
            "match_document_id": document_id,
            "match_threshold": match_threshold,
            "match_count": match_count,
        },
    ).execute()
    return response.data or []


def normalize_text(text: str) -> str:
    text = text.replace("\x00", " ")
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_pdf_text(file_bytes: bytes) -> str:
    parts: list[str] = []
    try:
        reader = PdfReader(io.BytesIO(file_bytes))
    except Exception as exc:
        logger.warning("Could not read PDF text directly: %s", exc)
        return ""

    for idx, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception as exc:
            logger.warning("Failed to extract text from page %s: %s", idx, exc)
            text = ""
        text = normalize_text(text)
        if text:
            parts.append(f"[Page {idx}]\n{text}")

    return "\n\n".join(parts)


def extract_text_with_claude(file_bytes: bytes, content_type: str, api_key: str) -> str:
    media_type = "image/jpeg" if content_type == "image/jpg" else content_type
    message_type = "document" if media_type == "application/pdf" else "image"
    b64_data = base64.standard_b64encode(file_bytes).decode("utf-8")
    content_block = {
        "type": message_type,
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": b64_data,
        },
    }
    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        messages=[
            {
                "role": "user",
                "content": [content_block, {"type": "text", "text": OCR_PROMPT}],
            }
        ],
    )
    parts = [
        (block.get("text", "") if isinstance(block, dict) else getattr(block, "text", ""))
        for block in response.content
        if (block.get("type") if isinstance(block, dict) else getattr(block, "type", None)) == "text"
    ]
    return normalize_text("\n".join(parts))


def extract_text_for_rag(file_bytes: bytes, content_type: str, anthropic_api_key: Optional[str]) -> str:
    text = ""
    if content_type == "application/pdf":
        text = extract_pdf_text(file_bytes)
    elif content_type.startswith("text/"):
        try:
            text = file_bytes.decode("utf-8")
        except UnicodeDecodeError:
            text = file_bytes.decode("latin-1", errors="ignore")

    text = normalize_text(text)
    if len(text) >= 200:
        return text

    if not anthropic_api_key:
        return text

    try:
        ocr_text = extract_text_with_claude(file_bytes, content_type, anthropic_api_key)
        if ocr_text:
            return ocr_text
    except Exception as exc:
        logger.warning("Claude OCR fallback failed: %s", exc)

    return text


def chunk_text(text: str, max_chars: int = 1800, overlap: int = 250) -> list[dict[str, Any]]:
    cleaned = normalize_text(text)
    if not cleaned:
        return []

    paragraphs = [segment.strip() for segment in re.split(r"\n\s*\n", cleaned) if segment.strip()]
    chunks: list[dict[str, Any]] = []
    buffer = ""
    page_refs: set[int] = set()

    def flush() -> None:
        nonlocal buffer, page_refs
        final_text = normalize_text(buffer)
        if not final_text:
            return
        chunks.append(
            {
                "content": final_text,
                "token_count": max(1, len(final_text) // 4),
                "page_refs": sorted(page_refs),
            }
        )
        buffer = final_text[-overlap:] if overlap > 0 else ""
        page_refs = set()

    for paragraph in paragraphs:
        found_pages = {int(match) for match in re.findall(r"\[Page (\d+)\]", paragraph)}
        candidate = f"{buffer}\n\n{paragraph}".strip() if buffer else paragraph
        if len(candidate) > max_chars and buffer:
            flush()
            candidate = f"{buffer}\n\n{paragraph}".strip() if buffer else paragraph
        buffer = candidate
        page_refs.update(found_pages)
        if len(buffer) >= max_chars:
            flush()

    if buffer.strip():
        flush()

    for idx, chunk in enumerate(chunks):
        chunk["chunk_index"] = idx
    return chunks


def generate_embedding(text: str, config: Optional[RAGConfig] = None) -> list[float]:
    config = config or load_rag_config()
    if not is_embeddings_configured(config):
        raise ValueError("Embeddings are not configured.")

    payload: dict[str, Any] = {"model": config.embedding_model, "input": text}
    if config.embedding_dimensions:
        payload["dimensions"] = config.embedding_dimensions

    response = httpx.post(
        config.embedding_api_url,
        headers={
            "Authorization": f"Bearer {config.embedding_api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=60.0,
    )
    response.raise_for_status()
    body = response.json()
    data = body.get("data") or []
    if not data or "embedding" not in data[0]:
        raise ValueError("Embedding API returned no embedding vector.")
    return data[0]["embedding"]


def generate_embeddings_batch(texts: list[str], config: Optional[RAGConfig] = None) -> list[list[float]]:
    """Generate embeddings for multiple texts in a single API call (much faster)."""
    config = config or load_rag_config()
    if not is_embeddings_configured(config):
        raise ValueError("Embeddings are not configured.")
    
    if not texts:
        return []
    
    payload: dict[str, Any] = {"model": config.embedding_model, "input": texts}
    if config.embedding_dimensions:
        payload["dimensions"] = config.embedding_dimensions

    response = httpx.post(
        config.embedding_api_url,
        headers={
            "Authorization": f"Bearer {config.embedding_api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=120.0,  # Longer timeout for batch
    )
    response.raise_for_status()
    body = response.json()
    data = body.get("data") or []
    
    # Sort by index to maintain order
    data.sort(key=lambda x: x.get("index", 0))
    return [item["embedding"] for item in data if "embedding" in item]
