import os
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv("backend/.env")
url = os.environ.get("SUPABASE_URL")
if not url.endswith('/'):
    url += '/'
key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
supabase: Client = create_client(url, key)

print("Listing ABC:")
try:
    res = supabase.storage.from_("test-building-files").list("ABC/Architectural/Drawing")
    for item in res:
        print(item)
except Exception as e:
    print(e)
