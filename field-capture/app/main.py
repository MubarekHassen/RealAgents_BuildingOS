"""
BuildingOS Field Capture v1.4 — Standalone API
A mobile-first data ingestion tool for field equipment documentation.

v1.4 CHANGES (from EF Capital Pilot, Apr 22):
  • Full unit inspection flow: 27-field Inspection Log capture per unit
  • POST/GET inspection endpoints for unit-level inspections
  • GET building inspections list
  • Inspection photo uploads (fc_inspection_photos)
  • Maintenance items endpoint: auto-extracts work orders from inspections
  • 13-stop room walkthrough constant (INSPECTION_STOPS)
  • v1.4 migration endpoint for new tables
  • Bug fix: fc_capture_photos wrapped in try/except to prevent crashes

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
import re
import secrets
import string
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

import anthropic
import bcrypt
import httpx
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

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


def _validate_config():
    missing = []
    if not SUPABASE_URL:
        missing.append("SUPABASE_URL")
    if not SUPABASE_KEY:
        missing.append("SUPABASE_SERVICE_ROLE_KEY")
    if not ANTHROPIC_KEY:
        missing.append("ANTHROPIC_API_KEY")
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")

_validate_config()


def _safe_id(val: str) -> str:
    """Sanitize a value for use in Supabase PostgREST filters. Only allow alphanumeric, hyphens, underscores, dots, @."""
    val = str(val).strip()
    if not re.match(r'^[\w\-\.@]+$', val):
        raise HTTPException(400, "Invalid input characters")
    if len(val) > 255:
        raise HTTPException(400, "Input too long")
    return val


ALLOWED_ORIGINS = os.getenv("CORS_ORIGINS", "https://buildingos-fieldcapture-production.up.railway.app,https://buildos.it,https://www.buildos.it,http://localhost:8000,http://localhost:3000").split(",")

app = FastAPI(title="BuildingOS Field Capture", version="1.4.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(status_code=429, content={"detail": "Too many requests. Please wait before trying again."})


# ── v1.4: 13-stop room walkthrough ────────────────────────────────────────

INSPECTION_STOPS = [
    {"order": 0, "name": "Front Door", "icon": "\U0001f6aa", "photos": "Photo of front door + unit number sign", "notes": "Verification shot. Note if tenant present."},
    {"order": 1, "name": "Kitchen", "icon": "\U0001f373", "photos": "Wide shot + close-ups of any damage", "notes": "Cabinets, countertops, appliances, flooring condition."},
    {"order": 2, "name": "Family Room / Living", "icon": "\U0001f6cb\ufe0f", "photos": "Wide shot from doorway", "notes": "Walls, ceiling, windows, general condition."},
    {"order": 3, "name": "Backyard - HVAC Unit", "icon": "\u2744\ufe0f", "photos": "Photo of outdoor condenser + model/serial plate", "notes": "Note make/model, visible condition, age."},
    {"order": 4, "name": "Backyard - Electrical Panel", "icon": "\u26a1", "photos": "Photo of panel open + laundry room", "notes": "Panel brand, breaker count, laundry room condition."},
    {"order": 5, "name": "Backyard - Exterior", "icon": "\U0001f3e0", "photos": "Photos of back of unit, fencing, siding", "notes": "Siding damage, fence condition, drainage."},
    {"order": 6, "name": "1F Hallway", "icon": "\U0001f6b6", "photos": "Wide shot down hallway", "notes": "Flooring, walls, lighting."},
    {"order": 7, "name": "1F Water Heater", "icon": "\U0001f525", "photos": "Photo of unit + data plate", "notes": "Make/model, age, visible leaks or corrosion."},
    {"order": 8, "name": "1F Bathroom", "icon": "\U0001f6bf", "photos": "Wide shot + close-ups", "notes": "Toilet, vanity, tub/shower, flooring, ceiling."},
    {"order": 9, "name": "Stairs", "icon": "\U0001fa9c", "photos": "Photo looking up stairway", "notes": "Tread condition, railing, walls."},
    {"order": 10, "name": "2F Bathroom", "icon": "\U0001f6c1", "photos": "Wide shot + close-ups", "notes": "Same as 1F. CHECK CEILING FOR WATER STAINS."},
    {"order": 11, "name": "2F Bedroom 1", "icon": "\U0001f6cf\ufe0f", "photos": "Wide shot from doorway", "notes": "Walls, windows, closet, ceiling. CHECK FOR WATER STAINS."},
    {"order": 12, "name": "2F Bedroom 2", "icon": "\U0001f6cf\ufe0f", "photos": "Wide shot from doorway", "notes": "Same as Bedroom 1. CHECK CEILING FOR WATER STAINS."},
    {"order": 13, "name": "Air Return / Filter", "icon": "\U0001f300", "photos": "Photo of return vent + filter", "notes": "Note filter size. Note if dirty/missing."},
]


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
            raise HTTPException(500, "A database error occurred. Please try again.")
        return r.json() if r.text else []


async def sb_post_safe(table: str, data: dict | list) -> list:
    """Like sb_post but returns [] instead of raising on error.
    Used for tables that may not exist yet (e.g. fc_capture_photos before migration).
    """
    try:
        return await sb_post(table, data)
    except HTTPException:
        logger.warning(f"sb_post_safe: {table} write failed (table may not exist yet)")
        return []


async def sb_get_safe(table: str, filters: str = "") -> list:
    """Like sb_get but explicitly catches all errors. For tables that may not exist."""
    try:
        return await sb_get(table, filters)
    except Exception:
        logger.warning(f"sb_get_safe: {table} read failed (table may not exist yet)")
        return []


async def sb_patch(table: str, filters: str, data: dict) -> list:
    async with httpx.AsyncClient() as client:
        r = await client.patch(
            f"{SUPABASE_URL}/rest/v1/{table}{filters}",
            headers=HEADERS,
            json=data,
        )
        if r.status_code >= 400:
            logger.error(f"sb_patch {table}: {r.status_code} {r.text}")
            raise HTTPException(500, "A database error occurred. Please try again.")
        return r.json() if r.text else []


async def sb_delete(table: str, filters: str) -> list:
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.delete(
            f"{SUPABASE_URL}/rest/v1/{table}{filters}",
            headers={
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
            },
        )
        if r.status_code >= 400:
            logger.error(f"sb_delete {table}: {r.status_code} {r.text}")
            return []
        logger.info(f"sb_delete {table}: deleted OK ({r.status_code})")
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
            raise HTTPException(500, "A database error occurred. Please try again.")

    return f"{SUPABASE_URL}/storage/v1/object/public/{bucket}/{path}"


# ── PIN hashing ─────────────────────────────────────────────────────────────

def hash_pin(pin: str, email: str) -> str:
    """Hash a PIN with bcrypt using email as additional context."""
    salted = f"{email.lower().strip()}:{pin}"
    return bcrypt.hashpw(salted.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_pin(pin: str, email: str, stored_hash: str) -> bool:
    """Verify a PIN against a stored bcrypt hash."""
    salted = f"{email.lower().strip()}:{pin}"
    try:
        return bcrypt.checkpw(salted.encode("utf-8"), stored_hash.encode("utf-8"))
    except Exception:
        # Fallback: check if it's an old SHA-256 hash for migration
        old_hash = hashlib.sha256(salted.encode("utf-8")).hexdigest()
        if old_hash == stored_hash:
            return True
        return False


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

    sessions = await sb_get("fc_sessions", f"?token=eq.{_safe_id(token)}")
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
            raise HTTPException(401, "Session expired — invalid expiry date")

    # Load building memberships for tenant scoping
    memberships = await sb_get("fc_building_members", f"?user_id=eq.{_safe_id(session['user_id'])}&select=building_id")
    session["building_ids"] = [m["building_id"] for m in memberships]

    return session


def _check_building_access(session: dict, building_id: str):
    """Verify the user has access to the specified building."""
    if building_id not in session.get("building_ids", []):
        raise HTTPException(403, "No access to this building")


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
async def health():
    db_ok = False
    try:
        result = await sb_get("fc_users", "?select=id&limit=1")
        db_ok = True
    except Exception:
        pass
    status_code = 200 if db_ok else 503
    return JSONResponse(
        status_code=status_code,
        content={
            "status": "ok" if db_ok else "degraded",
            "app": "BuildingOS Field Capture",
            "version": "1.4.0",
            "supabase": db_ok,
            "anthropic": bool(ANTHROPIC_KEY),
        }
    )


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
async def run_migration(session: dict = Depends(verify_session)):
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
# v1.4 MIGRATION ENDPOINT
# ═══════════════════════════════════════════════════════════════════════════════

@app.post("/api/admin/migrate-v1.4")
async def run_migration_v14(session: dict = Depends(verify_session)):
    """Admin endpoint: outputs v1.4 migration SQL for fc_inspections, fc_inspection_photos, and fc_capture_photos."""
    return {
        "message": "Run this SQL in your Supabase SQL Editor (supabase.com/dashboard → SQL Editor)",
        "version": "1.4.0",
        "sql": """
