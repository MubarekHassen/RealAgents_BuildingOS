"""
BuildingOS Field Capture Module — v2 API
Handles field technician workflows for property onboarding.
Manages unit data, asset type configuration, photo capture with AI extraction,
and completeness tracking for building asset inventories.
"""

import asyncio
import base64
import io
import json
import logging
import os
import uuid
from datetime import datetime
from typing import Any, List, Optional

import anthropic
import httpx
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends, Request, Header
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field as PydanticField

logger = logging.getLogger("buildingos.field_capture")

# ─────────────────────────────────────────────────────────────────────────────
# Configuration & Environment
# ─────────────────────────────────────────────────────────────────────────────

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
STORAGE_BUCKET = os.getenv("SUPABASE_STORAGE_BUCKET", "documents").strip() or "documents"
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation"
}

TAG_EXTRACTION_PROMPT = """You are analyzing a photo of an equipment tag/label/nameplate on building equipment.
Extract the following fields. Return ONLY a valid JSON object — no markdown, no explanation.
If a field is not visible or readable in the photo, use null.

{
  "make": "Manufacturer/brand name (e.g., Carrier, Rheem, Goodman, GE, Lennox, Trane, Bradford White, Siemens)",
  "model_name": "Model name if shown separately from number, else null",
  "model_number": "Full model/part number string exactly as shown",
  "model_year": "4-digit year of manufacture if visible, else null",
  "serial_number": "Serial number string exactly as shown",
  "description": "Brief description of equipment type and visible specs (e.g., '3-ton split system AC, 14 SEER', '50-gal gas water heater, 40K BTU')",
  "additional_specs": {
    "btu": "BTU rating if visible, else null",
    "tonnage": "Tonnage if visible, else null",
    "voltage": "Voltage if visible, else null",
    "fuel_type": "Gas/Electric/Propane/etc if visible, else null",
    "efficiency_rating": "SEER/EER/AFUE/UEF rating if visible, else null",
    "capacity": "Any capacity rating (gallons, CFM, amps) if visible, else null"
  },
  "tag_condition": "good/fair/poor/unreadable",
  "confidence": "high/medium/low — your overall confidence in the extraction"
}"""

# ─────────────────────────────────────────────────────────────────────────────
# Pydantic Models
# ─────────────────────────────────────────────────────────────────────────────

class UnitImportPayload(BaseModel):
    unit_name: str
    appfolio_unit_id: Optional[str] = None
    appfolio_property_id: Optional[str] = None
    unit_address: Optional[str] = None
    sqft: Optional[int] = None
    bedrooms: Optional[float] = None
    bathrooms: Optional[float] = None


class AssetTypePayload(BaseModel):
    name: str
    appfolio_type_name: Optional[str] = None


class AssetUpdatePayload(BaseModel):
    make: Optional[str] = None
    model_name: Optional[str] = None
    model_number: Optional[str] = None
    model_year: Optional[str] = None
    serial_number: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    placed_in_service: Optional[str] = None
    warranty_expiration: Optional[str] = None
    condition_rating: Optional[str] = None
    estimated_age_years: Optional[int] = None
    estimated_remaining_life: Optional[int] = None
    inspection_notes: Optional[str] = None
    manually_verified: Optional[bool] = None


class UnitUpdatePayload(BaseModel):
    access_status: Optional[str] = None
    access_note: Optional[str] = None
    status: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────────
# Helper Functions
# ─────────────────────────────────────────────────────────────────────────────

