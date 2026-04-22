"""
BuildingOS Field Capture v1.3 — Standalone API
A mobile-first data ingestion tool for field equipment documentation.

v1.3 CHANGES (from EF Capital Pilot #3 feedback, Apr 15):
  • HVAC sub-types: condenser, air handler, PTAC, package unit dropdown
  • Multiple photos per capture: tag photo + unit overview photo
  • Edit-after-save: PATCH captures to correct AI-extracted data post-save
  • Brand vs Manufacturer distinction (e.g., Goodman brand by Daikin manufacturer)
  • Custom equipment types: field users can add types not in the pre-set list
  • User/timestamp stamping on every capture
  • Phase 1 scope: 3 equipment types (HVAC, Water Heater, Electrical Panel)
  • No photo annotation (liability concern)
  • AI prompt updated to extract brand separately from manufacturer

v1.3.1 SECURITY CHANGES (Apr 22):
  • PIN-based authentication (4-6 digit, SHA-256 hashed with email salt)
  • Session tokens (UUID, 7-day expiry) stored in fc_sessions
  • All protected endpoints require valid session via Authorization header
  • Analytics logging (fc_analytics) for key events
  • Admin endpoint for user listing
"""

import base64
import hashlib
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
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("fieldcapture")

# ── Config ──────────────────────────────────────────────────────────────────

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}

SESSION_EXPIRY_DAYS = 7

app = FastAPI(title="BuildingOS Field Capture", version="1.3.1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Supabase helpers ────────────────────────────────────────────────────────

async def sb_get(table: str, filters: str = "") -> list:
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


async def sb_delete(table: str, filters: str) -> list:
    async with httpx.AsyncClient() as client:
        r = await client.delete(
            f"{SUPABASE_URL}/rest/v1/{table}{filters}",
            headers=HEADERS,
        )
        if r.status_code >= 400:
            logger.error(f"sb_delete {table}: {r.status_code} {r.text}")
            return []
        return r.json() if r.text else []


async def sb_upload(bucket: str, path: str, data: bytes, content_type: str = "image/jpeg") -> str:
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


# ── PIN hashing ─────────────────────────────────────────────────────────────

def hash_pin(pin: str, email: str) -> str:
    """SHA-256 hash a PIN with the user's email as salt."""
    salted = f"{email.lower().strip()}:{pin}"
    return hashlib.sha256(salted.encode("utf-8")).hexdigest()


def verify_pin(pin: str, email: str, stored_hash: str) -> bool:
    """Verify a PIN against a stored hash."""
    return hash_pin(pin, email) == stored_hash


# ── Session management ──────────────────────────────────────────────────────

async def create_session(user_id: str, request: Request) -> dict:
    """Create a new session token for a user."""
    token = str(uuid.uuid4())
    now = datetime.utcnow()
    expires = now + timedelta(days=SESSION_EXPIRY_DAYS)

    device_info = request.headers.get("User-Agent", "")
    ip_address = request.client.host if request.client else ""

    session = await sb_post("fc_sessions", {
        "user_id": user_id,
        "token": token,
        "created_at": now.isoformat(),
        "expires_at": expires.isoformat(),
        "device_info": device_info[:500],
        "ip_address": ip_address,
    })
    return {
        "token": token,
        "expires_at": expires.isoformat(),
        "session": session[0] if session else None,
    }


async def verify_session(request: Request) -> dict:
    """Dependency: extract and verify session token from Authorization header.
    Returns the session row (includes user_id).
    """
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, "Missing or invalid Authorization header")

    token = auth[7:].strip()
    if not token:
        raise HTTPException(401, "Empty session token")

    sessions = await sb_get("fc_sessions", f"?token=eq.{token}")
    if not sessions:
        raise HTTPException(401, "Invalid session token")

    session = sessions[0]
    expires_at = session.get("expires_at", "")
    if expires_at:
        try:
            exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00").replace("+00:00", ""))
            if exp < datetime.utcnow():
                raise HTTPException(401, "Session expired")
        except ValueError:
            pass

    return session


# ── Analytics logging ───────────────────────────────────────────────────────

async def log_analytics(
    event_type: str,
    user_id: str = None,
    building_id: str = None,
    metadata: dict = None,
    request: Request = None,
):
    """Fire-and-forget analytics event logging."""
    try:
        event = {
            "event_type": event_type,
            "user_id": user_id,
            "building_id": building_id,
            "metadata": metadata or {},
            "timestamp": datetime.utcnow().isoformat(),
            "device_info": request.headers.get("User-Agent", "")[:500] if request else "",
            "ip_address": (request.client.host if request and request.client else ""),
        }
        await sb_post("fc_analytics", event)
    except Exception as e:
        logger.warning(f"Analytics logging failed: {e}")


