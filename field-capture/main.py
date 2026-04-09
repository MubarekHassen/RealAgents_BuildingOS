"""
BuildingOS Field Capture — Standalone API
A mobile-first data ingestion tool for field equipment documentation.
Connects to the same Supabase as the main BuildingOS platform.
"""

import base64
import io
import json
import logging
import os
import random
import string
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

import anthropic
import httpx
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("fieldcapture")

# ─── Config ──────────────────────────────────────────────────────────────────

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}

app = FastAPI(title="BuildingOS Field Capture", version="1.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Supabase helpers ────────────────────────────────────────────────────────

async def sb_get(table: str, filters: str = "") -> list:
    """GET rows from Supabase REST API."""
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{SUPABASE_URL}/rest/v1/{table}{filters}",
            headers={**HEADERS, "Prefer": ""},
        )
        if r.status_code >= 400:
            logger.error(f"sb_get {table}: {r.status_code} {r.text}")
            return []
        return r.json() if r.text else []


async def sb_post(table: str, data: dict | list) -> list:
    """INSERT into Supabase."""
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{SUPABASE_URL}/rest/v1/{table}",
            headers=HEADERS,
            json=data,
        )
        if r.status_code >= 400:
            logger.error(f"sb_post {table}: {r.status_code} {r.text}")
            raise HTTPException(500, f"Database error: {r.text}")
        return r.json() if r.text else []


async def sb_patch(table: str, filters: str, data: dict) -> list:
    """UPDATE rows in Supabase."""
    async with httpx.AsyncClient() as client:
        r = await client.patch(
            f"{SUPABASE_URL}/rest/v1/{table}{filters}",
            headers=HEADERS,
            json=data,
        )
        if r.status_code >= 400:
            logger.error(f"sb_patch {table}: {r.status_code} {r.text}")
            raise HTTPException(500, f"Database error: {r.text}")
        return r.json() if r.text else []


async def sb_upload(bucket: str, path: str, data: bytes, content_type: str = "image/jpeg") -> str:
    """Upload file to Supabase Storage and return public URL."""
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{SUPABASE_URL}/storage/v1/object/{bucket}/{path}",
            headers={
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "Content-Type": content_type,
            },
            content=data,
        )
        if r.status_code >= 400:
            # Try upsert if already exists
            r = await client.put(
                f"{SUPABASE_URL}/storage/v1/object/{bucket}/{path}",
                headers={
                    "apikey": SUPABASE_KEY,
                    "Authorization": f"Bearer {SUPABASE_KEY}",
                    "Content-Type": content_type,
                },
                content=data,
            )
        if r.status_code >= 400:
            logger.error(f"Storage upload failed: {r.status_code} {r.text}")
            raise HTTPException(500, f"Photo upload failed: {r.text}")

    return f"{SUPABASE_URL}/storage/v1/object/public/{bucket}/{path}"


