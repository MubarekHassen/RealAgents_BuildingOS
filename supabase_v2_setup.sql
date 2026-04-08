-- ============================================================
-- BuildingOS v2 — Field Capture Tables
-- Run this SQL in the Supabase SQL Editor (Dashboard → SQL)
-- ============================================================

-- 1. Units (one row per apartment/townhome unit)
CREATE TABLE IF NOT EXISTS fc_units (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    building_id TEXT NOT NULL,
    unit_name TEXT NOT NULL,
    appfolio_unit_id TEXT,
    appfolio_property_id TEXT,
    unit_address TEXT,
    sqft INTEGER,
    bedrooms NUMERIC(3,1),
    bathrooms NUMERIC(3,1),
    status TEXT DEFAULT 'active',         -- active, inactive
    access_status TEXT DEFAULT 'pending',  -- pending, accessible, no_access, scheduled
    access_note TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_fc_units_building ON fc_units(building_id);

-- 2. Asset Types (configured per building: HVAC, Hot Water, Electrical Panel, etc.)
CREATE TABLE IF NOT EXISTS fc_asset_types (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    building_id TEXT NOT NULL,
    name TEXT NOT NULL,
    appfolio_type_name TEXT,
    per_unit BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_fc_asset_types_building ON fc_asset_types(building_id);

-- 3. Unit Assets (captured equipment data per unit)
CREATE TABLE IF NOT EXISTS fc_unit_assets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    unit_id UUID REFERENCES fc_units(id),
    building_id TEXT NOT NULL,
    asset_type_id UUID REFERENCES fc_asset_types(id),
    asset_type_name TEXT,
    make TEXT,
    model_name TEXT,
    model_number TEXT,
    model_year TEXT,
    serial_number TEXT,
    description TEXT,
    tag_photo_path TEXT,
    ai_extracted BOOLEAN DEFAULT false,
    ai_confidence TEXT,                     -- high, medium, low
    manually_verified BOOLEAN DEFAULT false,
    status TEXT DEFAULT 'Installed',
    placed_in_service TEXT,
    warranty_expiration TEXT,
    condition_rating TEXT,                   -- excellent, good, fair, poor
    estimated_age_years INTEGER,
    estimated_remaining_life INTEGER,
    inspected_by TEXT,
    inspection_date TIMESTAMPTZ,
    inspection_notes TEXT,
    is_current BOOLEAN DEFAULT true,
    replaced_by UUID,
    replaced_date TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_fc_unit_assets_unit ON fc_unit_assets(unit_id);
CREATE INDEX IF NOT EXISTS idx_fc_unit_assets_building ON fc_unit_assets(building_id);
CREATE INDEX IF NOT EXISTS idx_fc_unit_assets_current ON fc_unit_assets(is_current) WHERE is_current = true;

-- 4. Asset History (tracks replacements and verifications)
CREATE TABLE IF NOT EXISTS fc_asset_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    unit_asset_id UUID REFERENCES fc_unit_assets(id),
    building_id TEXT NOT NULL,
    snapshot JSONB,
    action TEXT,                             -- replaced, manually_verified, updated
    performed_by TEXT,
    performed_date TIMESTAMPTZ DEFAULT now(),
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_fc_asset_history_asset ON fc_asset_history(unit_asset_id);

-- ============================================================
-- Enable Row Level Security (RLS) — open for service role
-- ============================================================
ALTER TABLE fc_units ENABLE ROW LEVEL SECURITY;
ALTER TABLE fc_asset_types ENABLE ROW LEVEL SECURITY;
ALTER TABLE fc_unit_assets ENABLE ROW LEVEL SECURITY;
ALTER TABLE fc_asset_history ENABLE ROW LEVEL SECURITY;

-- Allow service role full access
CREATE POLICY "Service role full access" ON fc_units FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Service role full access" ON fc_asset_types FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Service role full access" ON fc_unit_assets FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Service role full access" ON fc_asset_history FOR ALL USING (true) WITH CHECK (true);

-- ============================================================
-- Done! Tables are ready for BuildingOS v2 Field Capture.
-- ============================================================