# ── Frontend ────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def serve_app():
    html_path = Path(__file__).parent / "index.html"
    if not html_path.exists():
        return HTMLResponse("<h1>Field Capture app not found</h1>", status_code=404)
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.get("/health")
def health():
    return {
        "status": "ok",
        "app": "BuildingOS Field Capture",
        "version": "1.3.1",
        "supabase": bool(SUPABASE_URL),
        "anthropic": bool(ANTHROPIC_KEY),
    }


# ── Database Migration (self-bootstrapping) ────────────────────────────────

_migration_done = False

async def ensure_schema():
    """Ensure required tables/columns exist. Idempotent, runs once per process."""
    global _migration_done
    if _migration_done:
        return
    _migration_done = True

    # Check if fc_sessions table exists
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{SUPABASE_URL}/rest/v1/fc_sessions?select=id&limit=1",
            headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
        )
        sessions_exist = r.status_code != 404 and "PGRST" not in r.text

    if not sessions_exist:
        logger.warning("fc_sessions table missing — schema migration required!")
        logger.warning("Run the following SQL in Supabase SQL Editor:")
        logger.warning("""
-- v1.3.1 Auth Migration
ALTER TABLE fc_users ADD COLUMN IF NOT EXISTS pin_hash text;
ALTER TABLE fc_users ADD COLUMN IF NOT EXISTS last_login_at timestamptz;

CREATE TABLE IF NOT EXISTS fc_sessions (
    id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
    user_id uuid NOT NULL,
    token text NOT NULL UNIQUE,
    created_at timestamptz DEFAULT now(),
    expires_at timestamptz NOT NULL,
    device_info text DEFAULT '',
    ip_address text DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_fc_sessions_token ON fc_sessions(token);
CREATE INDEX IF NOT EXISTS idx_fc_sessions_user_id ON fc_sessions(user_id);

CREATE TABLE IF NOT EXISTS fc_analytics (
    id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
    event_type text NOT NULL,
    user_id uuid,
    building_id text,
    metadata jsonb DEFAULT '{}',
    timestamp timestamptz DEFAULT now(),
    device_info text DEFAULT '',
    ip_address text DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_fc_analytics_event ON fc_analytics(event_type);
CREATE INDEX IF NOT EXISTS idx_fc_analytics_user ON fc_analytics(user_id);
        """)


@app.on_event("startup")
async def startup_event():
    await ensure_schema()


@app.post("/api/admin/migrate")
async def run_migration():
    """Admin endpoint: outputs migration SQL to run in Supabase SQL Editor."""
    return {
        "message": "Run this SQL in your Supabase SQL Editor (supabase.com/dashboard → SQL Editor)",
        "sql": """
-- v1.3.1 Auth Migration — Run in Supabase SQL Editor
-- Step 1: Add columns to fc_users
ALTER TABLE fc_users ADD COLUMN IF NOT EXISTS pin_hash text;
ALTER TABLE fc_users ADD COLUMN IF NOT EXISTS last_login_at timestamptz;

-- Step 2: Create sessions table
CREATE TABLE IF NOT EXISTS fc_sessions (
    id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
    user_id uuid NOT NULL,
    token text NOT NULL UNIQUE,
    created_at timestamptz DEFAULT now(),
    expires_at timestamptz NOT NULL,
    device_info text DEFAULT '',
    ip_address text DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_fc_sessions_token ON fc_sessions(token);
CREATE INDEX IF NOT EXISTS idx_fc_sessions_user_id ON fc_sessions(user_id);

-- Step 3: Create analytics table
CREATE TABLE IF NOT EXISTS fc_analytics (
    id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
    event_type text NOT NULL,
    user_id uuid,
    building_id text,
    metadata jsonb DEFAULT '{}',
    timestamp timestamptz DEFAULT now(),
    device_info text DEFAULT '',
    ip_address text DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_fc_analytics_event ON fc_analytics(event_type);
CREATE INDEX IF NOT EXISTS idx_fc_analytics_user ON fc_analytics(user_id);

-- Step 4: Set PINs for existing users (they'll need to re-register or admin sets PIN)
-- UPDATE fc_users SET pin_hash = encode(sha256(concat(lower(email), ':1234')::bytea), 'hex') WHERE pin_hash IS NULL;
"""
    }


# ═══════════════════════════════════════════════════════════════════════════════
# AUTH ENDPOINTS (v1.3.2 — invite-code login + session)
# ═══════════════════════════════════════════════════════════════════════════════