# ─── Frontend ────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def serve_app():
    """Serve the Field Capture mobile-first app."""
    html_path = Path(__file__).parent / "index.html"
    if not html_path.exists():
        return HTMLResponse("<h1>Field Capture app not found</h1>", status_code=404)
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.get("/health")
def health():
    return {
        "status": "ok",
        "app": "BuildingOS Field Capture",
        "version": "1.2.0",
        "supabase": bool(SUPABASE_URL),
        "anthropic": bool(ANTHROPIC_KEY),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# OFFICE USER ENDPOINTS (Matt sets up building, units, equipment types, invites)
# ═══════════════════════════════════════════════════════════════════════════════

@app.post("/api/buildings/{building_id}/setup")
async def setup_building_for_field_capture(building_id: str, request: Request):
    """
    Office user (Matt) sets up a building for field capture.
    Creates units and equipment types, generates invite code.
    Called from the main BuildingOS platform.
    """
    body = await request.json()
    building_name = body.get("building_name", "")
    building_address = body.get("building_address", "")
    units = body.get("units", [])            # [{name, floor, unit_type, sqft, bedrooms, bathrooms}]
    equipment_types = body.get("equipment_types", [])  # [{name, icon, description}]
    created_by = body.get("created_by", "")

    # Insert units
    if units:
        unit_rows = [{
            "building_id": building_id,
            "unit_name": u.get("name", u.get("unit_name", "")),
            "floor": u.get("floor", ""),
            "unit_type": u.get("unit_type", "residential"),
            "sqft": u.get("sqft"),
            "bedrooms": u.get("bedrooms"),
            "bathrooms": u.get("bathrooms"),
            "notes": u.get("notes", ""),
            "sort_order": i,
        } for i, u in enumerate(units)]
        await sb_post("fc_units", unit_rows)

    # Insert equipment types
    if equipment_types:
        type_rows = [{
            "building_id": building_id,
            "name": et.get("name", ""),
            "icon": et.get("icon", "🔧"),
            "description": et.get("description", ""),
            "sort_order": i,
        } for i, et in enumerate(equipment_types)]
        await sb_post("fc_equipment_types", type_rows)

    # Use invite code from main app if provided, otherwise generate one
    code = body.get("invite_code") or _generate_invite_code()
    invite = await sb_post("fc_invite_codes", {
        "code": code,
        "building_id": building_id,
        "building_name": building_name,
        "building_address": building_address,
        "created_by": created_by,
        "max_uses": 10,
        "expires_at": (datetime.utcnow() + timedelta(days=30)).isoformat(),
    })

    return {
        "building_id": building_id,
        "units_created": len(units),
        "equipment_types_created": len(equipment_types),
        "invite_code": code,
        "invite_url": f"/join/{code}",
    }


@app.post("/api/invite-codes")
async def create_invite_code(request: Request):
    """Generate an invite code for a building."""
    body = await request.json()
    code = _generate_invite_code()
    invite = await sb_post("fc_invite_codes", {
        "code": code,
        "building_id": body["building_id"],
        "building_name": body.get("building_name", ""),
        "building_address": body.get("building_address", ""),
        "created_by": body.get("created_by", ""),
        "max_uses": body.get("max_uses", 10),
        "expires_at": (datetime.utcnow() + timedelta(days=30)).isoformat(),
    })
    return {"code": code, "invite": invite[0] if invite else None}


@app.get("/api/invite-codes/{code}")
async def validate_invite_code(code: str):
    """Validate an invite code and return building info."""
    rows = await sb_get("fc_invite_codes", f"?code=eq.{code}&is_active=eq.true")
    if not rows:
        raise HTTPException(404, "Invalid or expired invite code")
    invite = rows[0]
    if invite.get("uses", 0) >= invite.get("max_uses", 10):
        raise HTTPException(410, "Invite code has reached maximum uses")
    return {
        "valid": True,
        "building_id": invite["building_id"],
        "building_name": invite["building_name"],
        "building_address": invite.get("building_address", ""),
    }


def _generate_invite_code() -> str:
    """Generate a 6-character alphanumeric invite code."""
    chars = string.ascii_uppercase + string.digits
    return ''.join(random.choices(chars, k=6))


# ─── Building config endpoints ───────────────────────────────────────────────

@app.post("/api/buildings/{building_id}/units")
async def add_units(building_id: str, request: Request):
    """Add units to a building."""
    body = await request.json()
    units = body if isinstance(body, list) else body.get("units", [])
    rows = [{
        "building_id": building_id,
        "unit_name": u.get("name", u.get("unit_name", "")),
        "floor": u.get("floor", ""),
        "unit_type": u.get("unit_type", "residential"),
        "sqft": u.get("sqft"),
        "bedrooms": u.get("bedrooms"),
        "bathrooms": u.get("bathrooms"),
        "sort_order": i,
    } for i, u in enumerate(units)]
    result = await sb_post("fc_units", rows)
    return {"units_created": len(result), "units": result}


@app.get("/api/buildings/{building_id}/units")
async def list_units(building_id: str):
    """List all units for a building with capture progress."""
    units = await sb_get("fc_units", f"?building_id=eq.{building_id}&order=sort_order,unit_name")
    equip_types = await sb_get("fc_equipment_types", f"?building_id=eq.{building_id}&order=sort_order")
    captures = await sb_get("fc_captures", f"?building_id=eq.{building_id}&select=id,unit_id,equipment_type_id")

    type_count = len(equip_types)
    capture_map = {}
    for c in captures:
        uid = c.get("unit_id")
        if uid not in capture_map:
            capture_map[uid] = set()
        capture_map[uid].add(c.get("equipment_type_id"))

    enriched = []
    for u in units:
        captured_types = capture_map.get(u["id"], set())
        enriched.append({
            **u,
            "captures_done": len(captured_types),
            "captures_total": type_count,
            "is_complete": len(captured_types) >= type_count if type_count > 0 else False,
        })

    return {"units": enriched, "equipment_type_count": type_count}


@app.post("/api/buildings/{building_id}/equipment-types")
async def add_equipment_types(building_id: str, request: Request):
    """Add equipment types to a building."""
    body = await request.json()
    types = body if isinstance(body, list) else body.get("equipment_types", [])
    rows = [{
        "building_id": building_id,
        "name": t.get("name", ""),
        "icon": t.get("icon", "🔧"),
        "description": t.get("description", ""),
        "sort_order": i,
    } for i, t in enumerate(types)]
    result = await sb_post("fc_equipment_types", rows)
    return {"types_created": len(result), "equipment_types": result}


@app.get("/api/buildings/{building_id}/equipment-types")
async def list_equipment_types(building_id: str):
    """List equipment types for a building."""
    types = await sb_get("fc_equipment_types", f"?building_id=eq.{building_id}&order=sort_order")
    return {"equipment_types": types}


# ═══════════════════════════════════════════════════════════════════════════════
# FIELD USER ENDPOINTS (Eric joins, walks, captures)
# ═══════════════════════════════════════════════════════════════════════════════

@app.post("/api/auth/register")
async def register_field_user(request: Request):
    """Register a new field user."""
    body = await request.json()
    name = body.get("name", "").strip()
    email = body.get("email", "").strip().lower()
    if not name or not email:
        raise HTTPException(400, "Name and email required")

    # Check if user exists
    existing = await sb_get("fc_users", f"?email=eq.{email}")
    if existing:
        return {"user": existing[0], "is_new": False}

    user = await sb_post("fc_users", {
        "name": name,
        "email": email,
        "phone": body.get("phone", ""),
    })
    return {"user": user[0], "is_new": True}


@app.post("/api/auth/login")
async def login_field_user(request: Request):
    """Login an existing field user by email."""
    body = await request.json()
    email = body.get("email", "").strip().lower()
    users = await sb_get("fc_users", f"?email=eq.{email}")
    if not users:
        raise HTTPException(404, "No account found with that email")
    return {"user": users[0]}


@app.post("/api/join/{code}")
async def join_building(code: str, request: Request):
    """Field user joins a building using invite code."""
    body = await request.json()
    user_id = body.get("user_id")
    if not user_id:
        raise HTTPException(400, "user_id required")

    # Validate code
    invites = await sb_get("fc_invite_codes", f"?code=eq.{code}&is_active=eq.true")
    if not invites:
        raise HTTPException(404, "Invalid or expired invite code")
    invite = invites[0]

    if invite.get("uses", 0) >= invite.get("max_uses", 10):
        raise HTTPException(410, "Invite code has reached maximum uses")

    # Check if already a member
    existing = await sb_get(
        "fc_building_members",
        f"?user_id=eq.{user_id}&building_id=eq.{invite['building_id']}"
    )
    if existing:
        return {"already_member": True, "building_id": invite["building_id"], "building_name": invite["building_name"]}

    # Create membership
    await sb_post("fc_building_members", {
        "user_id": user_id,
        "building_id": invite["building_id"],
        "invite_code_id": invite["id"],
    })

    # Increment usage
    await sb_patch("fc_invite_codes", f"?id=eq.{invite['id']}", {"uses": invite.get("uses", 0) + 1})

    return {
        "joined": True,
        "building_id": invite["building_id"],
        "building_name": invite["building_name"],
        "building_address": invite.get("building_address", ""),
    }


@app.get("/api/users/{user_id}/buildings")
async def list_user_buildings(user_id: str):
    """List buildings a field user belongs to."""
    memberships = await sb_get("fc_building_members", f"?user_id=eq.{user_id}")
    buildings = []
    for m in memberships:
        bid = m["building_id"]
        units = await sb_get("fc_units", f"?building_id=eq.{bid}&select=id")
        types = await sb_get("fc_equipment_types", f"?building_id=eq.{bid}&select=id")
        captures = await sb_get("fc_captures", f"?building_id=eq.{bid}&select=id")
        total_expected = len(units) * len(types)
        buildings.append({
            "building_id": bid,
            "role": m.get("role", "field_tech"),
            "joined_at": m.get("joined_at"),
            "unit_count": len(units),
            "equipment_type_count": len(types),
            "captures_done": len(captures),
            "captures_total": total_expected,
        })

    # Enrich with invite code info (building name)
    for b in buildings:
        invites = await sb_get("fc_invite_codes", f"?building_id=eq.{b['building_id']}&limit=1")
        if invites:
            b["building_name"] = invites[0].get("building_name", "")
            b["building_address"] = invites[0].get("building_address", "")

    return {"buildings": buildings}


# ─── Walk Sessions ───────────────────────────────────────────────────────────

@app.post("/api/buildings/{building_id}/walks")
async def start_walk(building_id: str, request: Request):
    """Start a new walk session."""
    body = await request.json()
    walk = await sb_post("fc_walk_sessions", {
        "building_id": building_id,
        "user_id": body.get("user_id"),
        "user_name": body.get("user_name", ""),
    })
    return {"walk": walk[0]}


@app.patch("/api/walks/{walk_id}")
async def update_walk(walk_id: str, request: Request):
    """Update walk session (complete, pause, add notes)."""
    body = await request.json()
    update = {}
    if "status" in body:
        update["status"] = body["status"]
        if body["status"] == "completed":
            update["completed_at"] = datetime.utcnow().isoformat()
    if "notes" in body:
        update["notes"] = body["notes"]
    if "units_visited" in body:
        update["units_visited"] = body["units_visited"]
    if "captures_made" in body:
        update["captures_made"] = body["captures_made"]

    result = await sb_patch("fc_walk_sessions", f"?id=eq.{walk_id}", update)
    return {"walk": result[0] if result else None}


# ─── Unit Visits ─────────────────────────────────────────────────────────────

@app.post("/api/buildings/{building_id}/units/{unit_id}/visit")
async def visit_unit(building_id: str, unit_id: str, request: Request):
    """Record that a field user is visiting a unit."""
    body = await request.json()
    visit = await sb_post("fc_unit_visits", {
        "walk_session_id": body.get("walk_session_id"),
        "unit_id": unit_id,
        "building_id": building_id,
        "user_id": body.get("user_id"),
    })
    return {"visit": visit[0]}


@app.patch("/api/visits/{visit_id}")
async def update_visit(visit_id: str, request: Request):
    """Update unit visit (complete, skip, access issue)."""
    body = await request.json()
    update = {}
    if "status" in body:
        update["status"] = body["status"]
        if body["status"] in ("completed", "skipped", "access_issue"):
            update["completed_at"] = datetime.utcnow().isoformat()
    if "access_note" in body:
        update["access_note"] = body["access_note"]
    result = await sb_patch("fc_unit_visits", f"?id=eq.{visit_id}", update)
    return {"visit": result[0] if result else None}


# ═══════════════════════════════════════════════════════════════════════════════
# CAPTURE — The core: photo → AI extraction → store
# ═══════════════════════════════════════════════════════════════════════════════

TAG_EXTRACTION_PROMPT = """You are analyzing a photo of an equipment tag/nameplate from a building.
Extract ALL readable information from the tag. Return a JSON object with these fields:

{
  "make": "manufacturer/brand name",
  "model_name": "model name if visible",
  "model_number": "model/part number",
  "serial_number": "serial number",
  "manufacture_year": "year of manufacture if visible",
  "description": "brief description of the equipment based on what you see",
  "additional_specs": {
    "btu": "BTU rating if visible",
    "voltage": "voltage if visible",
    "amperage": "amperage if visible",
    "refrigerant_type": "refrigerant type if visible",
    "capacity": "capacity/tonnage if visible",
    "efficiency": "SEER/EER/AFUE if visible"
  },
  "tag_condition": "good/fair/poor/illegible",
  "confidence": "high/medium/low"
}

If a field is not readable, use null. Be precise — don't guess serial numbers.
Return ONLY valid JSON, no markdown."""


@app.post("/api/buildings/{building_id}/units/{unit_id}/capture")
async def capture_equipment(
    building_id: str,
    unit_id: str,
    photo: UploadFile = File(...),
    equipment_type_id: str = Form(...),
    walk_session_id: str = Form(None),
    unit_visit_id: str = Form(None),
    user_id: str = Form(None),
    user_name: str = Form(""),
    condition_rating: str = Form(None),
    condition_notes: str = Form(""),
    tag_readable: bool = Form(True),
):
    """
    Core capture endpoint:
    1. Upload photo to Supabase Storage
    2. Send to Claude Vision for tag extraction
    3. Store capture record with AI-extracted data
    """
    # Read photo
    photo_bytes = await photo.read()
    if not photo_bytes:
        raise HTTPException(400, "Empty photo")

    # Get equipment type name for filename
    eq_types = await sb_get("fc_equipment_types", f"?id=eq.{equipment_type_id}")
    eq_type_name = eq_types[0]["name"] if eq_types else "unknown"
    eq_type_slug = eq_type_name.lower().replace(" ", "_").replace("/", "_")

    # Get unit name for filename
    units = await sb_get("fc_units", f"?id=eq.{unit_id}")
    unit_name = units[0]["unit_name"] if units else "unknown"
    unit_slug = unit_name.lower().replace(" ", "_").replace("/", "_")

    # Generate clean filename: {building}_{unit}_{type}_{timestamp}.jpg
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename = f"{building_id}_{unit_slug}_{eq_type_slug}_{timestamp}.jpg"
    storage_path = f"field-capture/{building_id}/{unit_slug}/{filename}"

    # 1. Upload photo to Supabase Storage
    photo_url = await sb_upload("equipment-photos", storage_path, photo_bytes)

    # 2. AI extraction (if tag is readable)
    ai_data = {}
    ai_confidence = None
    ai_raw = None

    if tag_readable and ANTHROPIC_KEY:
        try:
            client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
            b64_photo = base64.b64encode(photo_bytes).decode("utf-8")
            media_type = photo.content_type or "image/jpeg"

            response = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=1024,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {"type": "base64", "media_type": media_type, "data": b64_photo},
                        },
                        {"type": "text", "text": TAG_EXTRACTION_PROMPT},
                    ],
                }],
            )

            raw_text = response.content[0].text.strip()
            # Clean up response — remove markdown code fences if present
            if raw_text.startswith("```"):
                raw_text = raw_text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

            ai_data = json.loads(raw_text)
            ai_confidence = ai_data.get("confidence", "medium")
            ai_raw = ai_data
            logger.info(f"AI extraction successful for {filename}: confidence={ai_confidence}")

        except Exception as e:
            logger.error(f"AI extraction failed: {e}")
            ai_data = {}
            ai_confidence = "failed"

    # 3. Store capture record
    capture_record = {
        "building_id": building_id,
        "unit_id": unit_id,
        "unit_visit_id": unit_visit_id,
        "equipment_type_id": equipment_type_id,
        "walk_session_id": walk_session_id,
        "captured_by": user_id,
        "captured_by_name": user_name,
        "photo_url": photo_url,
        "photo_storage_path": storage_path,
        "photo_filename": filename,
        "make": ai_data.get("make"),
        "model_name": ai_data.get("model_name"),
        "model_number": ai_data.get("model_number"),
        "serial_number": ai_data.get("serial_number"),
        "manufacture_year": ai_data.get("manufacture_year"),
        "description": ai_data.get("description"),
        "additional_specs": ai_data.get("additional_specs", {}),
        "ai_confidence": ai_confidence,
        "ai_raw_response": ai_raw,
        "condition_rating": condition_rating,
        "condition_notes": condition_notes,
        "tag_readable": tag_readable,
    }

    result = await sb_post("fc_captures", capture_record)
    capture = result[0] if result else capture_record

    return {
        "capture": capture,
        "ai_extracted": bool(ai_data),
        "photo_url": photo_url,
        "filename": filename,
    }


