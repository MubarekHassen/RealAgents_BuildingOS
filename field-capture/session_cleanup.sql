-- Session cleanup: delete expired fc_sessions rows
-- Run this in Supabase SQL Editor to set up daily cleanup
-- Requires pg_cron extension (enabled by default in Supabase)

-- Enable pg_cron if not already enabled
CREATE EXTENSION IF NOT EXISTS pg_cron;

-- Schedule daily cleanup at 3 AM UTC
SELECT cron.schedule(
    'cleanup-expired-sessions',
    '0 3 * * *',  -- every day at 3:00 AM UTC
    $$DELETE FROM fc_sessions WHERE expires_at < NOW()$$
);

-- To verify the job was created:
-- SELECT * FROM cron.job;

-- To remove the job if needed:
-- SELECT cron.unschedule('cleanup-expired-sessions');