@app.post("/api/auth/login")
async def login_field_user(request: Request):
    """Login/register with name + email + invite code. The invite code IS the auth.
    - If user exists and has access to a building matching the invite code → session created.
    - If user doesn't exist → auto-register, join building, create session.
    """
    body = await request.json()
    name = body.get("name", "").strip()
    email = body.get("email", "").strip().lower()
    invite_code = body.get("invite_code", "").strip().upper()

    if not email:
        raise HTTPException(400, "Email required")
    if not invite_code:
        raise HTTPException(400, "Invite code required")

    # Validate invite code
    invites = await sb_get("fc_invite_codes", f"?code=eq.{invite_code}&is_active=eq.true")
    if not invites:
        raise HTTPException(404, "Invalid or expired invite code")
    invite = invites[0]
    if invite.get("uses", 0) >= invite.get("max_uses", 10):
        raise HTTPException(410, "Invite code has reached maximum uses")

    # Find or create user
    users = await sb_get("fc_users", f"?email=eq.{email}")
    is_new = False
    if users:
        user = users[0]
        # Update name if provided and different
        if name and name != user.get("name", ""):
            try:
                await sb_patch("fc_users", f"?id=eq.{user['id']}", {"name": name})
                user["name"] = name
            except Exception:
                pass
    else:
        if not name:
            raise HTTPException(400, "Name and email required for new accounts")
        created = await sb_post("fc_users", {
            "name": name,
            "email": email,
            "phone": body.get("phone", ""),
        })
        if not created:
            raise HTTPException(500, "Failed to create account")
        user = created[0]
        is_new = True

    # Ensure user is a member of the building from the invite code
    existing_member = await sb_get(
        "fc_building_members",
        f"?user_id=eq.{user['id']}&building_id=eq.{invite['building_id']}"
    )
    if not existing_member:
        await sb_post("fc_building_members", {
            "user_id": user["id"],
            "building_id": invite["building_id"],
            "invite_code_id": invite["id"],
        })
        await sb_patch("fc_invite_codes", f"?id=eq.{invite['id']}", {"uses": invite.get("uses", 0) + 1})

    # Create session
    session_info = await create_session(user["id"], request)

    # Update last login
    try:
        await sb_patch("fc_users", f"?id=eq.{user['id']}", {"last_login_at": datetime.utcnow().isoformat()})
    except Exception:
        pass

    # Log analytics
    event = "register" if is_new else "login"
    await log_analytics(event, user_id=user["id"], building_id=invite["building_id"], request=request)

    safe_user = {k: v for k, v in user.items() if k != "pin_hash"}
    return {
        "user": safe_user,
        "token": session_info["token"],
        "expires_at": session_info["expires_at"],
        "is_new": is_new,
        "building": {
            "building_id": invite["building_id"],
            "building_name": invite.get("building_name", ""),
            "building_address": invite.get("building_address", ""),
        },
    }


@app.get("/api/auth/verify")
async def verify_auth(session: dict = Depends(verify_session)):
    """Verify a session token is still valid. Returns user info."""
    users = await sb_get("fc_users", f"?id=eq.{session['user_id']}")
    if not users:
        raise HTTPException(401, "User not found")
    safe_user = {k: v for k, v in users[0].items() if k != "pin_hash"}
    return {"valid": True, "user": safe_user}


@app.post("/api/auth/logout")
async def logout(session: dict = Depends(verify_session)):
    """Invalidate the current session."""
    await sb_delete("fc_sessions", f"?token=eq.{session['token']}")
    return {"logged_out": True}


