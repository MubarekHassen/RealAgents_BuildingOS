# JWT Key Migration Plan

## Overview
Migrate from legacy Supabase JWT keys (`eyJhbGciOiJIUzI1NiIs...` format) to the new `sb_publishable_` / `sb_secret_` key format. This improves security by making keys distinguishable, rotatable, and compatible with Supabase's latest auth infrastructure.

## Prerequisites
- Supabase project dashboard access (owner role)
- Access to Railway environment variables for both services (BuildingOS MVP, Field Capture)
- Maintenance window (~5 minutes of downtime)

## Migration Steps

### Phase 1: Preparation (no downtime)
1. **Audit current key usage**
   - Search codebase for all references to `SUPABASE_KEY`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`
   - Confirm both services use `service_role` key only (no anon key in backend)
   - Document every environment where keys are set: Railway (prod), `.env` (local dev)

2. **Generate new keys in Supabase Dashboard**
   - Go to Project Settings > API
   - If the project supports the new key format, generate `sb_publishable_` (anon equivalent) and `sb_secret_` (service_role equivalent)
   - Copy both new keys securely (password manager, not plaintext)

3. **Test new keys locally**
   - Update local `.env` with new `sb_secret_` key as `SUPABASE_SERVICE_ROLE_KEY`
   - Run full test suite / smoke test against Supabase
   - Verify: auth, reads, writes, storage uploads, RPC calls all work

### Phase 2: Deploy (brief downtime)
4. **Update Railway environment variables**
   - BuildingOS MVP service: set `SUPABASE_SERVICE_ROLE_KEY` to new `sb_secret_` key
   - Field Capture service: set `SUPABASE_SERVICE_ROLE_KEY` to new `sb_secret_` key
   - If any service uses anon key: set to new `sb_publishable_` key

5. **Redeploy both services**
   - Trigger redeploy on Railway for BuildingOS MVP
   - Trigger redeploy on Railway for Field Capture
   - Monitor logs for auth errors during rollout

6. **Verify in production**
   - Test field capture login flow (PIN auth)
   - Test document upload + Q&A in main platform
   - Check Supabase dashboard for any failed auth requests

### Phase 3: Cleanup
7. **Revoke legacy keys**
   - In Supabase Dashboard, revoke/rotate the old JWT keys
   - This invalidates any leaked copies of the old keys

8. **Update documentation**
   - Update any READMEs or setup guides referencing key format
   - Update `.env.example` files with placeholder format

## Rollback Plan
If issues arise after deploying new keys:
1. Revert Railway env vars to old legacy keys
2. Redeploy both services
3. Old keys remain valid until explicitly revoked

## Timeline
- Phase 1: 30 minutes (can be done anytime)
- Phase 2: 5 minutes (schedule during low-traffic window)
- Phase 3: 10 minutes (after confirming production stability for 24 hours)

## Notes
- The `service_role` key bypasses RLS, so the migration is transparent to RLS policies
- No database schema changes required
- No client-side changes needed (keys are server-side only)
- Legacy keys continue to work until revoked, so there is no hard deadline
