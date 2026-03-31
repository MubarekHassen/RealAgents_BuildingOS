"""
BuildingOS Backend — Real AI Document Analysis
Uses Claude claude-sonnet-4-6 to extract 10 categories of building intelligence
from uploaded PDFs, floor plans, specs, and drawings.

Run with:
    pip install -r requirements.txt
    cp .env.example .env   # then add your Anthropic API key
    uvicorn main:app --reload --port 8000
"""

import asyncio
import os
import base64
import json
import re
import logging
import io
import html
import secrets
from pathlib import Path
from typing import Optional, List, Any
from urllib.parse import parse_qs, unquote, urlencode, urlparse, urlunparse

import anthropic
import httpx
from fastapi import FastAPI, File, UploadFile, HTTPException, Query, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse, RedirectResponse
from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv(override=True)

from document_qa import (
    chunk_text,
    create_document_record,
    delete_document as delete_rag_document,
    extract_text_for_rag,
    generate_embedding,
    generate_embeddings_batch,
    get_supabase_client,
    insert_document_chunks,
    is_embeddings_configured,
    is_rag_ready,
    is_supabase_configured,
    list_documents as rag_list_documents,
    load_rag_config,
    match_document_chunks,
    save_document_question,
    update_document_record,
    upload_file_to_storage,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("buildingos")

# ── Optional OAuth libraries (graceful fallback if not installed) ──
try:
    from google_auth_oauthlib.flow import Flow as GoogleFlow
    from googleapiclient.discovery import build as gdrive_build
    from googleapiclient.http import MediaIoBaseDownload
    GOOGLE_AVAILABLE = True
except ImportError:
    GOOGLE_AVAILABLE = False
    logger.warning("google-auth-oauthlib not installed — Google Drive integration disabled")

try:
    import msal
    MICROSOFT_AVAILABLE = True
except ImportError:
    MICROSOFT_AVAILABLE = False
    logger.warning("msal not installed — Microsoft/OneDrive integration disabled")

# ── In-memory token store (replace with DB for production) ──
_tokens: dict = {}
_oauth_states: dict = {}  # state -> provider mapping for CSRF protection

app = FastAPI(title="BuildingOS API", version="1.0.0")

# Allow requests from the HTML file (file://) and any local dev server
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SUPPORTED_TYPES = {
    "application/pdf": "document",
    "image/png": "image",
    "image/jpeg": "image",
    "image/jpg": "image",
    "image/webp": "image",
}

# ─────────────────────────────────────────────
# ANALYSIS PROMPT
# This is what drives the quality of extraction.
# Tune this prompt to improve results over time.
# ─────────────────────────────────────────────
ANALYSIS_PROMPT = """You are a licensed building systems engineer and facilities management expert with 20+ years of experience in property condition assessments, MEP systems, building codes, capital planning, and commercial real estate.

Analyze the attached building document and extract structured intelligence. Return ONLY a valid JSON object — no markdown, no explanation, no preamble.

CRITICAL RULES — READ CAREFULLY:
- ONLY extract information that is explicitly present in this document. Do NOT invent, estimate, or assume any data.
- If a piece of information is not clearly stated in the document, you MUST use null for scalar fields or [] for arrays. Never fabricate values.
- Do NOT generate example assets, fictional equipment names, made-up model numbers, or placeholder costs.
- If a whole category cannot be populated from this document, leave all its fields as null/[].
- Be specific: only cite actual values, model numbers, room names, and measurements that appear verbatim or are directly calculable from the document.
- For monetary values in "cost" string fields, use format like "$280K" or "$1.4M" — only if the document states or clearly implies these costs.
- For monetary totals (yr1, yr3, yr5, totalRequired, cost_k), use integers representing thousands of dollars — only from document data.
- In "missing_data", list every category that could NOT be populated and what type of document the user should upload to get that data.

DOCUMENT-TYPE SPECIFIC GUIDANCE:
- PCA / Property Condition Assessment: This is the richest source. Extract building_profile (address, year built, SF, use), ALL deferred maintenance items with condition ratings and costs, capital needs by year, and all systems assessed.
- Lease / Rent Roll: Extract tenant names, suite numbers, square footage, lease expiration dates, and rent per SF. Populate building_profile.tenants.
- Floor plan / architectural drawing: THIS IS YOUR RICHEST PROFILE SOURCE. Extract everything you can for building_profile: number of floors, total SF (sum all floor areas if given), net SF, building use, room types and counts, ceiling heights, core vs shell vs tenant areas, structural grid, stair/elevator count, loading docks, parking levels, accessibility features, exterior cladding, window type, roof type. Also extract space breakdown percentages. Do NOT fabricate values not visible in the document.
- Specification (CSI format): code compliance, approved manufacturers, and commissioning requirements may be available.
- Equipment schedule / O&M manual: asset details, model numbers, useful life, and maintenance requirements.
- Energy audit: EUI baseline, ASHRAE benchmarks, efficiency measures with costs and savings.
- Inspection report: deficiency list, condition ratings, safety flags.
- Maintenance log / work order history: recurring failures, cost trends, asset reliability.

Return this exact JSON structure:

{
  "document_type": "one of: pca_report | lease | rent_roll | floor_plan | mep_drawing | structural | specification | equipment_manual | maintenance_report | energy_audit | inspection_report | utility_bill | general",
  "document_summary": "2-3 sentence factual summary of what this document actually contains — no assumptions.",
  "missing_data": [
    {"category": "<category name>", "reason": "<why this data is not in this document>", "upload_suggestion": "<what document type would provide this data>"}
  ],
  "building_profile": {
    "name": "<building name if stated, else null>",
    "address": "<full street address if stated, else null>",
    "city": "<city or null>",
    "state": "<state/province or null>",
    "year_built": <integer year or null>,
    "year_renovated": <integer year or null>,
    "gross_sf": <total gross square footage integer or null>,
    "net_sf": <net rentable/usable SF integer or null>,
    "stories": <number of above-grade floors integer or null>,
    "basement_levels": <integer number of below-grade levels or 0>,
    "building_use": "<Office | Retail | Industrial | Mixed-Use | Healthcare | Education | Multifamily | Hospitality | Laboratory | Data Center | Parking | Other | null>",
    "building_use_detail": "<more specific use description e.g. 'Class A suburban office with ground-floor retail' or null>",
    "construction_type": "<IBC type e.g. Type II-B Non-Combustible Steel Frame or null>",
    "structural_system": "<e.g. Steel moment frame | Concrete shear wall | Wood frame | Masonry bearing wall | null>",
    "foundation_type": "<e.g. Spread footings | Mat slab | Pile | null>",
    "exterior_cladding": "<e.g. Curtain wall glass | Brick veneer | Metal panel | EIFS | null>",
    "roof_type": "<e.g. TPO membrane | Built-up | Metal standing seam | null>",
    "window_type": "<e.g. Double-pane aluminum-framed curtain wall | null>",
    "ceiling_height_ft": <typical floor-to-floor or ceiling height in feet decimal or null>,
    "occupancy_pct": <integer 0-100 or null>,
    "parking_spaces": <integer or null>,
    "parking_type": "<Surface | Underground | Structured | null>",
    "loading_docks": <integer count or null>,
    "elevators": <integer count or null>,
    "stairwells": <integer count or null>,
    "sprinklered": <true | false | null>,
    "accessibility": "<ADA compliant | Partial | Non-compliant | null>",
    "zoning": "<zoning designation if stated or null>",
    "lot_sf": <site/lot area in SF integer or null>,
    "room_types": [
      {"type": "<room type e.g. Open Office | Private Office | Conference | Lobby | Restroom | Mechanical | Electrical | Stairwell | Corridor | Storage | Retail | Lab | Kitchen | Loading | Parking >", "count": <integer or null>, "approx_sf": <approximate total SF for this type integer or null>}
    ],
    "floor_breakdown": [
      {"level": "<e.g. Basement | Ground | Level 2 | Roof >", "use": "<primary use of this floor>", "sf": <integer or null>}
    ],
    "amenities": ["<list of building amenities e.g. Fitness center, Rooftop terrace, Café, Conference center>"],
    "tenants": [
      {"name": "<tenant name>", "sf": <integer or null>, "suite": "<suite/unit or null>", "lease_exp": "<YYYY-MM or month-year string or null>", "rent_psf": <decimal annual rent per SF or null>}
    ]
  },
  "deferred_maintenance": {
    "total_cost_k": <total deferred maintenance cost in $K integer or null>,
    "immediate_cost_k": <cost of items needed within 1-2 years in $K integer or null>,
    "items": [
      {
        "system": "<HVAC | Electrical | Plumbing | Roofing | Envelope | Structure | Life Safety | Elevators | Site | Interior | Other>",
        "item": "<specific description of deficiency or needed work>",
        "condition": "<Good | Fair | Poor | Critical>",
        "action": "<recommended corrective action>",
        "cost_k": <estimated cost in $K integer or null>,
        "priority": <1-5 integer where 1=immediate safety, 2=urgent, 3=near-term, 4=planned, 5=monitor>,
        "timeline_yr": <years from now integer, 0=immediate, 1=within 1 year, etc.>
      }
    ]
  },
  "assets": {
    "total": <integer>,
    "hvac": <integer>,
    "electrical": <integer>,
    "plumbing": <integer>,
    "mechanical": <integer>,
    "items": [
      {"name": "<asset name>", "mfr": "<manufacturer>", "model": "<model number>", "age": "<age or 'Unknown'>", "status": "Operational | Monitor | End-of-Life"}
    ]
  },
  "capital": {
    "totalRequired": <integer in $K>,
    "yr1": <integer in $K>,
    "yr3": <integer in $K>,
    "yr5": <integer in $K>,
    "items": [
      {"asset": "<description>", "yr": "<timeline e.g. Year 1-2>", "cost": "<e.g. $280K>", "priority": "HIGH | MEDIUM | LOW"}
    ]
  },
  "energy": {
    "eui": <integer kBtu/sf/yr or null>,
    "ashraePct": <integer % gap vs ASHRAE 90.1 benchmark, positive means worse than benchmark>,
    "insulation": "<e.g. R-19 or null>",
    "glazingU": "<decimal e.g. 0.38 or null>",
    "lightingPower": "<decimal W/sf e.g. 1.2 or null>",
    "opportunities": [
      {"item": "<description>", "savings": "<e.g. $45K/yr>", "cost": "<e.g. $60K>", "payback": "<e.g. 1.3 yrs>"}
    ]
  },
  "compliance": {
    "gaps": <integer>,
    "critical": <integer>,
    "codes": ["<code name>"],
    "flags": [
      {"sev": "high | medium | low", "code": "<code reference>", "issue": "<specific violation or concern>"}
    ]
  },
  "space": {
    "grossSF": <integer or null>,
    "ntr": <integer net-to-gross ratio % or null>,
    "rooms": <integer or null>,
    "types": {"office": <integer %, or null>, "mechanical": <integer %, or null>, "common": <integer %, or null>, "storage": <integer %, or null>},
    "opportunities": ["<specific optimization opportunity>"]
  },
  "structural": {
    "system": "<structural system type or null>",
    "foundation": "<foundation type or null>",
    "seismic": "<seismic zone e.g. Zone 3 (ASCE 7-22) or null>",
    "vintage": <year integer or null>,
    "flags": [
      {"sev": "high | medium | low", "issue": "<specific structural or safety concern>"}
    ]
  },
  "commissioning": {
    "deviations": <integer>,
    "items": [
      {"system": "<system name>", "spec": "<design intent>", "actual": "<observed condition>", "impact": "High | Medium | Low"}
    ]
  },
  "vendors": {
    "specs": <integer number of spec sections scanned>,
    "mfrs": <integer number of manufacturers referenced>,
    "items": [
      {"type": "<equipment category>", "approved": ["<manufacturer>"], "warranty": "<warranty terms>"}
    ],
    "opportunities": ["<cost saving or procurement opportunity>"]
  },
  "sustainability": {
    "energyStarScore": <integer estimated score 1-100 or null>,
    "waterIssues": <integer count or 0>,
    "opportunities": [
      {"cert": "<certification name>", "current": <integer score>, "target": <integer target>, "gap": "<e.g. 12pts>", "actions": "<specific actions to close gap>"}
    ]
  },
  "insurance": {
    "replacementValue": "<e.g. $12M or null>",
    "systemAge": "<e.g. 11 yr avg or null>",
    "risks": <integer count>,
    "items": [
      {"risk": "<specific risk>", "impact": "<insurance implication>", "action": "<recommended action>"}
    ]
  }
}"""

QUESTION_ANSWER_PROMPT = """You answer questions about a building document using only the supplied context.

Rules:
- Use only the retrieved context chunks below. Do not use outside knowledge.
- If the answer is not supported by the context, say you could not find it in the uploaded document.
- Keep the answer concise and factual.
- Return ONLY valid JSON in this shape:
{
  "answer": "<answer text>",
  "citations": [
    {"chunk_index": 0, "page_refs": [1], "quote": "<short direct quote from the context>"}
  ],
  "confidence": "high | medium | low"
}
"""


def extract_json_from_response(text: str) -> dict:
    """
    Robustly extract JSON from Claude's response.
    Handles cases where Claude adds explanation text around the JSON.
    """
    # Try direct parse first
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass

    # Look for JSON block in markdown fences
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence_match:
        try:
            return json.loads(fence_match.group(1))
        except json.JSONDecodeError:
            pass

    # Find the outermost { ... } block
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass

    raise ValueError("Could not extract valid JSON from Claude response")


class DocumentQuestionRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    match_count: int = Field(default=6, ge=1, le=12)
    match_threshold: float = Field(default=0.2, ge=0.0, le=1.0)


class BuildingQuestionRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    document_ids: List[str] = Field(default_factory=list)
    deferred_items: List[dict[str, Any]] = Field(default_factory=list)
    match_count: int = Field(default=8, ge=1, le=16)
    match_threshold: float = Field(default=0.2, ge=0.0, le=1.0)


class SharedLinkImportRequest(BaseModel):
    url: str = Field(min_length=10, max_length=4000)
    building_id: Optional[str] = Field(default=None)


def normalize_content_type(content_type: str) -> str:
    return "image/jpeg" if content_type == "image/jpg" else content_type


def ensure_supported_upload(content_type: str, file_bytes: bytes) -> tuple[str, str]:
    normalized = normalize_content_type(content_type or "")
    message_type = SUPPORTED_TYPES.get(normalized)
    if not message_type:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {normalized}. Supported: PDF, PNG, JPG, WEBP.",
        )
    if len(file_bytes) > 50 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large. Maximum size is 50MB.")
    return normalized, message_type