# ═══════════════════════════════════════════════════════════════════════════════
# OFFICE USER ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@app.post("/api/buildings/{building_id}/setup")
async def setup_building_for_field_capture(building_id: str, request: Request):
    body = await request.json()
    building_name = body.get("building_name", "")
    building_address = body.get("building_address", "")
    units = body.get("units", [])
    equipment_types = body.get("equipment_types", [])
    created_by = body.get("created_by", "")

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

    if equipment_types:
        type_rows = [{
            "building_id": building_id,
            "name": et.get("name", ""),
            "icon": et.get("icon", "🔧"),
            "description": et.get("description", ""),
            "sub_types": json.dumps(et.get("sub_types", [])),  # v1.3
            "sort_order": i,
        } for i, et in enumerate(equipment_types)]
        await sb_post("fc_equipment_types", type_rows)

    code = _generate_invite_code()
    await sb_post("fc_invite_codes", {
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
    chars = string.ascii_uppercase + string.digits
    return ''.join(random.choices(chars, k=6))


# ── Building config endpoints ───────────────────────────────────────────────

@app.post("/api/buildings/{building_id}/units")
async def add_units(building_id: str, request: Request, session: dict = Depends(verify_session)):
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
async def list_units(building_id: str, session: dict = Depends(verify_session)):
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
async def add_equipment_types(building_id: str, request: Request, session: dict = Depends(verify_session)):
    body = await request.json()
    types = body if isinstance(body, list) else body.get("equipment_types", [])
    rows = [{
        "building_id": building_id,
        "name": t.get("name", ""),
        "icon": t.get("icon", "🔧"),
        "description": t.get("description", ""),
        "sub_types": json.dumps(t.get("sub_types", [])),  # v1.3
        "sort_order": i,
    } for i, t in enumerate(types)]
    result = await sb_post("fc_equipment_types", rows)
    return {"types_created": len(result), "equipment_types": result}


@app.get("/api/buildings/{building_id}/equipment-types")
async def list_equipment_types(building_id: str, session: dict = Depends(verify_session)):
    types = await sb_get("fc_equipment_types", f"?building_id=eq.{building_id}&order=sort_order")
    return {"equipment_types": types}


# v1.3: Get pre-configured sub-types for equipment categories
@app.get("/api/equipment-sub-types")
async def get_equipment_sub_types(session: dict = Depends(verify_session)):
    """Return pre-configured sub-types for each equipment category."""
    return {"sub_types": EQUIPMENT_SUB_TYPES}


# v1.3: AI equipment identification (no tag visible)
@app.post("/api/identify-equipment")
async def identify_equipment(
    photo: UploadFile = File(...),
    session: dict = Depends(verify_session),
):
    """Send a photo of equipment (no tag) to AI for visual identification."""
    photo_bytes = await photo.read()
    if not photo_bytes:
        raise HTTPException(400, "Empty photo")

    if not ANTHROPIC_KEY:
        raise HTTPException(503, "AI service not configured")

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
                    {"type": "text", "text": EQUIPMENT_IDENTIFICATION_PROMPT},
                ],
            }],
        )

        raw_text = response.content[0].text.strip()
        if raw_text.startswith("```"):
            raw_text = raw_text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        result = json.loads(raw_text)
        return {"identification": result, "success": True}

    except Exception as e:
        logger.error(f"Equipment identification failed: {e}")
        return {"identification": None, "success": False, "error": str(e)}


# v1.3: Custom equipment type creation by field users
@app.post("/api/buildings/{building_id}/equipment-types/custom")
async def add_custom_equipment_type(building_id: str, request: Request, session: dict = Depends(verify_session)):
    """Field user adds a custom equipment type not in the pre-set list."""
    body = await request.json()
    name = body.get("name", "").strip()
    if not name:
        raise HTTPException(400, "Equipment type name required")

    # Check for duplicates
    existing = await sb_get("fc_equipment_types", f"?building_id=eq.{building_id}&name=eq.{name}")
    if existing:
        return {"equipment_type": existing[0], "already_exists": True}

    # Get current max sort_order
    all_types = await sb_get("fc_equipment_types", f"?building_id=eq.{building_id}&order=sort_order.desc&limit=1")
    next_order = (all_types[0]["sort_order"] + 1) if all_types else 0

    result = await sb_post("fc_equipment_types", {
        "building_id": building_id,
        "name": name,
        "icon": body.get("icon", "🔧"),
        "description": body.get("description", ""),
        "sub_types": json.dumps(body.get("sub_types", [])),
        "sort_order": next_order,
        "is_custom": True,
    })
    return {"equipment_type": result[0] if result else None, "already_exists": False}


# ═══════════════════════════════════════════════════════════════════════════════
# FIELD USER ENDPOINTS (protected by session)
# ═══════════════════════════════════════════════════════════════════════════════

@app.post("/api/join/{code}")
async def join_building(code: str, request: Request, session: dict = Depends(verify_session)):
    body = await request.json()
    user_id = body.get("user_id") or session.get("user_id")
    if not user_id:
        raise HTTPException(400, "user_id required")

    invites = await sb_get("fc_invite_codes", f"?code=eq.{code}&is_active=eq.true")
    if not invites:
        raise HTTPException(404, "Invalid or expired invite code")
    invite = invites[0]

    if invite.get("uses", 0) >= invite.get("max_uses", 10):
        raise HTTPException(410, "Invite code has reached maximum uses")

    existing = await sb_get(
        "fc_building_members",
        f"?user_id=eq.{user_id}&building_id=eq.{invite['building_id']}"
    )
    if existing:
        return {"already_member": True, "building_id": invite["building_id"], "building_name": invite["building_name"]}

    await sb_post("fc_building_members", {
        "user_id": user_id,
        "building_id": invite["building_id"],
        "invite_code_id": invite["id"],
    })

    await sb_patch("fc_invite_codes", f"?id=eq.{invite['id']}", {"uses": invite.get("uses", 0) + 1})

    return {
        "joined": True,
        "building_id": invite["building_id"],
        "building_name": invite["building_name"],
        "building_address": invite.get("building_address", ""),
    }


