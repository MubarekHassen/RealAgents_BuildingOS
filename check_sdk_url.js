const { createClient } = require('@supabase/supabase-js');
const dotenv = require('dotenv');
dotenv.config({ path: 'backend/.env' });

const supabaseUrl = process.env.SUPABASE_URL;
const supabaseKey = process.env.SUPABASE_SERVICE_ROLE_KEY;

const supabase = createClient(supabaseUrl, supabaseKey);

const { data } = supabase.storage
    .from('test-building-files')
    .getPublicUrl('ABC/Architectural/Drawing/reflection #3 comm 3440 - Google Docs.pdf');

console.log('Public URL from SDK:', data.publicUrl);