def build_claude_document_content(file_bytes: bytes, content_type: str, message_type: str) -> dict[str, Any]:
    b64_data = base64.standard_b64encode(file_bytes).decode("utf-8")
    return {
        "type": message_type,
        "source": {
            "type": "base64",
            "media_type": content_type,
            "data": b64_data,
        },
    }


def analyze_file_bytes(file_bytes: bytes, filename: str, content_type: str, api_key: str) -> dict:
    normalized_type, message_type = ensure_supported_upload(content_type, file_bytes)
    logger.info(f"Analyzing: {filename} ({len(file_bytes)/1024:.1f} KB, {normalized_type})")

    doc_content = build_claude_document_content(file_bytes, normalized_type, message_type)

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=8192,
            messages=[
                {
                    "role": "user",
                    "content": [doc_content, {"type": "text", "text": ANALYSIS_PROMPT}],
                }
            ],
        )
    except anthropic.AuthenticationError:
        raise HTTPException(status_code=401, detail="Invalid Anthropic API key. Check your .env file.")
    except anthropic.RateLimitError:
        raise HTTPException(status_code=429, detail="Anthropic rate limit reached. Please wait a moment and retry.")
    except anthropic.BadRequestError as exc:
        logger.error("Anthropic bad request: %s", exc)
        raise HTTPException(status_code=400, detail=f"Document could not be processed: {str(exc)}")
    except anthropic.APIError as exc:
        logger.error("Anthropic API error: %s", exc)
        raise HTTPException(status_code=502, detail=f"AI analysis failed: {str(exc)}")
    except Exception as exc:
        logger.error("Unexpected error calling Claude: %s: %s", type(exc).__name__, exc)
        raise HTTPException(status_code=500, detail=f"Unexpected error: {type(exc).__name__}: {str(exc)}")

    raw_text = response.content[0].text
    logger.info("Claude response length: %s chars", len(raw_text))

    try:
        intelligence = extract_json_from_response(raw_text)
    except ValueError:
        logger.error("JSON extraction failed. Raw response:\n%s", raw_text[:500])
        raise HTTPException(
            status_code=500,
            detail="AI returned malformed data. Try re-uploading the document.",
        )

    intelligence["_meta"] = {
        "filename": filename,
        "file_size_kb": round(len(file_bytes) / 1024, 1),
        "content_type": normalized_type,
        "model": "claude-sonnet-4-6",
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }
    return intelligence