@app.get("/api/users/{user_id}/buildings")
async def list_user_buildings(user_id: str, session: dict = Depends(verify_session)):
    # Ensure user can only see their own buildings
    if session.get("user_id") != user_id:
        raise HTTPException(403, "Access denied")

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

    for b in buildings:
        invites = await sb_get("fc_invite_codes", f"?building_id=eq.{b['building_id']}&limit=1")
        if invites:
            b["building_name"] = invites[0].get("building_name", "")
            b["building_address"] = invites[0].get("building_address", "")

    return {"buildings": buildings}


# ── Walk Sessions ───────────────────────────────────────────────────────────

@app.post("/api/buildings/{building_id}/walks")
async def start_walk(building_id: str, request: Request, session: dict = Depends(verify_session)):
    body = await request.json()
    walk = await sb_post("fc_walk_sessions", {
        "building_id": building_id,
        "user_id": body.get("user_id") or session.get("user_id"),
        "user_name": body.get("user_name", ""),
    })

    await log_analytics("walk_start", user_id=session.get("user_id"), building_id=building_id, request=request)

    return {"walk": walk[0]}


@app.patch("/api/walks/{walk_id}")
async def update_walk(walk_id: str, request: Request, session: dict = Depends(verify_session)):
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

    if body.get("status") == "completed":
        await log_analytics("walk_end", user_id=session.get("user_id"), metadata={"walk_id": walk_id}, request=request)

    return {"walk": result[0] if result else None}


# ── Unit Visits ─────────────────────────────────────────────────────────────

@app.post("/api/buildings/{building_id}/units/{unit_id}/visit")
async def visit_unit(building_id: str, unit_id: str, request: Request, session: dict = Depends(verify_session)):
    body = await request.json()
    visit = await sb_post("fc_unit_visits", {
        "walk_session_id": body.get("walk_session_id"),
        "unit_id": unit_id,
        "building_id": building_id,
        "user_id": body.get("user_id") or session.get("user_id"),
    })
    return {"visit": visit[0]}


@app.patch("/api/visits/{visit_id}")
async def update_visit(visit_id: str, request: Request, session: dict = Depends(verify_session)):
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
# CAPTURE — photo → AI extraction → store
# v1.3: Updated prompt to extract brand vs manufacturer separately
# ═══════════════════════════════════════════════════════════════════════════════

TAG_EXTRACTION_PROMPT = """You are analyzing a photo of an equipment tag/nameplate from a building.
Extract ALL readable information from the tag. Return a JSON object with these fields:

{
  "brand": "brand name visible on the unit (e.g. 'Goodman', 'Carrier', 'Rheem')",
  "make": "parent manufacturer if different from brand (e.g. 'Daikin' makes 'Goodman'). If same as brand, repeat it.",
  "model_name": "model name if visible",
  "model_number": "model/part number",
  "serial_number": "serial number",
  "manufacture_year": "year of manufacture if visible",
  "description": "brief description of the equipment based on what you see",
  "additional_specs": {
    "btu": "BTU rating if visible",
    "voltage": "voltage if visible",
    "amperage": "amperage if visible",
    "phase": "single phase or three phase if visible",
    "refrigerant_type": "refrigerant type if visible (e.g. R-410A, R-22)",
    "refrigerant_charge": "refrigerant charge amount if visible",
    "capacity": "capacity/tonnage if visible",
    "efficiency": "SEER/EER/AFUE/UEF rating if visible",
    "wattage": "wattage if visible",
    "gallons": "tank capacity in gallons if visible (water heaters)",
    "recovery_rate": "recovery rate GPH if visible (water heaters)",
    "breaker_amps": "main breaker amperage if visible (electrical panels)",
    "bus_rating": "bus bar rating if visible (electrical panels)",
    "fuel_type": "gas/electric/heat pump/propane if determinable",
    "min_circuit_amps": "minimum circuit ampacity if visible",
    "max_fuse_size": "maximum fuse/breaker size if visible",
    "weight": "unit weight if visible",
    "dimensions": "physical dimensions if visible"
  },
  "tag_condition": "good/fair/poor/illegible",
  "confidence": "high/medium/low"
}

IMPORTANT:
- "brand" is the name prominently displayed on the equipment (what a field tech sees).
- "make" is the parent manufacturer (may differ — e.g., Goodman brand is made by Daikin).
- If they are the same company, put the same value in both fields.
- Extract EVERY piece of technical data visible on the tag — voltages, amperages, BTU, efficiency ratings, refrigerant type, capacity, etc.
- If a field is not readable, use null. Be precise — don't guess serial numbers.
- Return ONLY valid JSON, no markdown."""


