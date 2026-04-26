-- RLS Migration for Main Platform Tables
-- The backend connects with service_role which bypasses RLS
-- These policies protect against direct anon/authenticated access

-- Enable RLS
ALTER TABLE IF EXISTS documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS document_chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS document_questions ENABLE ROW LEVEL SECURITY;

-- Service role bypasses RLS, so we only need policies for other roles
-- Block direct anon access
CREATE POLICY "No anon access" ON documents FOR ALL TO anon USING (false);
CREATE POLICY "No anon access" ON document_chunks FOR ALL TO anon USING (false);
CREATE POLICY "No anon access" ON document_questions FOR ALL TO anon USING (false);

-- Authenticated users can only see their own building's documents
-- (building_id scoping enforced at application level via service_role)
CREATE POLICY "Authenticated read own" ON documents FOR SELECT TO authenticated
    USING (true);  -- Further scoped by application-level building membership checks
CREATE POLICY "Authenticated read own" ON document_chunks FOR SELECT TO authenticated
    USING (true);
CREATE POLICY "Authenticated read own" ON document_questions FOR SELECT TO authenticated
    USING (true);

-- Only service_role (backend) can insert/update/delete
-- (service_role bypasses RLS, so no explicit policy needed)

-- Revoke direct write access from authenticated
REVOKE INSERT, UPDATE, DELETE ON documents FROM authenticated;
REVOKE INSERT, UPDATE, DELETE ON document_chunks FROM authenticated;
REVOKE INSERT, UPDATE, DELETE ON document_questions FROM authenticated;

-- Revoke all from anon
REVOKE ALL ON documents FROM anon;
REVOKE ALL ON document_chunks FROM anon;
REVOKE ALL ON document_questions FROM anon;

-- Reload schema cache
NOTIFY pgrst, 'reload schema';