def guess_content_type_from_filename(filename: str) -> str:
    suffix = Path(filename or "").suffix.lower()
    return {
        ".pdf": "application/pdf",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }.get(suffix, "application/octet-stream")


def filename_from_headers(headers: httpx.Headers) -> Optional[str]:
    disposition = headers.get("content-disposition", "")
    if not disposition:
        return None
    match = re.search(r"filename\*=UTF-8''([^;]+)", disposition, re.IGNORECASE)
    if match:
        return unquote(match.group(1)).strip().strip('"')
    match = re.search(r'filename="?([^";]+)"?', disposition, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None


def filename_from_url(url: str) -> Optional[str]:
    name = Path(urlparse(url).path.rstrip("/")).name
    if name and "." in name:
        return unquote(name)
    return None


def extract_google_drive_file_id(url: str) -> Optional[str]:
    for pattern in [r"/file/d/([a-zA-Z0-9_-]+)", r"[?&]id=([a-zA-Z0-9_-]+)", r"/d/([a-zA-Z0-9_-]+)"]:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


def extract_google_drive_folder_id(url: str) -> Optional[str]:
    for pattern in [r"/folders/([a-zA-Z0-9_-]+)", r"[?&]folder=([a-zA-Z0-9_-]+)"]:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


def extract_google_confirm_url(page_html: str, fallback_url: str) -> Optional[str]:
    match = re.search(r'href="([^"]*confirm[^"]*)"', page_html, re.IGNORECASE)
    if not match:
        match = re.search(r'action="([^"]*uc[^"]*)"', page_html, re.IGNORECASE)
    if not match:
        return None

    raw = html.unescape(match.group(1))
    if raw.startswith("/"):
        parsed = urlparse(fallback_url)
        return f"{parsed.scheme}://{parsed.netloc}{raw}"
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw
    return None


def is_google_drive_folder_url(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    return ("drive.google.com" in host or "docs.google.com" in host) and extract_google_drive_folder_id(url) is not None


def build_shared_link_candidates(url: str) -> tuple[list[str], str, Optional[str]]:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if "drive.google.com" in host or "docs.google.com" in host:
        file_id = extract_google_drive_file_id(url)
        if not file_id:
            raise HTTPException(status_code=400, detail="Google Drive link is missing a file ID.")
        query = parse_qs(parsed.query)
        params = {"export": "download", "id": file_id}
        resource_key = query.get("resourcekey", [None])[0]
        if resource_key:
            params["resourcekey"] = resource_key
        direct_url = f"https://drive.google.com/uc?{urlencode(params)}"
        return [
            direct_url,
            f"https://drive.google.com/uc?id={file_id}&export=download",
            url,
        ], "google_drive_link", file_id
    if any(part in host for part in ["1drv.ms", "onedrive.live.com", "sharepoint.com"]):
        query = parse_qs(parsed.query)
        query["download"] = ["1"]
        return [
            urlunparse(parsed._replace(query=urlencode(query, doseq=True))),
            url,
        ], "onedrive_link", None
    return [url], "shared_link", None


async def download_shared_file(url: str) -> tuple[bytes, str, str, dict[str, Any]]:
    candidates, source_kind, google_file_id = build_shared_link_candidates(url)
    headers = {"User-Agent": "BuildingOS/1.0"}
    last_status: Optional[int] = None

    async with httpx.AsyncClient(follow_redirects=True, timeout=90.0) as client:
        for candidate in candidates:
            response = await client.get(candidate, headers=headers)
            last_status = response.status_code
            content_type = normalize_content_type(
                response.headers.get("content-type", "").split(";")[0].strip().lower()
            )

            if google_file_id and content_type == "text/html" and "drive.google.com" in str(response.url):
                confirm_match = re.search(r"confirm=([0-9A-Za-z_-]+)", response.text)
                if confirm_match:
                    confirm_url = (
                        "https://drive.google.com/uc"
                        f"?export=download&confirm={confirm_match.group(1)}&id={google_file_id}"
                    )
                    response = await client.get(confirm_url, headers=headers)
                    last_status = response.status_code
                    content_type = normalize_content_type(
                        response.headers.get("content-type", "").split(";")[0].strip().lower()
                    )
                else:
                    confirm_url = extract_google_confirm_url(response.text, str(response.url))
                    if confirm_url:
                        response = await client.get(confirm_url, headers=headers)
                        last_status = response.status_code
                        content_type = normalize_content_type(
                            response.headers.get("content-type", "").split(";")[0].strip().lower()
                        )

            if response.status_code >= 400:
                continue

            filename = (
                filename_from_headers(response.headers)
                or filename_from_url(str(response.url))
                or filename_from_url(url)
                or "shared-document.pdf"
            )
            if content_type in {"", "application/octet-stream"}:
                content_type = guess_content_type_from_filename(filename)
            if content_type == "text/html":
                continue

            file_bytes = response.content
            if not file_bytes:
                continue

            normalized_type, _ = ensure_supported_upload(content_type, file_bytes)
            if Path(filename).suffix.lower() not in {".pdf", ".png", ".jpg", ".jpeg", ".webp"}:
                default_ext = {
                    "application/pdf": ".pdf",
                    "image/png": ".png",
                    "image/jpeg": ".jpg",
                    "image/webp": ".webp",
                }.get(normalized_type, "")
                filename = f"{Path(filename).stem or 'shared-document'}{default_ext}"

            return (
                file_bytes,
                filename,
                normalized_type,
                {
                    "source": source_kind,
                    "source_url": url,
                    "resolved_url": str(response.url),
                },
            )

    raise HTTPException(
        status_code=400,
        detail=(
            "Could not download a supported file from this shared link. "
            f"Make sure it is a public Google Drive or OneDrive PDF/image link. Last status: {last_status or 'unknown'}."
        ),
    )


def extract_drive_folder_entries(page_html: str) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    seen_ids: set[str] = set()

    link_pattern = re.compile(
        r'<a[^>]+href="([^"]*(?:/file/d/|[?&]id=)[^"]+)"[^>]*>(.*?)</a>',
        re.IGNORECASE | re.DOTALL,
    )
    for match in link_pattern.finditer(page_html):
        href = html.unescape(match.group(1))
        file_id = extract_google_drive_file_id(href)
        if not file_id or file_id in seen_ids:
            continue
        raw_name = re.sub(r"<[^>]+>", "", html.unescape(match.group(2))).strip()
        name = re.sub(r"\s+", " ", raw_name)
        entries.append(
            {
                "file_id": file_id,
                "name": name or f"{file_id}.pdf",
                "url": f"https://drive.google.com/file/d/{file_id}/view?usp=sharing",
            }
        )
        seen_ids.add(file_id)

    json_pattern = re.compile(
        r'"([A-Za-z0-9_-]{20,})","([^"]+\.(?:pdf|png|jpg|jpeg|webp))"',
        re.IGNORECASE,
    )
    for match in json_pattern.finditer(page_html):
        file_id = match.group(1)
        if file_id in seen_ids:
            continue
        entries.append(
            {
                "file_id": file_id,
                "name": html.unescape(match.group(2)),
                "url": f"https://drive.google.com/file/d/{file_id}/view?usp=sharing",
            }
        )
        seen_ids.add(file_id)

    return entries


async def list_google_drive_shared_folder_files(url: str) -> list[dict[str, str]]:
    folder_id = extract_google_drive_folder_id(url)
    if not folder_id:
        raise HTTPException(status_code=400, detail="Google Drive folder link is missing a folder ID.")

    candidates = [
        f"https://drive.google.com/embeddedfolderview?id={folder_id}#list",
        url,
    ]
    headers = {"User-Agent": "BuildingOS/1.0"}
    last_status: Optional[int] = None

    async with httpx.AsyncClient(follow_redirects=True, timeout=90.0) as client:
        for candidate in candidates:
            response = await client.get(candidate, headers=headers)
            last_status = response.status_code
            if response.status_code >= 400:
                continue
            entries = extract_drive_folder_entries(response.text)
            if entries:
                return entries[:20]

    raise HTTPException(
        status_code=400,
        detail=(
            "Could not read files from this shared Google Drive folder. "
            f"Make sure the folder is public and contains PDFs or images. Last status: {last_status or 'unknown'}."
        ),
    )


def index_document_bytes(
    *,
    client,
    rag_config,
    api_key: str,
    file_bytes: bytes,
    filename: str,
    content_type: str,
    building_id: Optional[str],
    source_meta: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    content_type, _ = ensure_supported_upload(content_type, file_bytes)
    document_id = secrets.token_hex(12)
    storage_path = upload_file_to_storage(
        client,
        rag_config,
        document_id=document_id,
        filename=filename,
        file_bytes=file_bytes,
        content_type=content_type,
    )

    create_document_record(
        client,
        document_id=document_id,
        building_id=building_id,
        filename=filename,
        storage_path=storage_path,
        mime_type=content_type,
        size_bytes=len(file_bytes),
    )

    try:
        analysis = analyze_file_bytes(file_bytes, filename, content_type, api_key)
        if source_meta:
            analysis.setdefault("_meta", {}).update(source_meta)
        extracted_text = extract_text_for_rag(file_bytes, content_type, api_key)
        if not extracted_text.strip():
            raise HTTPException(
                status_code=400,
                detail="No readable text could be extracted from this document for question answering.",
            )

        chunks = chunk_text(extracted_text)
        if not chunks:
            raise HTTPException(
                status_code=400,
                detail="The document did not produce any searchable chunks.",
            )

        # Batch generate embeddings for all chunks at once (much faster)
        chunk_texts = [chunk["content"] for chunk in chunks]
        embeddings = generate_embeddings_batch(chunk_texts, rag_config)
        
        chunk_rows = []
        for i, chunk in enumerate(chunks):
            chunk_rows.append(
                {
                    "document_id": document_id,
                    "chunk_index": chunk["chunk_index"],
                    "content": chunk["content"],
                    "token_count": chunk["token_count"],
                    "page_refs": chunk["page_refs"],
                    "embedding": embeddings[i] if i < len(embeddings) else generate_embedding(chunk["content"], rag_config),
                }
            )
        insert_document_chunks(client, chunk_rows)
        row = update_document_record(
            client,
            document_id,
            {
                "status": "ready",
                "document_summary": analysis.get("document_summary"),
                "analysis_json": analysis,
                "extracted_text": extracted_text,
                "chunk_count": len(chunk_rows),
                "error_message": None,
            },
        )
        return {"document": serialize_document_record(row), "analysis": analysis}
    except HTTPException as exc:
        update_document_record(client, document_id, {"status": "error", "error_message": exc.detail})
        raise
    except Exception as exc:
        logger.exception("Document upload/indexing failed for %s", filename)
        update_document_record(client, document_id, {"status": "error", "error_message": str(exc)})
        raise HTTPException(status_code=500, detail=f"Document indexing failed: {str(exc)}")


def serialize_document_record(row: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if not row:
        return None
    return {
        "id": row.get("id"),
        "building_id": row.get("building_id"),
        "filename": row.get("filename"),
        "mime_type": row.get("mime_type"),
        "size_bytes": row.get("size_bytes"),
        "status": row.get("status"),
        "chunk_count": row.get("chunk_count", 0),
        "document_summary": row.get("document_summary"),
        "error_message": row.get("error_message"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "ready_for_qa": row.get("status") == "ready" and (row.get("chunk_count") or 0) > 0,
    }


def build_question_context(matches: list[dict[str, Any]]) -> str:
    parts = []
    for match in matches:
        pages = match.get("page_refs") or []
        page_label = ", ".join(str(page) for page in pages) if pages else "unknown"
        parts.append(
            f"[Source: {match.get('source') or 'unknown'} | Chunk {match.get('chunk_index')} | Pages: {page_label} | Similarity: {round(match.get('similarity', 0), 3)}]\n"
            f"{match.get('content', '')}"
        )
    return "\n\n".join(parts)


def normalize_search_text(value: Optional[str]) -> str:
    if not value:
        return ""
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def build_document_search_targets(row: dict[str, Any]) -> list[str]:
    targets: list[str] = []
    filename = row.get("filename") or ""
    if filename:
        stem = Path(filename).stem
        targets.extend([filename, stem])
    analysis = row.get("analysis_json") or {}
    building_profile = analysis.get("building_profile") or {}
    if building_profile.get("name"):
        targets.append(building_profile["name"])
    if analysis.get("document_summary"):
        targets.append(analysis["document_summary"])
    return [normalize_search_text(target) for target in targets if normalize_search_text(target)]


def filter_documents_for_question(question: str, document_map: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    normalized_question = normalize_search_text(question)
    if not normalized_question:
        return document_map

    preferred: dict[str, dict[str, Any]] = {}
    letter_match = re.search(r"\b(?:building|file|doc|document)\s+([a-z])\b", question, re.I)
    requested_letter = letter_match.group(1).lower() if letter_match else None

    for doc_id, row in document_map.items():
        targets = build_document_search_targets(row)
        filename = normalize_search_text(row.get("filename"))
        if any(target and target in normalized_question for target in targets):
            preferred[doc_id] = row
            continue
        if requested_letter and re.search(rf"(^|[^a-z0-9]){re.escape(requested_letter)}($|[^a-z0-9])", filename):
            preferred[doc_id] = row

    return preferred or document_map


def resolve_cited_sources(answer: dict[str, Any], matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    citations = answer.get("citations") or []
    resolved: list[dict[str, Any]] = []
    for citation in citations:
        source = citation.get("source")
        chunk_index = citation.get("chunk_index")
        matched = next(
            (
                match
                for match in matches
                if (source is None or match.get("source") == source)
                and (chunk_index is None or match.get("chunk_index") == chunk_index)
            ),
            None,
        )
        if matched:
            resolved.append(
                {
                    "source": matched.get("source"),
                    "chunk_index": matched.get("chunk_index"),
                    "page_refs": citation.get("page_refs") or matched.get("page_refs") or [],
                    "similarity": matched.get("similarity"),
                    "content": citation.get("quote") or matched.get("content"),
                }
            )
        elif citation:
            resolved.append(
                {
                    "source": citation.get("source"),
                    "chunk_index": citation.get("chunk_index"),
                    "page_refs": citation.get("page_refs") or [],
                    "similarity": None,
                    "content": citation.get("quote") or "",
                }
            )
    return resolved


def build_deferred_context(items: list[dict[str, Any]]) -> str:
    if not items:
        return ""
    rows = []
    for idx, item in enumerate(items[:25], start=1):
        rows.append(
            f"[Deferred Item {idx}]\n"
            f"Source: {item.get('source') or item.get('_source') or 'unknown'}\n"
            f"System: {item.get('system') or 'unknown'}\n"
            f"Item: {item.get('item') or 'unknown'}\n"
            f"Condition: {item.get('condition') or 'unknown'}\n"
            f"Action: {item.get('action') or 'unknown'}\n"
            f"Priority: {item.get('priority') if item.get('priority') is not None else 'unknown'}\n"
            f"Timeline: {item.get('timeline_yr') if item.get('timeline_yr') is not None else 'unknown'}\n"
            f"Cost_k: {item.get('cost_k') if item.get('cost_k') is not None else 'unknown'}"
        )
    return "\n\n".join(rows)


def answer_question_from_matches(
    *,
    question: str,
    matches: list[dict[str, Any]],
    api_key: str,
) -> dict[str, Any]:
    if not matches:
        return {
            "answer": "I couldn't find that in the uploaded document.",
            "citations": [],
            "confidence": "low",
        }

    prompt = (
        f"{QUESTION_ANSWER_PROMPT}\n\n"
        f"Question:\n{question}\n\n"
        f"Retrieved context:\n{build_question_context(matches)}"
    )
    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1400,
        messages=[{"role": "user", "content": prompt}],
    )
    raw_text = response.content[0].text
    try:
        parsed = extract_json_from_response(raw_text)
    except ValueError:
        parsed = {
            "answer": raw_text.strip() or "I couldn't find that in the uploaded document.",
            "citations": [],
            "confidence": "low",
        }
    parsed["_meta"] = {
        "model": "claude-sonnet-4-6",
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }
    return parsed


def answer_building_question(
    *,
    question: str,
    deferred_items: list[dict[str, Any]],
    matches: list[dict[str, Any]],
    api_key: str,
) -> dict[str, Any]:
    if not deferred_items and not matches:
        return {
            "answer": "I couldn't find enough maintenance context to answer that yet.",
            "citations": [],
            "confidence": "low",
        }

    prompt = (
        "You answer questions about a building's maintenance register using only the supplied context.\n\n"
        "Rules:\n"
        "- Prioritize the structured maintenance rows because they are the normalized register.\n"
        "- Use retrieved document excerpts only to support or clarify the answer.\n"
        "- If the answer is not supported by the provided context, say so clearly.\n"
        "- Return ONLY valid JSON in this shape:\n"
        "{\n"
        '  "answer": "<answer text>",\n'
        '  "citations": [\n'
        '    {"type": "deferred_item | document_chunk", "label": "<short label>", "source": "<document/source name>", "page_refs": [1]}\n'
        "  ],\n"
        '  "confidence": "high | medium | low"\n'
        "}\n\n"
        f"Question:\n{question}\n\n"
        f"Deferred maintenance register rows:\n{build_deferred_context(deferred_items) or 'None'}\n\n"
        f"Retrieved document excerpts:\n{build_question_context(matches) or 'None'}"
    )
    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1400,
        messages=[{"role": "user", "content": prompt}],
    )
    raw_text = response.content[0].text
    try:
        parsed = extract_json_from_response(raw_text)
    except ValueError:
        parsed = {"answer": raw_text.strip(), "citations": [], "confidence": "low"}
    parsed["_meta"] = {
        "model": "claude-sonnet-4-6",
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }
    return parsed


def answer_documents_question(
    *,
    question: str,
    matches: list[dict[str, Any]],
    api_key: str,
) -> dict[str, Any]:
    if not matches:
        return {
            "answer": "I couldn't find enough information in the uploaded documents to answer that.",
            "citations": [],
            "confidence": "low",
        }

    prompt = (
        "You answer questions about uploaded building documents using only the supplied retrieved context.\n\n"
        "Rules:\n"
        "- Use only the provided document excerpts.\n"
        "- If the answer is not supported by the excerpts, say so clearly.\n"
        "- Be concise and factual.\n"
        "- Do not use markdown markers like **, bullet symbols, or headings with markdown syntax.\n"
        "- Return ONLY valid JSON in this shape:\n"
        "{\n"
        '  "answer": "<answer text>",\n'
        '  "citations": [\n'
        '    {"source": "<document name>", "chunk_index": 0, "page_refs": [1], "quote": "<short quote>"}\n'
        "  ],\n"
        '  "confidence": "high | medium | low"\n'
        "}\n\n"
        f"Question:\n{question}\n\n"
        f"Retrieved context:\n{build_question_context(matches)}"
    )
    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1400,
        messages=[{"role": "user", "content": prompt}],
    )
    raw_text = response.content[0].text
    try:
        parsed = extract_json_from_response(raw_text)
    except ValueError:
        parsed = {"answer": raw_text.strip(), "citations": [], "confidence": "low"}
    parsed["_meta"] = {
        "model": "claude-sonnet-4-6",
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }
    return parsed


def require_rag_configuration() -> None:
    config = load_rag_config()
    if not is_supabase_configured(config):
        raise HTTPException(
            status_code=500,
            detail="Supabase is not configured. Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY, then apply supabase/schema.sql.",
        )
    if not is_embeddings_configured(config):
        raise HTTPException(
            status_code=500,
            detail="Embeddings are not configured. Set EMBEDDING_API_URL, EMBEDDING_API_KEY, EMBEDDING_MODEL, and EMBEDDING_DIMENSIONS.",
        )


@app.get("/", response_class=HTMLResponse)
def serve_frontend():
    """Serve the BuildingOS frontend app."""
    html_path = Path(__file__).parent / "buildingos-mvp.html"
    if not html_path.exists():
        return HTMLResponse("<h1>buildingos-mvp.html not found</h1>", status_code=404)
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.get("/health")
def health():
    """Health check — also confirms the API key is configured."""
    key = os.getenv("ANTHROPIC_API_KEY", "")
    rag_config = load_rag_config()
    return {
        "status": "ok",
        "api_key_configured": bool(key and key.startswith("sk-ant-")),
        "supabase_configured": is_supabase_configured(rag_config),
        "embeddings_configured": is_embeddings_configured(rag_config),
        "rag_ready": is_rag_ready(rag_config),
    }


# ─────────────────────────────────────────────────────────────
# INTEGRATIONS — Status & OAuth
# ─────────────────────────────────────────────────────────────

GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/drive.metadata.readonly",
]
GOOGLE_REDIRECT_URI = "http://localhost:8000/auth/google/callback"
MICROSOFT_REDIRECT_URI = "http://localhost:8000/auth/microsoft/callback"
MICROSOFT_SCOPES = ["Files.Read", "Files.Read.All", "offline_access"]

# File types to look for in connected drives
BUILDING_DOC_MIME_TYPES = [
    "application/pdf",
    "image/png", "image/jpeg",
]
# Keywords that suggest a building document
BUILDING_DOC_KEYWORDS = [
    "PCA", "condition assessment", "floor plan", "architectural", "MEP",
    "inspection", "maintenance", "lease", "rent roll", "energy audit",
    "specification", "structural", "O&M", "commissioning", "permit",
]


@app.get("/integrations/status")
def integration_status():
    """Check which integrations are connected."""
    return {
        "google_drive": {
            "connected": "google" in _tokens,
            "available": GOOGLE_AVAILABLE,
            "client_configured": bool(os.getenv("GOOGLE_CLIENT_ID")),
        },
        "onedrive": {
            "connected": "microsoft" in _tokens,
            "available": MICROSOFT_AVAILABLE,
            "client_configured": bool(os.getenv("MICROSOFT_CLIENT_ID")),
        },
    }


# ── Google Drive ──────────────────────────────────────────────

@app.get("/auth/google/start")
def google_auth_start():
    """Redirect user to Google OAuth consent screen."""
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    if not GOOGLE_AVAILABLE:
        raise HTTPException(503, "Google auth library not installed. Run: pip install google-auth-oauthlib google-api-python-client")
    if not client_id or not client_secret:
        raise HTTPException(400, "GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET not set in .env")

    state = secrets.token_urlsafe(16)
    _oauth_states[state] = "google"
    client_config = {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [GOOGLE_REDIRECT_URI],
        }
    }
    flow = GoogleFlow.from_client_config(client_config, scopes=GOOGLE_SCOPES, state=state)
    flow.redirect_uri = GOOGLE_REDIRECT_URI
    auth_url, _ = flow.authorization_url(access_type="offline", prompt="consent")
    return RedirectResponse(auth_url)


@app.get("/auth/google/callback")
def google_auth_callback(code: str, state: str):
    """Handle Google OAuth callback."""
    if state not in _oauth_states:
        raise HTTPException(400, "Invalid OAuth state — possible CSRF attempt")
    del _oauth_states[state]
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    client_config = {
        "web": {
            "client_id": client_id, "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [GOOGLE_REDIRECT_URI],
        }
    }
    flow = GoogleFlow.from_client_config(client_config, scopes=GOOGLE_SCOPES)
    flow.redirect_uri = GOOGLE_REDIRECT_URI
    flow.fetch_token(code=code)
    _tokens["google"] = flow.credentials
    logger.info("Google Drive connected successfully")
    return RedirectResponse("http://localhost:8000/?integration=google_connected")


@app.get("/auth/google/disconnect")
def google_disconnect():
    _tokens.pop("google", None)
    return {"status": "disconnected"}


@app.get("/integrations/google-drive/files")
def list_google_drive_files(query: str = ""):
    """List building-relevant PDF files from Google Drive."""
    if "google" not in _tokens:
        raise HTTPException(401, "Google Drive not connected")
    try:
        service = gdrive_build("drive", "v3", credentials=_tokens["google"])
        # Build search query — PDFs + images modified in last 2 years
        q_parts = ["(mimeType='application/pdf' or mimeType='image/png' or mimeType='image/jpeg')", "trashed=false"]
        if query:
            q_parts.append(f"name contains '{query}'")
        q = " and ".join(q_parts)
        results = service.files().list(
            q=q, pageSize=50,
            fields="files(id,name,mimeType,size,modifiedTime,parents)",
            orderBy="modifiedTime desc",
        ).execute()
        files = results.get("files", [])
        # Score files by building-document relevance
        def relevance(f):
            name_lower = f["name"].lower()
            return sum(1 for kw in BUILDING_DOC_KEYWORDS if kw.lower() in name_lower)
        files.sort(key=relevance, reverse=True)
        return {
            "files": [
                {
                    "id": f["id"], "name": f["name"],
                    "size_kb": round(int(f.get("size", 0)) / 1024, 1),
                    "modified": f.get("modifiedTime", "")[:10],
                    "relevance": relevance(f),
                    "mime_type": f["mimeType"],
                }
                for f in files
            ],
            "total": len(files),
        }
    except Exception as e:
        logger.error(f"Google Drive list error: {e}")
        raise HTTPException(500, f"Could not list Google Drive files: {str(e)}")


@app.post("/integrations/google-drive/analyze")
async def analyze_google_drive_files(file_ids: List[str]):
    """Download files from Google Drive and analyze them."""
    if "google" not in _tokens:
        raise HTTPException(401, "Google Drive not connected")
    if not file_ids:
        raise HTTPException(400, "No file IDs provided")

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(500, "ANTHROPIC_API_KEY not configured")

    service = gdrive_build("drive", "v3", credentials=_tokens["google"])
    results = []

    for file_id in file_ids[:20]:  # Cap at 20 files per batch
        try:
            # Get file metadata
            meta = service.files().get(fileId=file_id, fields="name,mimeType,size").execute()
            filename = meta["name"]
            mime_type = meta["mimeType"]

            if mime_type not in BUILDING_DOC_MIME_TYPES:
                results.append({"file_id": file_id, "name": filename, "status": "skipped", "reason": "Unsupported file type"})
                continue

            # Download file content
            request = service.files().get_media(fileId=file_id)
            buf = io.BytesIO()
            downloader = MediaIoBaseDownload(buf, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            file_bytes = buf.getvalue()

            # Run AI analysis (reuse core logic)
            b64_data = base64.standard_b64encode(file_bytes).decode("utf-8")
            msg_type = "document" if mime_type == "application/pdf" else "image"
            doc_content = {
                "type": msg_type,
                "source": {"type": "base64", "media_type": mime_type, "data": b64_data},
            }
            client = anthropic.Anthropic(api_key=api_key)
            response = client.messages.create(
                model="claude-sonnet-4-6", max_tokens=8192,
                messages=[{"role": "user", "content": [doc_content, {"type": "text", "text": ANALYSIS_PROMPT}]}],
            )
            intelligence = extract_json_from_response(response.content[0].text)
            intelligence["_meta"] = {
                "filename": filename, "file_size_kb": round(len(file_bytes) / 1024, 1),
                "content_type": mime_type, "model": "claude-sonnet-4-6",
                "source": "google_drive", "drive_file_id": file_id,
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            }
            results.append({"file_id": file_id, "name": filename, "status": "analyzed", "data": intelligence})
            logger.info(f"Drive file analyzed: {filename}")

        except Exception as e:
            logger.error(f"Error analyzing Drive file {file_id}: {e}")
            results.append({"file_id": file_id, "status": "error", "error": str(e)})

    return {"results": results, "analyzed": sum(1 for r in results if r["status"] == "analyzed")}


# ── Microsoft OneDrive ────────────────────────────────────────

@app.get("/auth/microsoft/start")
def microsoft_auth_start():
    """Redirect user to Microsoft OAuth consent screen."""
    client_id = os.getenv("MICROSOFT_CLIENT_ID")
    tenant_id = os.getenv("MICROSOFT_TENANT_ID", "common")
    if not MICROSOFT_AVAILABLE:
        raise HTTPException(503, "msal not installed. Run: pip install msal")
    if not client_id:
        raise HTTPException(400, "MICROSOFT_CLIENT_ID not set in .env")

    state = secrets.token_urlsafe(16)
    _oauth_states[state] = "microsoft"
    app_msal = msal.PublicClientApplication(client_id, authority=f"https://login.microsoftonline.com/{tenant_id}")
    auth_url = app_msal.get_authorization_request_url(
        scopes=MICROSOFT_SCOPES,
        redirect_uri=MICROSOFT_REDIRECT_URI,
        state=state,
    )
    return RedirectResponse(auth_url)


@app.get("/auth/microsoft/callback")
def microsoft_auth_callback(code: str, state: str):
    """Handle Microsoft OAuth callback."""
    if state not in _oauth_states:
        raise HTTPException(400, "Invalid OAuth state")
    del _oauth_states[state]
    client_id = os.getenv("MICROSOFT_CLIENT_ID")
    client_secret = os.getenv("MICROSOFT_CLIENT_SECRET", "")
    tenant_id = os.getenv("MICROSOFT_TENANT_ID", "common")

    if client_secret:
        app_msal = msal.ConfidentialClientApplication(
            client_id, authority=f"https://login.microsoftonline.com/{tenant_id}",
            client_credential=client_secret,
        )
    else:
        app_msal = msal.PublicClientApplication(client_id, authority=f"https://login.microsoftonline.com/{tenant_id}")

    result = app_msal.acquire_token_by_authorization_code(
        code, scopes=MICROSOFT_SCOPES, redirect_uri=MICROSOFT_REDIRECT_URI
    )
    if "error" in result:
        raise HTTPException(400, f"Microsoft auth error: {result.get('error_description', result['error'])}")
    _tokens["microsoft"] = result
    logger.info("Microsoft OneDrive connected successfully")
    return RedirectResponse("http://localhost:8000/?integration=microsoft_connected")


@app.get("/auth/microsoft/disconnect")
def microsoft_disconnect():
    _tokens.pop("microsoft", None)
    return {"status": "disconnected"}


@app.get("/integrations/onedrive/files")
async def list_onedrive_files(query: str = ""):
    """List building-relevant files from OneDrive."""
    if "microsoft" not in _tokens:
        raise HTTPException(401, "OneDrive not connected")
    import httpx
    token = _tokens["microsoft"].get("access_token")
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with httpx.AsyncClient() as client:
            if query:
                resp = await client.get(
                    f"https://graph.microsoft.com/v1.0/me/drive/root/search(q='{query}')?$top=50&$select=id,name,size,lastModifiedDateTime,file",
                    headers=headers,
                )
            else:
                resp = await client.get(
                    "https://graph.microsoft.com/v1.0/me/drive/root/children?$top=50&$select=id,name,size,lastModifiedDateTime,file&$orderby=lastModifiedDateTime desc",
                    headers=headers,
                )
        resp.raise_for_status()
        items = resp.json().get("value", [])
        files = [i for i in items if i.get("file") and i["file"].get("mimeType") in BUILDING_DOC_MIME_TYPES]

        def relevance(f):
            name_lower = f["name"].lower()
            return sum(1 for kw in BUILDING_DOC_KEYWORDS if kw.lower() in name_lower)

        files.sort(key=relevance, reverse=True)
        return {
            "files": [
                {
                    "id": f["id"], "name": f["name"],
                    "size_kb": round(f.get("size", 0) / 1024, 1),
                    "modified": f.get("lastModifiedDateTime", "")[:10],
                    "relevance": relevance(f),
                    "mime_type": f["file"]["mimeType"],
                }
                for f in files
            ],
            "total": len(files),
        }
    except Exception as e:
        logger.error(f"OneDrive list error: {e}")
        raise HTTPException(500, f"Could not list OneDrive files: {str(e)}")


@app.post("/integrations/onedrive/analyze")
async def analyze_onedrive_files(file_ids: List[str]):
    """Download files from OneDrive and analyze them."""
    if "microsoft" not in _tokens:
        raise HTTPException(401, "OneDrive not connected")
    import httpx
    token = _tokens["microsoft"].get("access_token")
    headers = {"Authorization": f"Bearer {token}"}
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(500, "ANTHROPIC_API_KEY not configured")

    results = []
    async with httpx.AsyncClient() as http_client:
        for file_id in file_ids[:20]:
            try:
                meta_resp = await http_client.get(
                    f"https://graph.microsoft.com/v1.0/me/drive/items/{file_id}?$select=name,file",
                    headers=headers,
                )
                meta = meta_resp.json()
                filename = meta["name"]
                mime_type = meta.get("file", {}).get("mimeType", "application/pdf")

                dl_resp = await http_client.get(
                    f"https://graph.microsoft.com/v1.0/me/drive/items/{file_id}/content",
                    headers=headers, follow_redirects=True,
                )
                file_bytes = dl_resp.content
                b64_data = base64.standard_b64encode(file_bytes).decode("utf-8")
                msg_type = "document" if mime_type == "application/pdf" else "image"
                doc_content = {"type": msg_type, "source": {"type": "base64", "media_type": mime_type, "data": b64_data}}

                client = anthropic.Anthropic(api_key=api_key)
                response = client.messages.create(
                    model="claude-sonnet-4-6", max_tokens=8192,
                    messages=[{"role": "user", "content": [doc_content, {"type": "text", "text": ANALYSIS_PROMPT}]}],
                )
                intelligence = extract_json_from_response(response.content[0].text)
                intelligence["_meta"] = {
                    "filename": filename, "file_size_kb": round(len(file_bytes) / 1024, 1),
                    "content_type": mime_type, "model": "claude-sonnet-4-6",
                    "source": "onedrive", "onedrive_file_id": file_id,
                }
                results.append({"file_id": file_id, "name": filename, "status": "analyzed", "data": intelligence})
            except Exception as e:
                logger.error(f"OneDrive file error {file_id}: {e}")
                results.append({"file_id": file_id, "status": "error", "error": str(e)})

    return {"results": results, "analyzed": sum(1 for r in results if r["status"] == "analyzed")}


# ─────────────────────────────────────────────────────────────
# DOCUMENT ANALYSIS
# ─────────────────────────────────────────────────────────────

@app.get("/documents")
def get_documents(building_id: Optional[str] = Query(default=None)):
    require_rag_configuration()
    client = get_supabase_client(load_rag_config())
    rows = rag_list_documents(client, building_id=building_id)
    return {"documents": [serialize_document_record(row) for row in rows]}


@app.get("/documents/{document_id}/status")
def get_document_status(document_id: str):
    require_rag_configuration()
    client = get_supabase_client(load_rag_config())
    response = client.table("documents").select("*").eq("id", document_id).limit(1).execute()
    row = (response.data or [None])[0]
    if not row:
        raise HTTPException(status_code=404, detail="Document not found.")
    return {"document": serialize_document_record(row)}


@app.delete("/documents/{document_id}")
def delete_document(document_id: str):
    require_rag_configuration()
    rag_config = load_rag_config()
    client = get_supabase_client(rag_config)
    response = client.table("documents").select("*").eq("id", document_id).limit(1).execute()
    row = (response.data or [None])[0]
    if not row:
        raise HTTPException(status_code=404, detail="Document not found.")
    delete_rag_document(client, rag_config, document_id)
    return {"deleted": True, "document_id": document_id}


@app.post("/documents/upload")
async def upload_document(
    file: UploadFile = File(...),
    building_id: Optional[str] = Form(default=None),
):
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="ANTHROPIC_API_KEY not configured. Add it to your .env file.",
        )

    require_rag_configuration()
    rag_config = load_rag_config()
    client = get_supabase_client(rag_config)

    file_bytes = await file.read()
    filename = file.filename or "document"
    return index_document_bytes(
        client=client,
        rag_config=rag_config,
        api_key=api_key,
        file_bytes=file_bytes,
        filename=filename,
        content_type=file.content_type or guess_content_type_from_filename(filename),
        building_id=building_id,
    )


@app.post("/documents/import-shared-link")
async def import_shared_link(body: SharedLinkImportRequest):
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="ANTHROPIC_API_KEY not configured. Add it to your .env file.",
        )

    require_rag_configuration()
    rag_config = load_rag_config()
    client = get_supabase_client(rag_config)

    if is_google_drive_folder_url(body.url.strip()):
        items = await list_google_drive_shared_folder_files(body.url.strip())
        results: list[dict[str, Any]] = []
        
        async def process_single_item(item: dict[str, str]) -> dict[str, Any]:
            try:
                file_bytes, filename, content_type, source_meta = await download_shared_file(item["url"])
                imported = await asyncio.to_thread(
                    index_document_bytes,
                    client=client,
                    rag_config=rag_config,
                    api_key=api_key,
                    file_bytes=file_bytes,
                    filename=item.get("name") or filename,
                    content_type=content_type,
                    building_id=body.building_id,
                    source_meta={
                        **source_meta,
                        "source": "google_drive_folder_link",
                        "folder_url": body.url.strip(),
                        "folder_item_url": item["url"],
                    },
                )
                return {"name": item.get("name"), "status": "analyzed", **imported}
            except HTTPException as exc:
                return {"name": item.get("name"), "status": "error", "error": exc.detail}
            except Exception as exc:
                logger.exception("Folder shared-link import failed for %s", item.get("name"))
                return {"name": item.get("name"), "status": "error", "error": str(exc)}
        
        # Process in parallel batches of 3 to respect rate limits
        BATCH_SIZE = 3
        for i in range(0, len(items), BATCH_SIZE):
            batch = items[i:i + BATCH_SIZE]
            batch_results = await asyncio.gather(*[process_single_item(item) for item in batch])
            results.extend(batch_results)
        
        return {
            "batch": True,
            "folder_url": body.url.strip(),
            "results": results,
            "imported": sum(1 for item in results if item.get("status") == "analyzed"),
        }

    file_bytes, filename, content_type, source_meta = await download_shared_file(body.url.strip())
    return index_document_bytes(
        client=client,
        rag_config=rag_config,
        api_key=api_key,
        file_bytes=file_bytes,
        filename=filename,
        content_type=content_type,
        building_id=body.building_id,
        source_meta=source_meta,
    )


@app.post("/documents/{document_id}/ask")
def ask_document_question(document_id: str, body: DocumentQuestionRequest):
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="ANTHROPIC_API_KEY not configured. Add it to your .env file.",
        )

    require_rag_configuration()
    rag_config = load_rag_config()
    client = get_supabase_client(rag_config)
    response = client.table("documents").select("*").eq("id", document_id).limit(1).execute()
    document = (response.data or [None])[0]
    if not document:
        raise HTTPException(status_code=404, detail="Document not found.")
    if document.get("status") != "ready":
        raise HTTPException(status_code=409, detail="Document is still indexing. Try again shortly.")

    question_embedding = generate_embedding(body.question, rag_config)
    matches = match_document_chunks(
        client,
        document_id=document_id,
        query_embedding=question_embedding,
        match_count=body.match_count,
        match_threshold=body.match_threshold,
    )
    answer = answer_question_from_matches(question=body.question, matches=matches, api_key=api_key)
    sources = [
        {
            "chunk_index": match.get("chunk_index"),
            "page_refs": match.get("page_refs") or [],
            "similarity": match.get("similarity"),
            "content": match.get("content"),
        }
        for match in matches
    ]
    save_document_question(
        client,
        document_id=document_id,
        question=body.question,
        answer=answer.get("answer", ""),
        sources_json=sources,
    )
    return {
        "document": serialize_document_record(document),
        "question": body.question,
        "answer": answer,
        "matches": sources,
    }