EQUIPMENT_IDENTIFICATION_PROMPT = """You are analyzing a photo of building equipment. There is NO readable tag or nameplate.
Based on the visual appearance of the equipment, identify what it is.

Return a JSON object:
{
  "identified_type": "what this equipment appears to be (e.g. 'HVAC Condenser', 'Tankless Water Heater', 'Electrical Sub-Panel')",
  "equipment_category": "one of: 'HVAC', 'Water Heater', 'Electrical Panel', 'Other'",
  "suggested_sub_type": "specific sub-type (e.g. 'Condenser', 'Air Handler', 'PTAC', 'Tank', 'Tankless', 'Main Panel', 'Sub-Panel')",
  "brand": "brand name if visible on the unit body (not tag), or null",
  "description": "brief description of what you see — color, size, mounting, visible features",
  "estimated_age": "rough age estimate based on appearance if possible, or null",
  "condition_visual": "visual condition assessment: excellent/good/fair/poor",
  "confidence": "high/medium/low — how confident you are in the identification"
}

Be helpful but honest. If you can't tell what it is, say so. Return ONLY valid JSON, no markdown."""


# Pre-configured sub-types for Phase 1 equipment categories
EQUIPMENT_SUB_TYPES = {
    "HVAC": [
        "Condenser (Outdoor Unit)",
        "Air Handler (Indoor Unit)",
        "PTAC (Packaged Terminal AC)",
        "Package Unit (All-in-One)",
        "Mini-Split (Ductless)",
        "Furnace",
        "Heat Pump",
        "Rooftop Unit (RTU)",
        "Thermostat",
    ],
    "Water Heater": [
        "Tank (Gas)",
        "Tank (Electric)",
        "Tankless (Gas)",
        "Tankless (Electric)",
        "Heat Pump Water Heater",
        "Boiler",
        "Recirculation Pump",
    ],
    "Electrical Panel": [
        "Main Panel",
        "Sub-Panel",
        "Disconnect Box",
        "Meter",
        "Transfer Switch",
        "Breaker Panel",
    ],
}


@app.post("/api/buildings/{building_id}/units/{unit_id}/capture")
async def capture_equipment(
    building_id: str,
    unit_id: str,
    photo: UploadFile = File(...),
    equipment_type_id: str = Form(...),
    sub_type: str = Form(None),              # v1.3: HVAC sub-type
    walk_session_id: str = Form(None),
    unit_visit_id: str = Form(None),
    user_id: str = Form(None),
    user_name: str = Form(""),
    condition_rating: str = Form(None),
    condition_notes: str = Form(""),
    tag_readable: bool = Form(True),
    session: dict = Depends(verify_session),
):
    """
    Core capture endpoint:
    1. Upload photo to Supabase Storage
    2. Send to Claude Vision for tag extraction
    3. Store capture record with AI-extracted data
    4. v1.3: Store photo in fc_capture_photos for multi-photo support
    """
    photo_bytes = await photo.read()
    if not photo_bytes:
        raise HTTPException(400, "Empty photo")

    eq_types = await sb_get("fc_equipment_types", f"?id=eq.{equipment_type_id}")
    eq_type_name = eq_types[0]["name"] if eq_types else "unknown"
    eq_type_slug = eq_type_name.lower().replace(" ", "_").replace("/", "_")

    units = await sb_get("fc_units", f"?id=eq.{unit_id}")
    unit_name = units[0]["unit_name"] if units else "unknown"
    unit_slug = unit_name.lower().replace(" ", "_").replace("/", "_")

    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename = f"{building_id}_{unit_slug}_{eq_type_slug}_{timestamp}.jpg"
    storage_path = f"field-capture/{building_id}/{unit_slug}/{filename}"

    # 1. Upload photo
    photo_url = await sb_upload("equipment-photos", storage_path, photo_bytes)

    # 2. AI extraction
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
        "captured_by": user_id or session.get("user_id"),
        "captured_by_name": user_name,
        "sub_type": sub_type,                 # v1.3
        "photo_url": photo_url,
        "photo_storage_path": storage_path,
        "photo_filename": filename,
        "brand": ai_data.get("brand"),        # v1.3
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

    # 4. v1.3: Store tag photo in fc_capture_photos
    if capture.get("id"):
        await sb_post("fc_capture_photos", {
            "capture_id": capture["id"],
            "photo_type": "tag",
            "photo_url": photo_url,
            "photo_storage_path": storage_path,
            "photo_filename": filename,
            "sort_order": 0,
        })

    # Log analytics
    await log_analytics("capture", user_id=session.get("user_id"), building_id=building_id,
                        metadata={"capture_id": capture.get("id"), "equipment_type": eq_type_name}, request=None)

    return {
        "capture": capture,
        "ai_extracted": bool(ai_data),
        "photo_url": photo_url,
        "filename": filename,
    }