@app.patch("/api/captures/{capture_id}")
async def update_capture(capture_id: str, request: Request):
    """Update/correct a capture (manual verification after AI extraction)."""
    body = await request.json()
    allowed = [
        "make", "model_name", "model_number", "serial_number", "manufacture_year",
        "description", "condition_rating", "condition_notes", "verification_notes",
        "manually_verified", "tag_readable",
    ]
    update = {k: v for k, v in body.items() if k in allowed}
    if body.get("manually_verified"):
        update["verified_at"] = datetime.utcnow().isoformat()
        update["verified_by"] = body.get("verified_by")
    update["updated_at"] = datetime.utcnow().isoformat()

    result = await sb_patch("fc_captures", f"?id=eq.{capture_id}", update)
    return {"capture": result[0] if result else None}


@app.post("/api/buildings/{building_id}/units/{unit_id}/manual-capture")
async def manual_capture(building_id: str, unit_id: str, request: Request):
    """Manual capture when tag is unreadable — field user types data directly."""
    body = await request.json()
    capture_record = {
        "building_id": building_id,
        "unit_id": unit_id,
        "equipment_type_id": body.get("equipment_type_id"),
        "walk_session_id": body.get("walk_session_id"),
        "unit_visit_id": body.get("unit_visit_id"),
        "captured_by": body.get("user_id"),
        "captured_by_name": body.get("user_name", ""),
        "make": body.get("make"),
        "model_name": body.get("model_name"),
        "model_number": body.get("model_number"),
        "serial_number": body.get("serial_number"),
        "manufacture_year": body.get("manufacture_year"),
        "description": body.get("description"),
        "condition_rating": body.get("condition_rating"),
        "condition_notes": body.get("condition_notes"),
        "tag_readable": False,
        "manually_verified": True,
        "verified_at": datetime.utcnow().isoformat(),
    }
    result = await sb_post("fc_captures", capture_record)
    return {"capture": result[0] if result else capture_record}