@app.post("/buildings/{building_id}/ask-deferred")
def ask_building_deferred_question(building_id: str, body: BuildingQuestionRequest):
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="ANTHROPIC_API_KEY not configured. Add it to your .env file.",
        )

    rag_config = load_rag_config()
    rag_enabled = is_rag_ready(rag_config)
    matches: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    document_ids = list(dict.fromkeys([doc_id for doc_id in body.document_ids if doc_id]))

    if rag_enabled:
        client = get_supabase_client(rag_config)
        if not document_ids:
            building_docs = rag_list_documents(client, building_id=building_id)
            document_ids = [doc["id"] for doc in building_docs if doc.get("status") == "ready"]

        document_map: dict[str, dict[str, Any]] = {}
        if document_ids:
            doc_rows = client.table("documents").select("*").in_("id", document_ids).execute()
            for row in doc_rows.data or []:
                if row.get("status") == "ready":
                    document_map[row["id"]] = row

        if document_map:
            query_embedding = generate_embedding(body.question, rag_config)
            per_doc_limit = max(2, min(4, body.match_count))
            for doc_id, doc_row in document_map.items():
                doc_matches = match_document_chunks(
                    client,
                    document_id=doc_id,
                    query_embedding=query_embedding,
                    match_count=per_doc_limit,
                    match_threshold=body.match_threshold,
                )
                for match in doc_matches:
                    match["source"] = doc_row.get("filename")
                matches.extend(doc_matches)

            matches.sort(key=lambda item: item.get("similarity", 0), reverse=True)
            matches = matches[: body.match_count]
            sources = [
                {
                    "type": "document_chunk",
                    "source": match.get("source"),
                    "chunk_index": match.get("chunk_index"),
                    "page_refs": match.get("page_refs") or [],
                    "similarity": match.get("similarity"),
                    "content": match.get("content"),
                }
                for match in matches
            ]

    answer = answer_building_question(
        question=body.question,
        deferred_items=body.deferred_items,
        matches=matches,
        api_key=api_key,
    )
    return {
        "building_id": building_id,
        "question": body.question,
        "answer": answer,
        "sources": sources,
        "deferred_items_used": len(body.deferred_items),
        "rag_used": bool(matches),
    }


