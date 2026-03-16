-- Create DocumentUpdates table compatible with existing File table
CREATE TABLE IF NOT EXISTS public."DocumentUpdates" (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID REFERENCES public."File"(id) ON DELETE CASCADE, -- Linked to File, not Documents
    user_id UUID, -- Storing Auth UID directly
    type TEXT NOT NULL, -- 'note', 'highlight', 'new_file_upload'
    s3_version_id TEXT,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now())
);

-- Enable RLS
ALTER TABLE public."DocumentUpdates" ENABLE ROW LEVEL SECURITY;

-- Allow all access for authenticated users (simplified policy)
CREATE POLICY "Enable all access" ON public."DocumentUpdates" FOR ALL TO authenticated USING (true);
CREATE POLICY "Enable insert for authenticated" ON public."DocumentUpdates" FOR INSERT TO authenticated WITH CHECK (true);
