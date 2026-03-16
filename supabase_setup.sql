-- Backup of Supabase Schema for migrating to new project
-- Run this in your NEW Supabase project's SQL Editor

-- 1. Create custom types
DO $$ BEGIN
    CREATE TYPE document_category AS ENUM ('ARCHITECTURAL', 'MECHANICAL', 'ELECTRICAL', 'PLUMBING', 'STRUCTURAL', 'REPORTS', 'OTHER');
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

-- 2. Create users table
CREATE TABLE IF NOT EXISTS public.users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    firebase_uid TEXT UNIQUE,
    email TEXT UNIQUE NOT NULL,
    name TEXT,
    company TEXT,
    role TEXT DEFAULT 'user',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now())
);

-- 3. Create invites table
CREATE TABLE IF NOT EXISTS public.invites (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email TEXT NOT NULL,
    company TEXT NOT NULL,
    role TEXT DEFAULT 'user',
    token TEXT UNIQUE NOT NULL,
    created_by TEXT, -- firebase_uid
    used BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()),
    expires_at TIMESTAMP WITH TIME ZONE DEFAULT (timezone('utc'::text, now()) + interval '7 days')
);

-- 4. Create Building table
CREATE TABLE IF NOT EXISTS public."Building" (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    name TEXT NOT NULL,
    address TEXT NOT NULL,
    city TEXT,
    country TEXT,
    floors INTEGER DEFAULT 0,
    sqft INTEGER DEFAULT 0,
    "sqFt" INTEGER DEFAULT 0,
    "companyId" TEXT,
    "hvacHealth" INTEGER DEFAULT 100,
    "electricalHealth" INTEGER DEFAULT 100,
    "waterHealth" INTEGER DEFAULT 100,
    "fireSafetyHealth" INTEGER DEFAULT 100,
    temperature NUMERIC DEFAULT 72.0,
    humidity NUMERIC DEFAULT 45.0,
    "energyUsage" INTEGER DEFAULT 2000,
    "airQuality" TEXT DEFAULT 'Good',
    utilization INTEGER DEFAULT 85,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now())
);

-- 5. Create File table
CREATE TABLE IF NOT EXISTS public."File" (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    "companyId" TEXT,
    "buildingId" TEXT,
    folder TEXT,
    filename TEXT,
    "fileType" TEXT,
    "s3Key" TEXT,
    "uploadedBy" TEXT,
    "createdAt" TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now())
);

-- 6. Create Documents table (newer structure used by RAG)
CREATE TABLE IF NOT EXISTS public."Documents" (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    building_id TEXT REFERENCES public."Building"(id) ON DELETE CASCADE,
    category document_category,
    filename TEXT NOT NULL,
    s3_key TEXT NOT NULL UNIQUE,
    company_id TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now())
);

-- 7. Create DocumentUpdates table
CREATE TABLE IF NOT EXISTS public."DocumentUpdates" (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID REFERENCES public."File"(id) ON DELETE CASCADE,
    user_id UUID REFERENCES public.users(id),
    type TEXT NOT NULL,
    s3_version_id TEXT,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now())
);

-- 8. Create Analytics table
CREATE TABLE IF NOT EXISTS public."Analytics" (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    building_id TEXT,
    metric_name TEXT,
    metrics JSONB DEFAULT '{}'::jsonb,
    timestamp TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now())
);

-- 9. Create documents-openai table (for OpenAI vector embeddings)
-- Note: Requires pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS public."documents-openai" (
    id BIGSERIAL PRIMARY KEY,
    content TEXT,
    metadata JSONB,
    embedding VECTOR(1536)
);

-- 10. Enable Row Level Security (RLS) but set permissive policies for backend usage
ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.invites ENABLE ROW LEVEL SECURITY;
ALTER TABLE public."Building" ENABLE ROW LEVEL SECURITY;
ALTER TABLE public."File" ENABLE ROW LEVEL SECURITY;
ALTER TABLE public."Documents" ENABLE ROW LEVEL SECURITY;
ALTER TABLE public."DocumentUpdates" ENABLE ROW LEVEL SECURITY;
ALTER TABLE public."Analytics" ENABLE ROW LEVEL SECURITY;
ALTER TABLE public."documents-openai" ENABLE ROW LEVEL SECURITY;

-- Permissive policies (you can restrict these later)
CREATE POLICY "Enable all access" ON public.users FOR ALL USING (true);
CREATE POLICY "Enable all access" ON public.invites FOR ALL USING (true);
CREATE POLICY "Enable all access" ON public."Building" FOR ALL USING (true);
CREATE POLICY "Enable all access" ON public."File" FOR ALL USING (true);
CREATE POLICY "Enable all access" ON public."Documents" FOR ALL USING (true);
CREATE POLICY "Enable all access" ON public."DocumentUpdates" FOR ALL USING (true);
CREATE POLICY "Enable all access" ON public."Analytics" FOR ALL USING (true);
CREATE POLICY "Enable all access" ON public."documents-openai" FOR ALL USING (true);

-- 11. Create Storage Buckets (requires inserting into storage.buckets)
INSERT INTO storage.buckets (id, name, public) VALUES ('test-building-files', 'test-building-files', true) ON CONFLICT DO NOTHING;
INSERT INTO storage.buckets (id, name, public) VALUES ('building-embeddings', 'building-embeddings', true) ON CONFLICT DO NOTHING;