# ─── Progress & Dashboard ────────────────────────────────────────────────────

@app.get("/api/buildings/{building_id}/progress")
async def building_progress(building_id: str):
    """Get overall capture progress for a building."""
    units = await sb_get("fc_units", f"?building_id=eq.{building_id}&select=id,unit_name")
    types = await sb_get("fc_equipment_types", f"?building_id=eq.{building_id}&select=id,name,icon")
    captures = await sb_get("fc_captures", f"?building_id=eq.{building_id}&select=id,unit_id,equipment_type_id,captured_by_name,created_at,make,model_name,ai_confidence,manually_verified")

    total_units = len(units)
    total_types = len(types)
    total_expected = total_units * total_types

    # Per-unit completion
    unit_captures = {}
    for c in captures:
        uid = c["unit_id"]
        if uid not in unit_captures:
            unit_captures[uid] = set()
        unit_captures[uid].add(c["equipment_type_id"])

    units_complete = sum(1 for uid, types_done in unit_captures.items() if len(types_done) >= total_types)
    units_partial = sum(1 for uid, types_done in unit_captures.items() if 0 < len(types_done) < total_types)
    units_pending = total_units - units_complete - units_partial

    # Per-type progress
    type_progress = []
    for t in types:
        done = sum(1 for c in captures if c["equipment_type_id"] == t["id"])
        type_progress.append({
            "id": t["id"],
            "name": t["name"],
            "icon": t.get("icon", "🔧"),
            "captured": done,
            "total": total_units,
            "pct": round(done / total_units * 100) if total_units > 0 else 0,
        })

    # Recent activity
    recent = sorted(captures, key=lambda c: c.get("created_at", ""), reverse=True)[:10]

    return {
        "total_units": total_units,
        "total_equipment_types": total_types,
        "total_captures": len(captures),
        "total_expected": total_expected,
        "completion_pct": round(len(captures) / total_expected * 100) if total_expected > 0 else 0,
        "units_complete": units_complete,
        "units_partial": units_partial,
        "units_pending": units_pending,
        "type_progress": type_progress,
        "recent_captures": recent,
    }


