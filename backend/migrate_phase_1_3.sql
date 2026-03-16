-- Phase 1.3 File Management Migration

-- 1. Create Document Category Enum
DO $$ BEGIN
    CREATE TYPE document_category AS ENUM (
        'ARCHITECTURAL', 
        'MECHANICAL', 
        'ELECTRICAL', 
        'PLUMBING', 
        'STRUCTURAL', 
        'REPORTS'
    );
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

-- 2. Create Documents Table
CREATE TABLE IF NOT EXISTS public."Documents" (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    building_id UUID REFERENCES public."Building"(id) ON DELETE CASCADE,
    category document_category NOT NULL,
    filename TEXT NOT NULL,
    s3_key TEXT NOT NULL,
    s3_version_id TEXT,
    company_id TEXT, -- Added for isolation
    uploaded_by UUID REFERENCES public."users"(id), -- Added for metadata
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now())
);

-- 3. Create DocumentUpdates Table
CREATE TABLE IF NOT EXISTS public."DocumentUpdates" (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID REFERENCES public."Documents"(id) ON DELETE CASCADE,
    user_id UUID REFERENCES public."users"(id),
    type TEXT NOT NULL, -- 'note', 'highlight', 'new_file_upload'
    s3_version_id TEXT,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now())
);

-- 4. Migrate data from old File table if it exists
DO $$
BEGIN
    IF EXISTS (SELECT FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'File') THEN
        INSERT INTO public."Documents" (id, building_id, category, filename, s3_key, company_id, created_at)
        SELECT 
            id, 
            buildingId::uuid, 
            CASE 
                WHEN upper(folder) = 'ARCHITECTURAL' THEN 'ARCHITECTURAL'::document_category
                WHEN upper(folder) = 'MECHANICAL' THEN 'MECHANICAL'::document_category
                WHEN upper(folder) = 'ELECTRICAL' THEN 'ELECTRICAL'::document_category
                WHEN upper(folder) = 'PLUMBING' THEN 'PLUMBING'::document_category
                WHEN upper(folder) = 'STRUCTURAL' THEN 'STRUCTURAL'::document_category
                WHEN upper(folder) = 'REPORTS' THEN 'REPORTS'::document_category
                ELSE 'ARCHITECTURAL'::document_category
            END,
            filename,
            "s3Key",
            "companyId",
            "createdAt"
        FROM public."File"
        ON CONFLICT (id) DO NOTHING;
    END IF;
END $$;

-- Enable RLS
ALTER TABLE public."Documents" ENABLE ROW LEVEL SECURITY;
ALTER TABLE public."DocumentUpdates" ENABLE ROW LEVEL SECURITY;

-- Simple policies for now (backend handles most logic)
CREATE POLICY "Allow all for authenticated users" ON public."Documents" FOR ALL TO authenticated USING (true);
CREATE POLICY "Allow all for authenticated users" ON public."DocumentUpdates" FOR ALL TO authenticated USING (true);