async def sb_query(
    table: str,
    method: str = "GET",
    params: Optional[dict] = None,
    json_body: Optional[dict] = None,
    filters: str = "",
) -> Any:
    """Execute a Supabase REST API query."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise HTTPException(status_code=500, detail="Supabase not configured")

    url = f"{SUPABASE_URL}/rest/v1/{table}{filters}"
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            if method == "GET":
                r = await client.get(url, headers=HEADERS, params=params)
            elif method == "POST":
                r = await client.post(url, headers=HEADERS, json=json_body)
            elif method == "PATCH":
                r = await client.patch(url, headers=HEADERS, json=json_body)
            elif method == "DELETE":
                r = await client.delete(url, headers=HEADERS)
            else:
                raise ValueError(f"Unsupported method: {method}")

            if r.status_code >= 400:
                logger.error(f"Supabase {method} {table} failed ({r.status_code}): {r.text[:500]}")
                raise HTTPException(status_code=r.status_code, detail=f"Database error: {r.text[:200]}")

            return r.json() if r.content else None
        except httpx.TimeoutException:
            logger.error(f"Supabase {method} {table} timeout")
            raise HTTPException(status_code=504, detail="Database timeout")
        except Exception as e:
            logger.error(f"Supabase {method} {table} error: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


async def upload_photo_to_storage(file_bytes: bytes, path: str) -> str:
    """Upload a photo to Supabase storage and return the path."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise HTTPException(status_code=500, detail="Storage not configured")

    upload_url = f"{SUPABASE_URL}/storage/v1/object/{STORAGE_BUCKET}/{path}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "image/jpeg",
        "x-upsert": "true",
    }

    async with httpx.AsyncClient(timeout=60) as client:
        try:
            resp = await client.post(upload_url, content=file_bytes, headers=headers)
            if resp.status_code >= 400:
                logger.error(f"Storage upload failed ({resp.status_code}): {resp.text[:500]}")
                raise HTTPException(status_code=resp.status_code, detail="File upload failed")
            return path
        except httpx.TimeoutException:
            raise HTTPException(status_code=504, detail="Upload timeout")
        except Exception as e:
            logger.error(f"Storage upload error: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Upload error: {str(e)}")