@app.get("/api/buildings/{building_id}/captures")
async def list_captures(building_id: str, unit_id: str = None):
    """List all captures for a building, optionally filtered by unit."""
    filters = f"?building_id=eq.{building_id}&order=created_at.desc"
    if unit_id:
        filters += f"&unit_id=eq.{unit_id}"
    captures = await sb_get("fc_captures", filters)
    return {"captures": captures}


# ─── Export ──────────────────────────────────────────────────────────────────

@app.get("/api/buildings/{building_id}/export/csv")
async def export_csv(building_id: str):
    """Export all captures as CSV for AppFolio or other systems."""
    captures = await sb_get("fc_captures", f"?building_id=eq.{building_id}&order=created_at")
    units = await sb_get("fc_units", f"?building_id=eq.{building_id}")
    types = await sb_get("fc_equipment_types", f"?building_id=eq.{building_id}")

    unit_map = {u["id"]: u for u in units}
    type_map = {t["id"]: t for t in types}

    lines = ["Unit,Equipment Type,Make,Model,Model Number,Serial Number,Year,Condition,Description,Captured By,Date,Verified"]
    for c in captures:
        unit = unit_map.get(c.get("unit_id"), {})
        etype = type_map.get(c.get("equipment_type_id"), {})
        line = ",".join([
            _csv_escape(unit.get("unit_name", "")),
            _csv_escape(etype.get("name", "")),
            _csv_escape(c.get("make", "")),
            _csv_escape(c.get("model_name", "")),
            _csv_escape(c.get("model_number", "")),
            _csv_escape(c.get("serial_number", "")),
            _csv_escape(c.get("manufacture_year", "")),
            _csv_escape(c.get("condition_rating", "")),
            _csv_escape(c.get("description", "")),
            _csv_escape(c.get("captured_by_name", "")),
            _csv_escape(c.get("created_at", "")[:10] if c.get("created_at") else ""),
            "Yes" if c.get("manually_verified") else "No",
        ])
        lines.append(line)

    csv_content = "\n".join(lines)
    return StreamingResponse(
        io.StringIO(csv_content),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=field_capture_{building_id}.csv"},
    )


def _csv_escape(val: str) -> str:
    if not val:
        return ""
    val = str(val)
    if "," in val or '"' in val or "\n" in val:
        return '"' + val.replace('"', '""') + '"'
    return val


# ─── Run ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8001")))