@app.post("/buildings/{building_id}/ask-documents")
def ask_building_documents_question(building_id: str, body: BuildingQuestionRequest):
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="ANTHROPIC_API_KEY not configured. Add it to your .env file.",
        )

    require_rag_configuration()
    rag_config = load_rag_config()
    client = get_supabase_client(rag_config)

    document_ids = list(dict.fromkeys([doc_id for doc_id in body.document_ids if doc_id]))
    if not document_ids:
        building_docs = rag_list_documents(client, building_id=building_id)
        document_ids = [doc["id"] for doc in building_docs if doc.get("status") == "ready"]

    if not document_ids:
        raise HTTPException(status_code=400, detail="No indexed documents are available for this building yet.")

    doc_rows = client.table("documents").select("*").in_("id", document_ids).execute()
    document_map = {row["id"]: row for row in (doc_rows.data or []) if row.get("status") == "ready"}
    if not document_map:
        raise HTTPException(status_code=400, detail="No ready documents were found for this question.")
    document_map = filter_documents_for_question(body.question, document_map)

    query_embedding = generate_embedding(body.question, rag_config)
    matches: list[dict[str, Any]] = []
    per_doc_limit = max(2, min(4, body.match_count))
    for doc_id, doc_row in document_map.items():
        doc_matches = match_document_chunks(
            client,
            document_id=doc_id,
            query_embedding=query_embedding,
            match_count=per_doc_limit,
            match_threshold=body.match_threshold,
        )
        for match in doc_matches:
            match["source"] = doc_row.get("filename")
        matches.extend(doc_matches)

    matches.sort(key=lambda item: item.get("similarity", 0), reverse=True)
    matches = matches[: body.match_count]
    answer = answer_documents_question(question=body.question, matches=matches, api_key=api_key)
    sources = resolve_cited_sources(answer, matches)
    if not sources:
        sources = [
            {
                "source": match.get("source"),
                "chunk_index": match.get("chunk_index"),
                "page_refs": match.get("page_refs") or [],
                "similarity": match.get("similarity"),
                "content": match.get("content"),
            }
            for match in matches
        ]
    return {
        "building_id": building_id,
        "question": body.question,
        "answer": answer,
        "sources": sources,
        "documents_used": len(document_map),
    }


@app.post("/analyze")
async def analyze_document(file: UploadFile = File(...)):
    """
    Upload a building document (PDF or image) and receive
    AI-extracted intelligence across 10 categories.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="ANTHROPIC_API_KEY not configured. Add it to your .env file.",
        )

    file_bytes = await file.read()
    intelligence = analyze_file_bytes(
        file_bytes=file_bytes,
        filename=file.filename or "document",
        content_type=file.content_type or "",
        api_key=api_key,
    )

    logger.info(
        f"Analysis complete: {file.filename} — "
        f"{intelligence.get('assets', {}).get('total', '?')} assets, "
        f"{intelligence.get('compliance', {}).get('gaps', '?')} compliance gaps"
    )

    return JSONResponse(content=intelligence)
