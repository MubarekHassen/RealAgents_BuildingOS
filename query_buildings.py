from dotenv import load_dotenv
import os
from supabase import create_client, Client

load_dotenv("backend/.env")
url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
supabase: Client = create_client(url, key)

res = supabase.table("Building").select("*").execute()
print(res.data)