# v1.3: Additional photo upload for an existing capture (unit overview, detail, etc.)
@app.post("/api/captures/{capture_id}/photos")
async def add_capture_photo(
    capture_id: str,
    photo: UploadFile = File(...),
    photo_type: str = Form("unit"),  # 'unit', 'detail', 'condition'
    session: dict = Depends(verify_session),
):
    """Upload an additional photo for an existing capture (e.g., unit overview photo)."""
    photo_bytes = await photo.read()
    if not photo_bytes:
        raise HTTPException(400, "Empty photo")

    # Get capture to build storage path
    captures = await sb_get("fc_captures", f"?id=eq.{capture_id}")
    if not captures:
        raise HTTPException(404, "Capture not found")
    cap = captures[0]

    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename = f"{cap['building_id']}_{photo_type}_{timestamp}.jpg"
    storage_path = f"field-capture/{cap['building_id']}/{photo_type}/{filename}"

    photo_url = await sb_upload("equipment-photos", storage_path, photo_bytes)

    # Get next sort_order
    existing_photos = await sb_get("fc_capture_photos", f"?capture_id=eq.{capture_id}&order=sort_order.desc&limit=1")
    next_order = (existing_photos[0]["sort_order"] + 1) if existing_photos else 1

    result = await sb_post("fc_capture_photos", {
        "capture_id": capture_id,
        "photo_type": photo_type,
        "photo_url": photo_url,
        "photo_storage_path": storage_path,
        "photo_filename": filename,
        "sort_order": next_order,
    })

    await log_analytics("photo_upload", user_id=session.get("user_id"), building_id=cap.get("building_id"),
                        metadata={"capture_id": capture_id, "photo_type": photo_type}, request=None)

    return {
        "photo": result[0] if result else None,
        "photo_url": photo_url,
    }


# v1.3: List photos for a capture
@app.get("/api/captures/{capture_id}/photos")
async def list_capture_photos(capture_id: str, session: dict = Depends(verify_session)):
    """Get all photos for a capture."""
    photos = await sb_get("fc_capture_photos", f"?capture_id=eq.{capture_id}&order=sort_order")
    return {"photos": photos}


@app.patch("/api/captures/{capture_id}")
async def update_capture(capture_id: str, request: Request, session: dict = Depends(verify_session)):
    """Update/correct a capture — v1.3: supports edit-after-save, brand field."""
    body = await request.json()
    allowed = [
        "brand", "make", "model_name", "model_number", "serial_number",
        "manufacture_year", "description", "condition_rating", "condition_notes",
        "verification_notes", "manually_verified", "tag_readable",
        "sub_type", "additional_specs",  # v1.3: expanded
    ]
    update = {k: v for k, v in body.items() if k in allowed}
    if body.get("manually_verified"):
        update["verified_at"] = datetime.utcnow().isoformat()
        update["verified_by"] = body.get("verified_by")
    update["updated_at"] = datetime.utcnow().isoformat()

    result = await sb_patch("fc_captures", f"?id=eq.{capture_id}", update)
    return {"capture": result[0] if result else None}