async def extract_equipment_data_from_photo(photo_bytes: bytes) -> dict:
    """Send photo to Claude Sonnet 4.6 for equipment tag extraction via vision."""
    if not ANTHROPIC_API_KEY:
        raise HTTPException(status_code=500, detail="Anthropic API not configured")

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

        # Encode photo as base64
        b64_photo = base64.b64encode(photo_bytes).decode("utf-8")

        # Call Claude Sonnet 4.6 with vision
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": b64_photo,
                            },
                        },
                        {
                            "type": "text",
                            "text": TAG_EXTRACTION_PROMPT,
                        },
                    ],
                }
            ],
        )

        # Extract text response and parse JSON
        response_text = message.content[0].text

        # Try to extract JSON from response
        try:
            # Find JSON in response (may be wrapped in markdown)
            json_start = response_text.find("{")
            json_end = response_text.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                json_str = response_text[json_start:json_end]
                extracted = json.loads(json_str)
            else:
                extracted = json.loads(response_text)
        except json.JSONDecodeError:
            logger.warning(f"Could not parse JSON from Claude response: {response_text[:200]}")
            extracted = {
                "make": None,
                "model_name": None,
                "model_number": None,
                "model_year": None,
                "serial_number": None,
                "description": None,
                "additional_specs": {},
                "tag_condition": "unreadable",
                "confidence": "low",
            }

        return extracted
    except anthropic.APIError as e:
        logger.error(f"Claude API error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"AI extraction failed: {str(e)}")
    except Exception as e:
        logger.error(f"Photo extraction error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Extraction error: {str(e)}")


async def check_unit_completion(building_id: str, unit_id: str) -> dict:
    """Calculate completion status for a unit."""
    try:
        # Get configured asset types for building
        asset_types = await sb_query(
            "fc_asset_types",
            filters=f"?building_id=eq.{building_id}"
        )
        asset_types = asset_types or []
        total_types = len(asset_types)

        if total_types == 0:
            return {"status": "pending", "captured": 0, "total": 0}

        # Get captured assets for this unit
        captured = await sb_query(
            "fc_unit_assets",
            filters=f"?unit_id=eq.{unit_id}&is_current=eq.true"
        )
        captured = captured or []

        captured_types = set(a.get("asset_type_id") for a in captured if a.get("asset_type_id"))
        captured_count = len(captured_types)

        if captured_count == 0:
            status = "pending"
        elif captured_count < total_types:
            status = "partial"
        else:
            status = "complete"

        return {
            "status": status,
            "captured": captured_count,
            "total": total_types,
        }
    except Exception as e:
        logger.error(f"Error checking unit completion: {str(e)}")
        return {"status": "error", "captured": 0, "total": 0}


# ─────────────────────────────────────────────────────────────────────────────
# FastAPI Router
# ─────────────────────────────────────────────────────────────────────────────

router = APIRouter(prefix="/v2", tags=["field-capture"])


# ═════════════════════════════════════════════════════════════════════════════
# Setup Endpoints
# ═════════════════════════════════════════════════════════════════════════════

@router.post("/setup-tables")
async def setup_tables():
    """
    Check if field capture tables exist and return setup status.
    Tables must be created in Supabase dashboard manually.

    Required tables:
    - fc_units
    - fc_asset_types
    - fc_unit_assets
    - fc_asset_history
    """
    try:
        # Try to query each table to verify existence
        tables = ["fc_units", "fc_asset_types", "fc_unit_assets", "fc_asset_history"]
        status = {}

        for table in tables:
            try:
                result = await sb_query(table, filters="?limit=1")
                status[table] = "exists"
            except HTTPException as e:
                if "404" in str(e.detail):
                    status[table] = "missing"
                else:
                    status[table] = "error"

        all_exist = all(v == "exists" for v in status.values())

        return {
            "configured": all_exist,
            "tables": status,
            "setup_sql": "Please create tables in Supabase dashboard — see field_capture.py docstring for SQL"
        }
    except Exception as e:
        logger.error(f"Setup check error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/seed-marquis-villa/{building_id}")
async def seed_marquis_villa(building_id: str):
    """
    Seed Marquis Villa pilot data: 75 units + 3 asset types.
    This is a one-time setup endpoint for the pilot.
    """
    try:
        # Check if units already exist
        existing = await sb_query("fc_units", filters=f"?building_id=eq.{building_id}&limit=1")
        if existing:
            return {"message": "Building already has units", "count": len(existing)}

        # Create 3 asset types
        asset_types = [
            {"id": str(uuid.uuid4()), "building_id": building_id, "name": "HVAC System",
             "appfolio_type_name": "HVAC System", "per_unit": True},
            {"id": str(uuid.uuid4()), "building_id": building_id, "name": "Hot Water Heater",
             "appfolio_type_name": "Hot Water Heater", "per_unit": True},
            {"id": str(uuid.uuid4()), "building_id": building_id, "name": "Electrical Panel",
             "appfolio_type_name": "Electrical Panel", "per_unit": True},
        ]
        await sb_query("fc_asset_types", method="POST", json_body=asset_types)

        # Marquis Villa units (Property ID 791, Unit IDs 2543–2617)
        units = []
        unit_names = [
            "200 A","200 B","200 C","200 D","200 E","200 F",
            "202 A","202 B","202 C","202 D","202 E","202 F",
            "204 A","204 B","204 C","204 D","204 E","204 F",
            "206 A","206 B","206 C","206 D","206 E","206 F",
            "208 A","208 B","208 C","208 D","208 E","208 F",
            "210 A","210 B","210 C","210 D","210 E","210 F",
            "212 A","212 B","212 C","212 D","212 E","212 F",
            "214 A","214 B","214 C","214 D","214 E","214 F",
            "216 A","216 B","216 C","216 D","216 E","216 F",
            "218 A","218 B","218 C","218 D","218 E","218 F",
            "220 A","220 B","220 C","220 D","220 E",
            "222 A","222 B","222 C","222 D","222 E","222 F",
            "224 A","224 B","224 C","224 D","224 E",
            "226 A","226 B","226 C","226 D",
        ]
        appfolio_unit_ids = list(range(2543, 2618))

        for i, name in enumerate(unit_names):
            af_id = str(appfolio_unit_ids[i]) if i < len(appfolio_unit_ids) else None
            units.append({
                "id": str(uuid.uuid4()),
                "building_id": building_id,
                "unit_name": name,
                "appfolio_unit_id": af_id,
                "appfolio_property_id": "791",
                "unit_address": f"Marquis Villa, {name}, Norfolk, VA",
                "status": "active",
                "access_status": "pending",
            })

        # Bulk insert units (Supabase has max body size, batch in groups of 25)
        for i in range(0, len(units), 25):
            batch = units[i:i+25]
            await sb_query("fc_units", method="POST", json_body=batch)

        logger.info(f"Seeded Marquis Villa: {len(units)} units, 3 asset types for building {building_id}")

        return {
            "message": "Marquis Villa seeded successfully",
            "units": len(units),
            "asset_types": len(asset_types),
        }
    except Exception as e:
        logger.error(f"Seed error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


# ═════════════════════════════════════════════════════════════════════════════
# Unit Management
# ═════════════════════════════════════════════════════════════════════════════

@router.post("/buildings/{building_id}/units/import")
async def import_units(building_id: str, units: List[UnitImportPayload]):
    """Bulk import units from AppFolio or external source."""
    try:
        if not units:
            raise HTTPException(status_code=400, detail="No units provided")

        # Prepare records
        records = []
        for unit in units:
            records.append({
                "id": str(uuid.uuid4()),
                "building_id": building_id,
                "unit_name": unit.unit_name,
                "appfolio_unit_id": unit.appfolio_unit_id,
                "appfolio_property_id": unit.appfolio_property_id,
                "unit_address": unit.unit_address,
                "sqft": unit.sqft,
                "bedrooms": unit.bedrooms,
                "bathrooms": unit.bathrooms,
                "status": "active",
                "access_status": "pending",
            })

        # Bulk insert
        result = await sb_query("fc_units", method="POST", json_body=records)

        logger.info(f"Imported {len(records)} units into {building_id}")

        return {
            "imported": len(records),
            "units": result if isinstance(result, list) else records,
        }
    except Exception as e:
        logger.error(f"Import units error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/buildings/{building_id}/units")
async def list_units(building_id: str):
    """List all units for a building with completion status."""
    try:
        # Get all units
        units = await sb_query("fc_units", filters=f"?building_id=eq.{building_id}")
        units = units or []

        # Check completion status for each
        for unit in units:
            completion = await check_unit_completion(building_id, unit["id"])
            unit["completion"] = completion
            unit["completion_status"] = completion.get("status", "pending")
            unit["assets_captured"] = completion.get("captured", 0)
            unit["assets_required"] = completion.get("total", 0)

        # Count by status
        total = len(units)
        complete = sum(1 for u in units if u.get("completion", {}).get("status") == "complete")
        partial = sum(1 for u in units if u.get("completion", {}).get("status") == "partial")
        pending = sum(1 for u in units if u.get("completion", {}).get("status") == "pending")

        return {
            "units": units,
            "total": total,
            "complete": complete,
            "partial": partial,
            "pending": pending,
        }
    except Exception as e:
        logger.error(f"List units error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/buildings/{building_id}/units/{unit_id}")
async def update_unit(building_id: str, unit_id: str, payload: UnitUpdatePayload):
    """Update a unit's access status or other fields."""
    try:
        update_body = payload.dict(exclude_unset=True)
        update_body["updated_at"] = datetime.utcnow().isoformat()

        result = await sb_query(
            "fc_units",
            method="PATCH",
            json_body=update_body,
            filters=f"?id=eq.{unit_id}&building_id=eq.{building_id}"
        )

        if not result:
            raise HTTPException(status_code=404, detail="Unit not found")

        return result[0] if isinstance(result, list) else result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Update unit error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


# ═════════════════════════════════════════════════════════════════════════════
# Asset Type Configuration
# ═════════════════════════════════════════════════════════════════════════════

@router.post("/buildings/{building_id}/asset-types")
async def create_asset_type(building_id: str, payload: AssetTypePayload):
    """Create a new asset type for a building."""
    try:
        record = {
            "id": str(uuid.uuid4()),
            "building_id": building_id,
            "name": payload.name,
            "appfolio_type_name": payload.appfolio_type_name or payload.name,
            "per_unit": True,
        }

        result = await sb_query("fc_asset_types", method="POST", json_body=[record])

        return result[0] if isinstance(result, list) and result else record
    except Exception as e:
        logger.error(f"Create asset type error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/buildings/{building_id}/asset-types")
async def list_asset_types(building_id: str):
    """List all asset types configured for a building."""
    try:
        result = await sb_query(
            "fc_asset_types",
            filters=f"?building_id=eq.{building_id}&order=created_at.asc"
        )
        return {"asset_types": result or []}
    except Exception as e:
        logger.error(f"List asset types error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


# ═════════════════════════════════════════════════════════════════════════════
# Field Capture (Core Workflow)
# ═════════════════════════════════════════════════════════════════════════════

@router.post("/buildings/{building_id}/units/{unit_id}/capture")
async def capture_equipment(
    building_id: str,
    unit_id: str,
    photo: UploadFile = File(...),
    asset_type_id: str = Form(...),
    inspected_by: Optional[str] = Form(None),
    inspection_notes: Optional[str] = Form(None),
):
    """
    Capture equipment photo and extract tag data via AI.

    Flow:
    1. Upload photo to Supabase storage
    2. Send to Claude Sonnet 4.6 for tag extraction
    3. Create fc_unit_assets record with extracted data
    4. Return created asset
    """
    try:
        # Read photo file
        photo_bytes = await photo.read()
        if not photo_bytes:
            raise HTTPException(status_code=400, detail="Empty photo file")

        # Verify unit exists
        unit = await sb_query("fc_units", filters=f"?id=eq.{unit_id}&building_id=eq.{building_id}")
        if not unit:
            raise HTTPException(status_code=404, detail="Unit not found")

        # Verify asset type exists
        asset_type = await sb_query("fc_asset_types", filters=f"?id=eq.{asset_type_id}&building_id=eq.{building_id}")
        if not asset_type:
            raise HTTPException(status_code=404, detail="Asset type not found")
        asset_type_name = asset_type[0]["name"] if isinstance(asset_type, list) else asset_type.get("name")

        # Upload photo to storage
        timestamp = datetime.utcnow().isoformat().replace(":", "-")
        photo_path = f"field-capture/{building_id}/{unit_id}/{asset_type_id}_{timestamp}.jpg"
        await upload_photo_to_storage(photo_bytes, photo_path)

        # Extract equipment data from photo
        extracted = await extract_equipment_data_from_photo(photo_bytes)

        # Create asset record
        asset_id = str(uuid.uuid4())
        asset_record = {
            "id": asset_id,
            "unit_id": unit_id,
            "building_id": building_id,
            "asset_type_id": asset_type_id,
            "asset_type_name": asset_type_name,
            "make": extracted.get("make"),
            "model_name": extracted.get("model_name"),
            "model_number": extracted.get("model_number"),
            "model_year": extracted.get("model_year"),
            "serial_number": extracted.get("serial_number"),
            "description": extracted.get("description"),
            "tag_photo_path": photo_path,
            "ai_extracted": True,
            "ai_confidence": extracted.get("confidence"),
            "manually_verified": False,
            "inspected_by": inspected_by,
            "inspection_date": datetime.utcnow().isoformat(),
            "inspection_notes": inspection_notes,
            "is_current": True,
        }

        # Insert into database
        result = await sb_query("fc_unit_assets", method="POST", json_body=[asset_record])

        logger.info(f"Captured asset {asset_id} in unit {unit_id}")

        return result[0] if isinstance(result, list) and result else asset_record
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Capture equipment error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/buildings/{building_id}/units/{unit_id}/assets")
async def list_unit_assets(building_id: str, unit_id: str):
    """List all captured assets for a unit."""
    try:
        result = await sb_query(
            "fc_unit_assets",
            filters=f"?unit_id=eq.{unit_id}&building_id=eq.{building_id}&is_current=eq.true&order=created_at.desc"
        )
        return {"assets": result or []}
    except Exception as e:
        logger.error(f"List assets error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/buildings/{building_id}/assets/{asset_id}")
async def update_asset(building_id: str, asset_id: str, payload: AssetUpdatePayload):
    """Update/correct an asset's fields (manual verification)."""
    try:
        update_body = payload.dict(exclude_unset=True)
        update_body["updated_at"] = datetime.utcnow().isoformat()
        if payload.manually_verified is not None:
            update_body["manually_verified"] = payload.manually_verified

        result = await sb_query(
            "fc_unit_assets",
            method="PATCH",
            json_body=update_body,
            filters=f"?id=eq.{asset_id}&building_id=eq.{building_id}"
        )

        if not result:
            raise HTTPException(status_code=404, detail="Asset not found")

        asset = result[0] if isinstance(result, list) else result

        # Log to history if manually verified
        if payload.manually_verified:
            history_record = {
                "id": str(uuid.uuid4()),
                "unit_asset_id": asset_id,
                "building_id": building_id,
                "snapshot": asset,
                "action": "manually_verified",
                "performed_date": datetime.utcnow().isoformat(),
            }
            try:
                await sb_query("fc_asset_history", method="POST", json_body=[history_record])
            except Exception as e:
                logger.warning(f"Could not log to history: {str(e)}")

        return asset
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Update asset error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/buildings/{building_id}/assets/{asset_id}/replace")
async def replace_asset(
    building_id: str,
    asset_id: str,
    new_asset: dict = None,
):
    """
    Replace an asset: move current to history, create new record.
    Expects JSON body with new asset data.
    """
    try:
        # Get current asset
        current = await sb_query("fc_unit_assets", filters=f"?id=eq.{asset_id}&building_id=eq.{building_id}")
        if not current:
            raise HTTPException(status_code=404, detail="Asset not found")
        current_asset = current[0] if isinstance(current, list) else current

        # Store to history
        history_record = {
            "id": str(uuid.uuid4()),
            "unit_asset_id": asset_id,
            "building_id": building_id,
            "snapshot": current_asset,
            "action": "replaced",
            "performed_date": datetime.utcnow().isoformat(),
        }
        await sb_query("fc_asset_history", method="POST", json_body=[history_record])

        # Mark current as replaced
        await sb_query(
            "fc_unit_assets",
            method="PATCH",
            json_body={"is_current": False, "replaced_date": datetime.utcnow().isoformat()},
            filters=f"?id=eq.{asset_id}"
        )

        # Create new asset
        new_asset_id = str(uuid.uuid4())
        new_record = {
            "id": new_asset_id,
            "unit_id": current_asset["unit_id"],
            "building_id": building_id,
            "asset_type_id": current_asset.get("asset_type_id"),
            "asset_type_name": current_asset.get("asset_type_name"),
            "replaced_by": None,
            "is_current": True,
            **(new_asset or {}),
        }

        result = await sb_query("fc_unit_assets", method="POST", json_body=[new_record])

        # Update old to point to new
        await sb_query(
            "fc_unit_assets",
            method="PATCH",
            json_body={"replaced_by": new_asset_id},
            filters=f"?id=eq.{asset_id}"
        )

        logger.info(f"Asset {asset_id} replaced with {new_asset_id}")

        return result[0] if isinstance(result, list) and result else new_record
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Replace asset error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


# ═════════════════════════════════════════════════════════════════════════════
# Completeness & Analytics
# ═════════════════════════════════════════════════════════════════════════════

@router.get("/buildings/{building_id}/completeness")
async def get_completeness(building_id: str):
    """
    Get comprehensive completeness dashboard data.

    Returns:
    - Total units and completion counts
    - Completion % by asset type
    - List of units missing specific asset types with access status
    """
    try:
        # Get all units
        units = await sb_query("fc_units", filters=f"?building_id=eq.{building_id}")
        units = units or []
        total_units = len(units)

        # Get asset types
        asset_types = await sb_query("fc_asset_types", filters=f"?building_id=eq.{building_id}")
        asset_types = asset_types or []

        # Get all current assets
        assets = await sb_query(
            "fc_unit_assets",
            filters=f"?building_id=eq.{building_id}&is_current=eq.true"
        )
        assets = assets or []

        # Build asset type completion
        by_asset_type = []
        for asset_type in asset_types:
            type_id = asset_type["id"]
            type_name = asset_type["name"]
            captured = len([a for a in assets if a.get("asset_type_id") == type_id])
            by_asset_type.append({
                "type": type_name,
                "captured": captured,
                "total": total_units,
                "pct": round((captured / total_units * 100) if total_units > 0 else 0),
            })

        # Count unit completion
        units_complete = 0
        units_partial = 0
        units_pending = 0
        missing_units = []

        for unit in units:
            completion = await check_unit_completion(building_id, unit["id"])
            status = completion.get("status", "pending")

            if status == "complete":
                units_complete += 1
            elif status == "partial":
                units_partial += 1
            else:
                units_pending += 1

            # Track missing asset types
            if status != "complete":
                unit_assets = [a for a in assets if a.get("unit_id") == unit["id"]]
                captured_type_ids = set(a.get("asset_type_id") for a in unit_assets)
                missing_types = [
                    at["name"] for at in asset_types
                    if at["id"] not in captured_type_ids
                ]

                missing_units.append({
                    "unit_name": unit["unit_name"],
                    "unit_id": unit["id"],
                    "missing_types": missing_types,
                    "access_status": unit.get("access_status"),
                    "access_note": unit.get("access_note"),
                })

        completion_pct = round((units_complete / total_units * 100) if total_units > 0 else 0)

        return {
            "total_units": total_units,
            "units_complete": units_complete,
            "units_partial": units_partial,
            "units_pending": units_pending,
            "completion_pct": completion_pct,
            "by_asset_type": by_asset_type,
            "missing_units": missing_units,
        }
    except Exception as e:
        logger.error(f"Completeness error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


# ═════════════════════════════════════════════════════════════════════════════
# Export & Integration
# ═════════════════════════════════════════════════════════════════════════════

@router.get("/buildings/{building_id}/export/appfolio-csv")
async def export_appfolio_csv(building_id: str):
    """
    Export captured assets as CSV in AppFolio fixed asset import format.

    CSV columns:
    Unit ID, Property ID, Unit Name, Property Name, Asset ID (blank),
    Type, Make, Model Name, Model Number, Model Year, Serial Number,
    Description, Status, Placed in Service, Warranty Expiration
    """
    try:
        # Get units
        units = await sb_query("fc_units", filters=f"?building_id=eq.{building_id}")
        units = units or []
        unit_map = {u["id"]: u for u in units}

        # Get current assets
        assets = await sb_query(
            "fc_unit_assets",
            filters=f"?building_id=eq.{building_id}&is_current=eq.true"
        )
        assets = assets or []

        # Build CSV
        lines = []
        lines.append(
            "Unit ID,Property ID,Unit Name,Property Name,Asset ID,Type,Make,Model Name,"
            "Model Number,Model Year,Serial Number,Description,Status,Placed in Service,Warranty Expiration"
        )

        for asset in assets:
            unit_id = asset.get("unit_id")
            unit = unit_map.get(unit_id, {})

            # Escape CSV fields
            def escape_csv(val):
                if val is None:
                    return ""
                val = str(val).replace('"', '""')
                if "," in val or '"' in val or "\n" in val:
                    return f'"{val}"'
                return val

            row = [
                escape_csv(unit.get("appfolio_unit_id", "")),
                escape_csv(unit.get("appfolio_property_id", "")),
                escape_csv(unit.get("unit_name", "")),
                escape_csv(unit.get("unit_address", "")),
                "",  # Asset ID blank
                escape_csv(asset.get("asset_type_name", "")),
                escape_csv(asset.get("make", "")),
                escape_csv(asset.get("model_name", "")),
                escape_csv(asset.get("model_number", "")),
                escape_csv(asset.get("model_year", "")),
                escape_csv(asset.get("serial_number", "")),
                escape_csv(asset.get("description", "")),
                escape_csv(asset.get("status", "Installed")),
                escape_csv(asset.get("placed_in_service", "")),
                escape_csv(asset.get("warranty_expiration", "")),
            ]
            lines.append(",".join(row))

        csv_content = "\n".join(lines)

        # Return as downloadable CSV
        return StreamingResponse(
            iter([csv_content]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=appfolio-assets-{building_id}.csv"},
        )
    except Exception as e:
        logger.error(f"Export CSV error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