-- v1.4 Migration — Full Unit Inspection Tables
-- Run in Supabase SQL Editor

-- Step 1: Create fc_inspections table (27-column Inspection Log)
CREATE TABLE IF NOT EXISTS fc_inspections (
    id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
    building_id text NOT NULL,
    unit_id uuid NOT NULL,
    inspector_id uuid,
    inspector_name text DEFAULT '',
    walk_session_id uuid,
    inspection_date timestamptz DEFAULT now(),

    -- Safety & Habitability
    smoke_co_detectors text DEFAULT '',
    safety_check text DEFAULT '',
    safety_notes text DEFAULT '',

    -- Mechanicals - Systems
    hvac_info text DEFAULT '',
    hvac_condition text DEFAULT '',
    water_heater_info text DEFAULT '',
    water_heater_condition text DEFAULT '',
    elec_panel_info text DEFAULT '',
    windows_condition text DEFAULT '',
    plumbing_leaks text DEFAULT '',
    appliances_condition text DEFAULT '',

    -- Finishes - Reno Scope
    kitchen_bath_flooring text DEFAULT '',
    kitchen_bath_condition text DEFAULT '',
    doors_drywall text DEFAULT '',

    -- Tenant Assessment
    cleanliness_rating int CHECK (cleanliness_rating IS NULL OR (cleanliness_rating >= 1 AND cleanliness_rating <= 5)),
    tenant_issues text DEFAULT '',
    cooperation text DEFAULT '',
    renewal_rec text DEFAULT '',

    -- Outputs & Actions
    immediate_wos text DEFAULT '',
    photo_count int DEFAULT 0,
    notes text DEFAULT '',

    -- Status
    status text DEFAULT 'draft' CHECK (status IN ('draft', 'complete')),

    created_at timestamptz DEFAULT now(),
    updated_at timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_fc_inspections_building ON fc_inspections(building_id);
CREATE INDEX IF NOT EXISTS idx_fc_inspections_unit ON fc_inspections(unit_id);
CREATE INDEX IF NOT EXISTS idx_fc_inspections_date ON fc_inspections(inspection_date);

-- Step 2: Create fc_inspection_photos table
CREATE TABLE IF NOT EXISTS fc_inspection_photos (
    id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
    inspection_id uuid NOT NULL REFERENCES fc_inspections(id),
    photo_type text DEFAULT 'general',
    stop_index int,
    stop_name text DEFAULT '',
    photo_url text NOT NULL,
    photo_storage_path text DEFAULT '',
    photo_filename text DEFAULT '',
    sort_order int DEFAULT 0,
    notes text DEFAULT '',
    created_at timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_fc_inspection_photos_inspection ON fc_inspection_photos(inspection_id);

-- Step 3: Create fc_capture_photos table (was missing — causing photo errors)
CREATE TABLE IF NOT EXISTS fc_capture_photos (
    id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
    capture_id uuid NOT NULL,
    photo_type text DEFAULT 'tag',
    photo_url text NOT NULL,
    photo_storage_path text DEFAULT '',
    photo_filename text DEFAULT '',
    sort_order int DEFAULT 0,
    created_at timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_fc_capture_photos_capture ON fc_capture_photos(capture_id);
"""
    }


# ═══════════════════════════════════════════════════════════════════════════════
# AUTH ENDPOINTS (v1.3.2 — invite-code login + session)
# ═══════════════════════════════════════════════════════════════════════════════

@app.post("/api/auth/login")
@limiter.limit("5/minute")
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
    invites = await sb_get("fc_invite_codes", f"?code=eq.{_safe_id(invite_code)}&is_active=eq.true")
    if not invites:
        raise HTTPException(404, "Invalid or expired invite code")
    invite = invites[0]
    if invite.get("uses", 0) >= invite.get("max_uses", 10):
        raise HTTPException(410, "Invite code has reached maximum uses")

    # Find or create user
    users = await sb_get("fc_users", f"?email=eq.{_safe_id(email)}")
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
    # Verify caller — either a valid session or the setup API key
    setup_key = request.headers.get("X-Setup-Key", "")
    if setup_key != os.getenv("SETUP_API_KEY", "bos-setup-2026"):
        raise HTTPException(403, "Unauthorized setup request")

    body = await request.json()
    building_name = body.get("building_name", "")
    building_address = body.get("building_address", "")
    units = body.get("units", [])
    equipment_types = body.get("equipment_types", [])
    created_by = body.get("created_by", "")

    if units:
        # Smart upsert: only insert units that don't already exist (by unit_name)
        # This avoids FK constraint failures when deleting units that have captures
        existing_units = await sb_get("fc_units", f"?building_id=eq.{_safe_id(building_id)}&select=id,unit_name")
        existing_names = {u.get("unit_name", "") for u in existing_units}
        new_units = [u for u in units if u.get("name", u.get("unit_name", "")) not in existing_names]
        if new_units:
            unit_rows = [{
                "building_id": building_id,
                "unit_name": u.get("name", u.get("unit_name", "")),
                "floor": u.get("floor", ""),
                "unit_type": u.get("unit_type", "residential"),
                "sqft": u.get("sqft"),
                "bedrooms": u.get("bedrooms"),
                "bathrooms": u.get("bathrooms"),
                "notes": u.get("notes", ""),
                "sort_order": len(existing_units) + i,
            } for i, u in enumerate(new_units)]
            await sb_post("fc_units", unit_rows)
            logger.info(f"Setup: inserted {len(new_units)} new units, skipped {len(existing_names)} existing")
        else:
            logger.info(f"Setup: all {len(units)} units already exist, skipping insert")

    if equipment_types:
        # Smart upsert: only insert equipment types that don't already exist (by name)
        existing_et = await sb_get("fc_equipment_types", f"?building_id=eq.{_safe_id(building_id)}&select=id,name")
        existing_et_names = {et.get("name", "") for et in existing_et}
        new_ets = [et for et in equipment_types if et.get("name", "") not in existing_et_names]
        if new_ets:
            type_rows = [{
                "building_id": building_id,
                "name": et.get("name", ""),
                "icon": et.get("icon", "\U0001f527"),
                "description": et.get("description", ""),
                "sort_order": len(existing_et) + i,
            } for i, et in enumerate(new_ets)]
            await sb_post("fc_equipment_types", type_rows)
            logger.info(f"Setup: inserted {len(new_ets)} new equipment types, skipped {len(existing_et_names)} existing")
        else:
            logger.info(f"Setup: all {len(equipment_types)} equipment types already exist, skipping insert")

    # Use invite code from the main platform if provided, otherwise generate one
    code = body.get("invite_code", "").strip().upper()

    # Check if this code already exists and is active — if so, keep it (idempotent sync)
    if code:
        existing_code = await sb_get("fc_invite_codes", f"?code=eq.{_safe_id(code)}&is_active=eq.true")
        if existing_code:
            # Code already exists, no need to recreate — just sync units/equipment
            return {
                "building_id": building_id,
                "units_created": len(units),
                "equipment_types_created": len(equipment_types),
                "invite_code": code,
                "invite_url": f"/join/{code}",
            }

    if not code:
        code = _generate_invite_code()

    # Deactivate any existing invite codes for this building
    existing = await sb_get("fc_invite_codes", f"?building_id=eq.{_safe_id(building_id)}&is_active=eq.true")
    for old in existing:
        await sb_patch("fc_invite_codes", f"?id=eq.{old['id']}", {"is_active": False})

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
async def create_invite_code(request: Request, session: dict = Depends(verify_session)):
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


@app.post("/api/buildings/{building_id}/cleanup-units")
async def cleanup_building_units(building_id: str, request: Request, session: dict = Depends(verify_session)):
    """Delete units by ID list, or if keep_names provided, delete everything except those names."""
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass

    keep_names = body.get("keep_names", [])
    all_units = await sb_get("fc_units", f"?building_id=eq.{_safe_id(building_id)}&select=id,unit_name")
    if not all_units:
        return {"deleted": 0, "remaining": 0}

    if keep_names:
        keep_set = set(keep_names)
        # Keep one unit per name in keep_set, delete everything else
        seen = set()
        to_keep_ids = []
        to_delete = []
        for u in all_units:
            name = u.get("unit_name", "")
            if name in keep_set and name not in seen:
                seen.add(name)
                to_keep_ids.append(u["id"])
            else:
                to_delete.append(u["id"])
    else:
        # Just deduplicate
        seen = {}
        to_delete = []
        for u in all_units:
            name = u.get("unit_name", "")
            if name in seen:
                to_delete.append(u["id"])
            else:
                seen[name] = u["id"]

    # Cascade delete: all FK-referencing tables → then units
    # Tables that reference fc_units.id via unit_id:
    #   fc_capture_photos → fc_captures → fc_units
    #   fc_inspection_photos → fc_inspections → fc_units
    #   fc_unit_visits → fc_units
    hdrs = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    for i in range(0, len(to_delete), 30):
        batch = to_delete[i:i+30]
        id_list = ','.join(str(uid) for uid in batch)
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # 1. Get capture IDs → delete their photos → delete captures
                r = await client.get(f"{SUPABASE_URL}/rest/v1/fc_captures?unit_id=in.({id_list})&select=id", headers=hdrs)
                capture_ids = [c["id"] for c in (r.json() if r.status_code < 300 and r.text else [])]
                if capture_ids:
                    cap_id_list = ','.join(str(cid) for cid in capture_ids)
                    await client.delete(f"{SUPABASE_URL}/rest/v1/fc_capture_photos?capture_id=in.({cap_id_list})", headers=hdrs)
                await client.delete(f"{SUPABASE_URL}/rest/v1/fc_captures?unit_id=in.({id_list})", headers=hdrs)

                # 2. Get inspection IDs → delete their photos → delete inspections
                r2 = await client.get(f"{SUPABASE_URL}/rest/v1/fc_inspections?unit_id=in.({id_list})&select=id", headers=hdrs)
                insp_ids = [ins["id"] for ins in (r2.json() if r2.status_code < 300 and r2.text else [])]
                if insp_ids:
                    insp_id_list = ','.join(str(iid) for iid in insp_ids)
                    await client.delete(f"{SUPABASE_URL}/rest/v1/fc_inspection_photos?inspection_id=in.({insp_id_list})", headers=hdrs)
                await client.delete(f"{SUPABASE_URL}/rest/v1/fc_inspections?unit_id=in.({id_list})", headers=hdrs)

                # 3. Delete unit visits
                await client.delete(f"{SUPABASE_URL}/rest/v1/fc_unit_visits?unit_id=in.({id_list})", headers=hdrs)
        except Exception as e:
            logger.error(f"Cascade delete error: {e}")

    deleted = 0
    delete_errors = []
    for i in range(0, len(to_delete), 30):
        batch = to_delete[i:i+30]
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Try deleting one at a time if batch fails
                id_list = ','.join(str(uid) for uid in batch)
                r = await client.delete(
                    f"{SUPABASE_URL}/rest/v1/fc_units?id=in.({id_list})",
                    headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
                )
                if r.status_code < 300:
                    deleted += len(batch)
                else:
                    delete_errors.append(f"batch({len(batch)}): {r.status_code} {r.text[:300]}")
                    # Fall back to individual deletes
                    for uid in batch:
                        r2 = await client.delete(
                            f"{SUPABASE_URL}/rest/v1/fc_units?id=eq.{uid}",
                            headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
                        )
                        if r2.status_code < 300:
                            deleted += 1
                        else:
                            delete_errors.append(f"single({uid[:8]}): {r2.status_code} {r2.text[:200]}")
        except Exception as e:
            delete_errors.append(f"exception: {str(e)[:200]}")

    remaining = len(all_units) - deleted

    # Also deduplicate equipment types for this building
    all_et = await sb_get("fc_equipment_types", f"?building_id=eq.{_safe_id(building_id)}&select=id,name")
    et_seen = {}
    et_to_delete = []
    for et in all_et:
        name = et.get("name", "")
        if name in et_seen:
            et_to_delete.append(et["id"])
        else:
            et_seen[name] = et["id"]
    et_deleted = 0
    for i in range(0, len(et_to_delete), 30):
        batch = et_to_delete[i:i+30]
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.delete(
                    f"{SUPABASE_URL}/rest/v1/fc_equipment_types?id=in.({','.join(str(uid) for uid in batch)})",
                    headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
                )
                if r.status_code < 300:
                    et_deleted += len(batch)
        except Exception:
            pass

    return {"deleted": deleted, "remaining": remaining, "building_id": building_id,
            "equipment_types_deduped": et_deleted, "equipment_types_remaining": len(et_seen),
            "to_delete_count": len(to_delete), "errors": delete_errors[:10]}


@app.delete("/api/buildings/{building_id}/invite-codes")
async def delete_building_invite_codes(building_id: str, session: dict = Depends(verify_session)):
    """Deactivate all invite codes for a building."""
    existing = await sb_get("fc_invite_codes", f"?building_id=eq.{_safe_id(building_id)}&is_active=eq.true")
    deactivated = 0
    for inv in existing:
        await sb_patch("fc_invite_codes", f"?id=eq.{inv['id']}", {"is_active": False})
        deactivated += 1
    return {"deactivated": deactivated, "building_id": building_id}


@app.get("/api/invite-codes/{code}")
async def validate_invite_code(code: str):
    rows = await sb_get("fc_invite_codes", f"?code=eq.{_safe_id(code)}&is_active=eq.true")
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
    return ''.join(secrets.choice(chars) for _ in range(6))


# ── Building config endpoints ───────────────────────────────────────────────

@app.post("/api/buildings/{building_id}/units")
async def add_units(building_id: str, request: Request, session: dict = Depends(verify_session)):
    _check_building_access(session, building_id)
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
    _check_building_access(session, building_id)
    units = await sb_get("fc_units", f"?building_id=eq.{_safe_id(building_id)}&order=sort_order,unit_name")
    equip_types = await sb_get("fc_equipment_types", f"?building_id=eq.{_safe_id(building_id)}&order=sort_order")
    captures = await sb_get("fc_captures", f"?building_id=eq.{_safe_id(building_id)}&select=id,unit_id,equipment_type_id")

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
    _check_building_access(session, building_id)
    body = await request.json()
    types = body if isinstance(body, list) else body.get("equipment_types", [])
    rows = [{
        "building_id": building_id,
        "name": t.get("name", ""),
        "icon": t.get("icon", "\U0001f527"),
        "description": t.get("description", ""),
        "sub_types": json.dumps(t.get("sub_types", [])),  # v1.3
        "sort_order": i,
    } for i, t in enumerate(types)]
    result = await sb_post("fc_equipment_types", rows)
    return {"types_created": len(result), "equipment_types": result}


@app.get("/api/buildings/{building_id}/equipment-types")
async def list_equipment_types(building_id: str, session: dict = Depends(verify_session)):
    _check_building_access(session, building_id)
    types = await sb_get("fc_equipment_types", f"?building_id=eq.{_safe_id(building_id)}&order=sort_order")
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
    _check_building_access(session, building_id)
    body = await request.json()
    name = body.get("name", "").strip()
    if not name:
        raise HTTPException(400, "Equipment type name required")

    # Check for duplicates
    existing = await sb_get("fc_equipment_types", f"?building_id=eq.{_safe_id(building_id)}&name=eq.{_safe_id(name)}")
    if existing:
        return {"equipment_type": existing[0], "already_exists": True}

    # Get current max sort_order
    all_types = await sb_get("fc_equipment_types", f"?building_id=eq.{_safe_id(building_id)}&order=sort_order.desc&limit=1")
    next_order = (all_types[0]["sort_order"] + 1) if all_types else 0

    result = await sb_post("fc_equipment_types", {
        "building_id": building_id,
        "name": name,
        "icon": body.get("icon", "\U0001f527"),
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

    invites = await sb_get("fc_invite_codes", f"?code=eq.{_safe_id(code)}&is_active=eq.true")
    if not invites:
        raise HTTPException(404, "Invalid or expired invite code")
    invite = invites[0]

    if invite.get("uses", 0) >= invite.get("max_uses", 10):
        raise HTTPException(410, "Invite code has reached maximum uses")

    existing = await sb_get(
        "fc_building_members",
        f"?user_id=eq.{_safe_id(user_id)}&building_id=eq.{invite['building_id']}"
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

    memberships = await sb_get("fc_building_members", f"?user_id=eq.{_safe_id(user_id)}")
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
    _check_building_access(session, building_id)
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

    result = await sb_patch("fc_walk_sessions", f"?id=eq.{_safe_id(walk_id)}", update)

    if body.get("status") == "completed":
        await log_analytics("walk_end", user_id=session.get("user_id"), metadata={"walk_id": walk_id}, request=request)

    return {"walk": result[0] if result else None}


# ── Unit Visits ─────────────────────────────────────────────────────────────

@app.post("/api/buildings/{building_id}/units/{unit_id}/visit")
async def visit_unit(building_id: str, unit_id: str, request: Request, session: dict = Depends(verify_session)):
    _check_building_access(session, building_id)
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
    result = await sb_patch("fc_unit_visits", f"?id=eq.{_safe_id(visit_id)}", update)
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
    _check_building_access(session, building_id)
    photo_bytes = await photo.read()
    if not photo_bytes:
        raise HTTPException(400, "Empty photo")

    eq_types = await sb_get("fc_equipment_types", f"?id=eq.{_safe_id(equipment_type_id)}")
    eq_type_name = eq_types[0]["name"] if eq_types else "unknown"
    eq_type_slug = eq_type_name.lower().replace(" ", "_").replace("/", "_")

    units = await sb_get("fc_units", f"?id=eq.{_safe_id(unit_id)}")
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
    # Note: fc_captures table does NOT have 'brand' or 'sub_type' columns
    # Map brand → make (same concept), sub_type → additional_specs
    ai_make = ai_data.get("brand") or ai_data.get("make") or ""
    ai_specs = ai_data.get("additional_specs", {})
    if sub_type:
        ai_specs["sub_type"] = sub_type
    capture_record = {
        "building_id": building_id,
        "unit_id": unit_id,
        "unit_visit_id": unit_visit_id,
        "equipment_type_id": equipment_type_id,
        "walk_session_id": walk_session_id,
        "captured_by": user_id or session.get("user_id"),
        "captured_by_name": user_name,
        "photo_url": photo_url,
        "photo_storage_path": storage_path,
        "photo_filename": filename,
        "make": ai_make,
        "model_name": ai_data.get("model_name"),
        "model_number": ai_data.get("model_number"),
        "serial_number": ai_data.get("serial_number"),
        "manufacture_year": ai_data.get("manufacture_year"),
        "description": ai_data.get("description"),
        "additional_specs": ai_specs,
        "ai_confidence": ai_confidence,
        "ai_raw_response": ai_raw,
        "condition_rating": condition_rating,
        "condition_notes": condition_notes,
        "tag_readable": tag_readable,
    }

    result = await sb_post("fc_captures", capture_record)
    capture = result[0] if result else capture_record

    # 4. v1.3: Store tag photo in fc_capture_photos (wrapped in try/except — table may not exist)
    if capture.get("id"):
        await sb_post_safe("fc_capture_photos", {
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
    captures = await sb_get("fc_captures", f"?id=eq.{_safe_id(capture_id)}")
    if not captures:
        raise HTTPException(404, "Capture not found")
    cap = captures[0]

    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename = f"{cap['building_id']}_{photo_type}_{timestamp}.jpg"
    storage_path = f"field-capture/{cap['building_id']}/{photo_type}/{filename}"

    photo_url = await sb_upload("equipment-photos", storage_path, photo_bytes)

    # Get next sort_order (wrapped in try/except — table may not exist)
    existing_photos = await sb_get_safe("fc_capture_photos", f"?capture_id=eq.{_safe_id(capture_id)}&order=sort_order.desc&limit=1")
    next_order = (existing_photos[0]["sort_order"] + 1) if existing_photos else 1

    result = await sb_post_safe("fc_capture_photos", {
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
    """Get all photos for a capture (wrapped in try/except — table may not exist)."""
    photos = await sb_get_safe("fc_capture_photos", f"?capture_id=eq.{_safe_id(capture_id)}&order=sort_order")
    return {"photos": photos}


@app.patch("/api/captures/{capture_id}")
async def update_capture(capture_id: str, request: Request, session: dict = Depends(verify_session)):
    """Update/correct a capture — v1.3: supports edit-after-save."""
    body = await request.json()
    # Note: fc_captures has no 'brand' or 'sub_type' columns — map them
    if "brand" in body and "make" not in body:
        body["make"] = body.pop("brand")
    elif "brand" in body:
        body.pop("brand")
    if "sub_type" in body:
        specs = body.get("additional_specs", {})
        specs["sub_type"] = body.pop("sub_type")
        body["additional_specs"] = specs
    allowed = [
        "make", "model_name", "model_number", "serial_number",
        "manufacture_year", "description", "condition_rating", "condition_notes",
        "verification_notes", "manually_verified", "tag_readable",
        "additional_specs",
    ]
    update = {k: v for k, v in body.items() if k in allowed}
    if body.get("manually_verified"):
        update["verified_at"] = datetime.utcnow().isoformat()
        update["verified_by"] = body.get("verified_by")
    update["updated_at"] = datetime.utcnow().isoformat()

    result = await sb_patch("fc_captures", f"?id=eq.{_safe_id(capture_id)}", update)
    return {"capture": result[0] if result else None}


@app.post("/api/buildings/{building_id}/units/{unit_id}/manual-capture")
async def manual_capture(building_id: str, unit_id: str, request: Request, session: dict = Depends(verify_session)):
    """Manual capture when tag is unreadable."""
    _check_building_access(session, building_id)
    body = await request.json()
    # Note: fc_captures table does NOT have 'brand' or 'sub_type' columns
    manual_make = body.get("brand") or body.get("make") or ""
    manual_specs = {}
    if body.get("sub_type"):
        manual_specs["sub_type"] = body.get("sub_type")
    capture_record = {
        "building_id": building_id,
        "unit_id": unit_id,
        "equipment_type_id": body.get("equipment_type_id"),
        "walk_session_id": body.get("walk_session_id"),
        "unit_visit_id": body.get("unit_visit_id"),
        "captured_by": body.get("user_id") or session.get("user_id"),
        "captured_by_name": body.get("user_name", ""),
        "make": manual_make,
        "model_name": body.get("model_name"),
        "model_number": body.get("model_number"),
        "serial_number": body.get("serial_number"),
        "manufacture_year": body.get("manufacture_year"),
        "description": body.get("description"),
        "additional_specs": manual_specs if manual_specs else {},
        "condition_rating": body.get("condition_rating"),
        "condition_notes": body.get("condition_notes"),
        "tag_readable": False,
        "manually_verified": True,
        "verified_at": datetime.utcnow().isoformat(),
    }
    result = await sb_post("fc_captures", capture_record)
    return {"capture": result[0] if result else capture_record}


# ═══════════════════════════════════════════════════════════════════════════════
# v1.4: FULL UNIT INSPECTION ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/api/inspection-stops")
async def get_inspection_stops():
    """Return the 13-stop room walkthrough for the frontend."""
    return {"stops": INSPECTION_STOPS, "count": len(INSPECTION_STOPS)}


@app.post("/api/buildings/{building_id}/units/{unit_id}/inspection")
async def create_inspection(
    building_id: str,
    unit_id: str,
    request: Request,
    session: dict = Depends(verify_session),
):
    """Save a full unit inspection record (27-field Inspection Log)."""
    _check_building_access(session, building_id)
    body = await request.json()

    inspection_record = {
        "building_id": building_id,
        "unit_id": unit_id,
        "inspector_id": body.get("inspector_id") or session.get("user_id"),
        "inspector_name": body.get("inspector_name", ""),
        "walk_session_id": body.get("walk_session_id"),
        "inspection_date": datetime.utcnow().isoformat(),

        # Safety & Habitability
        "smoke_co_detectors": body.get("smoke_co_detectors", ""),
        "safety_check": body.get("safety_check", ""),
        "safety_notes": body.get("safety_notes", ""),

        # Mechanicals - Systems
        "hvac_info": body.get("hvac_info", ""),
        "hvac_condition": body.get("hvac_condition", ""),
        "water_heater_info": body.get("water_heater_info", ""),
        "water_heater_condition": body.get("water_heater_condition", ""),
        "elec_panel_info": body.get("elec_panel_info", ""),
        "windows_condition": body.get("windows_condition", ""),
        "plumbing_leaks": body.get("plumbing_leaks", ""),
        "appliances_condition": body.get("appliances_condition", ""),

        # Finishes - Reno Scope
        "kitchen_bath_flooring": body.get("kitchen_bath_flooring", ""),
        "kitchen_bath_condition": body.get("kitchen_bath_condition", ""),
        "doors_drywall": body.get("doors_drywall", ""),

        # Tenant Assessment
        "cleanliness_rating": body.get("cleanliness_rating"),
        "tenant_issues": body.get("tenant_issues", ""),
        "cooperation": body.get("cooperation", ""),
        "renewal_rec": body.get("renewal_rec", ""),

        # Outputs & Actions
        "immediate_wos": body.get("immediate_wos", ""),
        "photo_count": body.get("photo_count", 0),
        "notes": body.get("notes", ""),

        # Status
        "status": body.get("status", "draft"),
    }

    # Validate cleanliness_rating if provided
    cr = inspection_record.get("cleanliness_rating")
    if cr is not None:
        try:
            cr = int(cr)
            if cr < 1 or cr > 5:
                raise HTTPException(400, "cleanliness_rating must be between 1 and 5")
            inspection_record["cleanliness_rating"] = cr
        except (ValueError, TypeError):
            raise HTTPException(400, "cleanliness_rating must be an integer 1-5")

    # Validate status
    if inspection_record["status"] not in ("draft", "complete"):
        raise HTTPException(400, "status must be 'draft' or 'complete'")

    result = await sb_post("fc_inspections", inspection_record)
    inspection = result[0] if result else inspection_record

    # Log analytics
    await log_analytics(
        "inspection_create",
        user_id=session.get("user_id"),
        building_id=building_id,
        metadata={"inspection_id": inspection.get("id"), "unit_id": unit_id, "status": inspection_record["status"]},
        request=request,
    )

    return {"inspection": inspection}


@app.get("/api/buildings/{building_id}/units/{unit_id}/inspection")
async def get_latest_inspection(
    building_id: str,
    unit_id: str,
    session: dict = Depends(verify_session),
):
    """Retrieve the latest inspection for a unit."""
    _check_building_access(session, building_id)
    inspections = await sb_get(
        "fc_inspections",
        f"?building_id=eq.{_safe_id(building_id)}&unit_id=eq.{_safe_id(unit_id)}&order=inspection_date.desc&limit=1"
    )
    if not inspections:
        raise HTTPException(404, "No inspection found for this unit")
    return {"inspection": inspections[0]}


@app.get("/api/buildings/{building_id}/inspections")
async def list_building_inspections(
    building_id: str,
    session: dict = Depends(verify_session),
):
    """List all inspections for a building."""
    _check_building_access(session, building_id)
    inspections = await sb_get(
        "fc_inspections",
        f"?building_id=eq.{_safe_id(building_id)}&order=inspection_date.desc"
    )

    # Enrich with unit names
    units = await sb_get("fc_units", f"?building_id=eq.{_safe_id(building_id)}")
    unit_map = {u["id"]: u for u in units}

    enriched = []
    for insp in inspections:
        unit = unit_map.get(insp.get("unit_id"), {})
        enriched.append({
            **insp,
            "unit_name": unit.get("unit_name", ""),
        })

    return {"inspections": enriched, "count": len(enriched)}


# ── Inspection Photos ──────────────────────────────────────────────────────

@app.post("/api/inspections/{inspection_id}/photos")
async def add_inspection_photo(
    inspection_id: str,
    photo: UploadFile = File(...),
    photo_type: str = Form("general"),
    stop_index: int = Form(None),
    stop_name: str = Form(""),
    notes: str = Form(""),
    session: dict = Depends(verify_session),
):
    """Upload a photo linked to an inspection."""
    photo_bytes = await photo.read()
    if not photo_bytes:
        raise HTTPException(400, "Empty photo")

    # Get inspection to build storage path
    inspections = await sb_get("fc_inspections", f"?id=eq.{_safe_id(inspection_id)}")
    if not inspections:
        raise HTTPException(404, "Inspection not found")
    insp = inspections[0]

    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    stop_slug = (stop_name or photo_type).lower().replace(" ", "_").replace("/", "_")
    filename = f"{insp['building_id']}_insp_{stop_slug}_{timestamp}.jpg"
    storage_path = f"field-capture/{insp['building_id']}/inspections/{inspection_id}/{filename}"

    photo_url = await sb_upload("equipment-photos", storage_path, photo_bytes)

    # Get next sort_order
    existing_photos = await sb_get_safe(
        "fc_inspection_photos",
        f"?inspection_id=eq.{_safe_id(inspection_id)}&order=sort_order.desc&limit=1"
    )
    next_order = (existing_photos[0]["sort_order"] + 1) if existing_photos else 0

    result = await sb_post("fc_inspection_photos", {
        "inspection_id": inspection_id,
        "photo_type": photo_type,
        "stop_index": stop_index,
        "stop_name": stop_name,
        "photo_url": photo_url,
        "photo_storage_path": storage_path,
        "photo_filename": filename,
        "sort_order": next_order,
        "notes": notes,
    })

    # Update photo count on the inspection
    try:
        all_photos = await sb_get_safe(
            "fc_inspection_photos",
            f"?inspection_id=eq.{_safe_id(inspection_id)}&select=id"
        )
        await sb_patch("fc_inspections", f"?id=eq.{_safe_id(inspection_id)}", {
            "photo_count": len(all_photos),
            "updated_at": datetime.utcnow().isoformat(),
        })
    except Exception:
        pass

    await log_analytics(
        "inspection_photo_upload",
        user_id=session.get("user_id"),
        building_id=insp.get("building_id"),
        metadata={"inspection_id": inspection_id, "stop_name": stop_name},
        request=None,
    )

    return {
        "photo": result[0] if result else None,
        "photo_url": photo_url,
    }


# ── Maintenance Items ──────────────────────────────────────────────────────

@app.get("/api/buildings/{building_id}/maintenance-items")
async def list_maintenance_items(
    building_id: str,
    session: dict = Depends(verify_session),
):
    """
    Return all inspection findings flagged as needing work orders.
    Scans all inspections for a building and returns items where
    immediate_wos is non-empty, plus safety issues from safety_notes.
    """
    _check_building_access(session, building_id)
    inspections = await sb_get(
        "fc_inspections",
        f"?building_id=eq.{_safe_id(building_id)}&order=inspection_date.desc"
    )
    units = await sb_get("fc_units", f"?building_id=eq.{_safe_id(building_id)}")
    unit_map = {u["id"]: u for u in units}

    items = []
    item_counter = 0

    for insp in inspections:
        unit = unit_map.get(insp.get("unit_id"), {})
        unit_name = unit.get("unit_name", "Unknown")
        inspector = insp.get("inspector_name", "")
        insp_date = (insp.get("inspection_date") or "")[:10]
        insp_id = insp.get("id", "")

        # Parse immediate_wos — each line or semicolon-separated item is a work order
        immediate_wos = (insp.get("immediate_wos") or "").strip()
        if immediate_wos:
            wo_lines = [line.strip() for line in immediate_wos.replace(";", "\n").split("\n") if line.strip()]
            for wo in wo_lines:
                item_counter += 1
                # Try to detect system from the WO text
                system = _detect_system(wo)
                items.append({
                    "id": f"{insp_id}-wo-{item_counter}",
                    "unit": unit_name,
                    "system": system,
                    "item": wo,
                    "condition": "Needs Repair",
                    "priority": _estimate_priority(wo),
                    "source": "Field Inspection",
                    "inspector": inspector,
                    "inspection_date": insp_date,
                    "building_id": building_id,
                })

        # Parse safety_notes for issues (non-empty safety notes with fail = safety issue)
        safety_notes = (insp.get("safety_notes") or "").strip()
        safety_check = (insp.get("safety_check") or "").strip().lower()
        smoke_co = (insp.get("smoke_co_detectors") or "").strip().lower()

        if safety_notes:
            item_counter += 1
            items.append({
                "id": f"{insp_id}-safety-{item_counter}",
                "unit": unit_name,
                "system": "Safety",
                "item": safety_notes,
                "condition": "Fail" if safety_check == "fail" else "Needs Attention",
                "priority": 1 if safety_check == "fail" else 2,
                "source": "Field Inspection",
                "inspector": inspector,
                "inspection_date": insp_date,
                "building_id": building_id,
            })

        if smoke_co == "fail":
            item_counter += 1
            items.append({
                "id": f"{insp_id}-smoke-{item_counter}",
                "unit": unit_name,
                "system": "Safety",
                "item": "Smoke/CO detectors failed inspection",
                "condition": "Fail",
                "priority": 1,
                "source": "Field Inspection",
                "inspector": inspector,
                "inspection_date": insp_date,
                "building_id": building_id,
            })

        # Check mechanical conditions for "Poor" ratings
        _check_condition_field(items, insp, unit_name, inspector, insp_date, building_id,
                               "hvac_condition", "HVAC", insp.get("hvac_info", ""))
        _check_condition_field(items, insp, unit_name, inspector, insp_date, building_id,
                               "water_heater_condition", "Plumbing", insp.get("water_heater_info", ""))
        _check_condition_field(items, insp, unit_name, inspector, insp_date, building_id,
                               "plumbing_leaks", "Plumbing", "")
        _check_condition_field(items, insp, unit_name, inspector, insp_date, building_id,
                               "appliances_condition", "Appliances", "")

    # Sort by priority (1 = highest)
    items.sort(key=lambda x: x.get("priority", 5))

    return {"maintenance_items": items, "count": len(items)}


def _detect_system(text: str) -> str:
    """Detect the building system from work order text."""
    text_lower = text.lower()
    if any(w in text_lower for w in ["hvac", "ac ", "a/c", "condenser", "air handler", "heating", "cooling", "thermostat"]):
        return "HVAC"
    if any(w in text_lower for w in ["plumb", "faucet", "toilet", "pipe", "leak", "drain", "water heater"]):
        return "Plumbing"
    if any(w in text_lower for w in ["electric", "panel", "breaker", "outlet", "switch", "wiring"]):
        return "Electrical"
    if any(w in text_lower for w in ["smoke", "co ", "carbon", "detector", "fire", "safety"]):
        return "Safety"
    if any(w in text_lower for w in ["door", "window", "lock", "drywall", "wall", "ceiling"]):
        return "Structural"
    if any(w in text_lower for w in ["kitchen", "bath", "floor", "tile", "counter", "cabinet"]):
        return "Finishes"
    if any(w in text_lower for w in ["appliance", "stove", "oven", "dishwasher", "fridge", "refrigerator", "disposal"]):
        return "Appliances"
    return "General"


def _estimate_priority(text: str) -> int:
    """Estimate priority from 1 (urgent) to 5 (cosmetic)."""
    text_lower = text.lower()
    if any(w in text_lower for w in ["safety", "smoke", "co ", "carbon", "fire", "hazard", "emergency", "gas leak"]):
        return 1
    if any(w in text_lower for w in ["leak", "flood", "broken", "no heat", "no ac", "no hot water", "inoperable"]):
        return 2
    if any(w in text_lower for w in ["repair", "replace", "damage", "poor", "failing", "worn"]):
        return 3
    if any(w in text_lower for w in ["minor", "cosmetic", "touch up", "paint", "scuff"]):
        return 4
    return 3  # default medium priority


_item_counter_global = 0

def _check_condition_field(items, insp, unit_name, inspector, insp_date, building_id,
                           field_name, system, extra_info):
    """If a condition field contains 'poor' or 'fail', add a maintenance item."""
    value = (insp.get(field_name) or "").strip()
    if not value:
        return
    value_lower = value.lower()
    if any(w in value_lower for w in ["poor", "fail", "bad", "replace", "leak", "damage"]):
        item_text = value
        if extra_info:
            item_text = f"{extra_info} - {value}"
        items.append({
            "id": f"{insp.get('id', '')}-{field_name}",
            "unit": unit_name,
            "system": system,
            "item": item_text,
            "condition": "Poor",
            "priority": _estimate_priority(value),
            "source": "Field Inspection",
            "inspector": inspector,
            "inspection_date": insp_date,
            "building_id": building_id,
        })


# ── Progress & Dashboard ────────────────────────────────────────────────────

@app.get("/api/buildings/{building_id}/progress")
async def building_progress(building_id: str, session: dict = Depends(verify_session)):
    _check_building_access(session, building_id)
    units = await sb_get("fc_units", f"?building_id=eq.{_safe_id(building_id)}&select=id,unit_name")
    types = await sb_get("fc_equipment_types", f"?building_id=eq.{_safe_id(building_id)}&select=id,name,icon")
    captures = await sb_get("fc_captures", f"?building_id=eq.{_safe_id(building_id)}&select=id,unit_id,equipment_type_id,captured_by_name,created_at,make,model_name,ai_confidence,manually_verified")

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
            "icon": t.get("icon", "\U0001f527"),
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
    _check_building_access(session, building_id)
    filters = f"?building_id=eq.{_safe_id(building_id)}&order=created_at.desc"
    if unit_id:
        filters += f"&unit_id=eq.{_safe_id(unit_id)}"
    captures = await sb_get("fc_captures", filters)
    return {"captures": captures}


# ── Export ──────────────────────────────────────────────────────────────────

@app.get("/api/buildings/{building_id}/export/csv")
async def export_csv(building_id: str, session: dict = Depends(verify_session)):
    """Export all captures as CSV — includes sub_type, timestamp."""
    _check_building_access(session, building_id)
    captures = await sb_get("fc_captures", f"?building_id=eq.{_safe_id(building_id)}&order=created_at")
    units = await sb_get("fc_units", f"?building_id=eq.{_safe_id(building_id)}")
    types = await sb_get("fc_equipment_types", f"?building_id=eq.{_safe_id(building_id)}")

    unit_map = {u["id"]: u for u in units}
    type_map = {t["id"]: t for t in types}

    lines = ["Unit,Equipment Type,Sub-Type,Brand/Make,Model,Model Number,Serial Number,Year,Condition,Description,Captured By,Captured At,Verified"]
    for c in captures:
        unit = unit_map.get(c.get("unit_id"), {})
        etype = type_map.get(c.get("equipment_type_id"), {})
        specs = c.get("additional_specs") or {}
        line = ",".join([
            _csv_escape(unit.get("unit_name", "")),
            _csv_escape(etype.get("name", "")),
            _csv_escape(specs.get("sub_type", "")),
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