@app.post("/api/buildings/{building_id}/units/{unit_id}/manual-capture")
async def manual_capture(building_id: str, unit_id: str, request: Request, session: dict = Depends(verify_session)):
    """Manual capture when tag is unreadable."""
    body = await request.json()
    capture_record = {
        "building_id": building_id,
        "unit_id": unit_id,
        "equipment_type_id": body.get("equipment_type_id"),
        "walk_session_id": body.get("walk_session_id"),
        "unit_visit_id": body.get("unit_visit_id"),
        "captured_by": body.get("user_id") or session.get("user_id"),
        "captured_by_name": body.get("user_name", ""),
        "sub_type": body.get("sub_type"),     # v1.3
        "brand": body.get("brand"),           # v1.3
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


# ── Progress & Dashboard ────────────────────────────────────────────────────

@app.get("/api/buildings/{building_id}/progress")
async def building_progress(building_id: str, session: dict = Depends(verify_session)):
    units = await sb_get("fc_units", f"?building_id=eq.{building_id}&select=id,unit_name")
    types = await sb_get("fc_equipment_types", f"?building_id=eq.{building_id}&select=id,name,icon")
    captures = await sb_get("fc_captures", f"?building_id=eq.{building_id}&select=id,unit_id,equipment_type_id,captured_by_name,created_at,make,brand,model_name,ai_confidence,manually_verified")

    total_units = len(units)
    total_types = len(types)
    total_expected = total_units * total_types

    unit_captures = {}
    for c in captures:
        uid = c["unit_id"]
        if uid not in unit_captures:
            unit_captures[uid] = set()
        unit_captures[uid].add(c["equipment_type_id"])

    units_complete = sum(1 for uid, types_done in unit_captures.items() if len(types_done) >= total_types)
    units_partial = sum(1 for uid, types_done in unit_captures.items() if 0 < len(types_done) < total_types)
    units_pending = total_units - units_complete - units_partial

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
async def list_captures(building_id: str, unit_id: str = None, session: dict = Depends(verify_session)):
    filters = f"?building_id=eq.{building_id}&order=created_at.desc"
    if unit_id:
        filters += f"&unit_id=eq.{unit_id}"
    captures = await sb_get("fc_captures", filters)
    return {"captures": captures}


# ── Export ──────────────────────────────────────────────────────────────────

@app.get("/api/buildings/{building_id}/export/csv")
async def export_csv(building_id: str, session: dict = Depends(verify_session)):
    """Export all captures as CSV — v1.3: includes brand, sub_type, timestamp."""
    captures = await sb_get("fc_captures", f"?building_id=eq.{building_id}&order=created_at")
    units = await sb_get("fc_units", f"?building_id=eq.{building_id}")
    types = await sb_get("fc_equipment_types", f"?building_id=eq.{building_id}")

    unit_map = {u["id"]: u for u in units}
    type_map = {t["id"]: t for t in types}

    # v1.3: added Brand, Sub-Type, Captured At columns
    lines = ["Unit,Equipment Type,Sub-Type,Brand,Manufacturer,Model,Model Number,Serial Number,Year,Condition,Description,Captured By,Captured At,Verified"]
    for c in captures:
        unit = unit_map.get(c.get("unit_id"), {})
        etype = type_map.get(c.get("equipment_type_id"), {})
        line = ",".join([
            _csv_escape(unit.get("unit_name", "")),
            _csv_escape(etype.get("name", "")),
            _csv_escape(c.get("sub_type", "")),
            _csv_escape(c.get("brand", "")),
            _csv_escape(c.get("make", "")),
            _csv_escape(c.get("model_name", "")),
            _csv_escape(c.get("model_number", "")),
            _csv_escape(c.get("serial_number", "")),
            _csv_escape(c.get("manufacture_year", "")),
            _csv_escape(c.get("condition_rating", "")),
            _csv_escape(c.get("description", "")),
            _csv_escape(c.get("captured_by_name", "")),
            _csv_escape(c.get("created_at", "")),  # v1.3: full timestamp
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


# ═══════════════════════════════════════════════════════════════════════════════
# ANALYTICS ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@app.post("/api/analytics/event")
async def log_analytics_event(request: Request, session: dict = Depends(verify_session)):
    """Log an analytics event from the frontend."""
    body = await request.json()
    await log_analytics(
        event_type=body.get("event_type", "unknown"),
        user_id=session.get("user_id"),
        building_id=body.get("building_id"),
        metadata=body.get("metadata"),
        request=request,
    )
    return {"logged": True}


@app.get("/api/analytics/summary")
async def analytics_summary(session: dict = Depends(verify_session)):
    """Return analytics summary: login counts, active users, captures per day."""
    all_events = await sb_get("fc_analytics", "?order=timestamp.desc&limit=1000")

    login_count = sum(1 for e in all_events if e.get("event_type") == "login")
    register_count = sum(1 for e in all_events if e.get("event_type") == "register")
    capture_count = sum(1 for e in all_events if e.get("event_type") == "capture")
    walk_count = sum(1 for e in all_events if e.get("event_type") == "walk_start")

    active_users = len(set(e.get("user_id") for e in all_events if e.get("user_id")))

    # Captures per day (last 7 days)
    captures_per_day = {}
    for e in all_events:
        if e.get("event_type") == "capture" and e.get("timestamp"):
            day = e["timestamp"][:10]
            captures_per_day[day] = captures_per_day.get(day, 0) + 1

    return {
        "total_logins": login_count,
        "total_registrations": register_count,
        "total_captures": capture_count,
        "total_walks": walk_count,
        "active_users": active_users,
        "captures_per_day": captures_per_day,
        "recent_events": all_events[:20],
    }


# ═══════════════════════════════════════════════════════════════════════════════
# ADMIN ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/api/admin/users")
async def admin_list_users(session: dict = Depends(verify_session)):
    """List all registered users with last login, buildings, capture counts."""
    users = await sb_get("fc_users", "?order=created_at.desc")

    result = []
    for u in users:
        uid = u["id"]
        memberships = await sb_get("fc_building_members", f"?user_id=eq.{uid}")
        captures = await sb_get("fc_captures", f"?captured_by=eq.{uid}&select=id")

        building_names = []
        for m in memberships:
            invites = await sb_get("fc_invite_codes", f"?building_id=eq.{m['building_id']}&limit=1")
            if invites:
                building_names.append(invites[0].get("building_name", m["building_id"]))

        result.append({
            "id": uid,
            "name": u.get("name", ""),
            "email": u.get("email", ""),
            "created_at": u.get("created_at", ""),
            "last_login_at": u.get("last_login_at", ""),
            "building_count": len(memberships),
            "buildings": building_names,
            "capture_count": len(captures),
        })

    return {"users": result}


# ── Run ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8001")))
