import os
import certifi
from dotenv import load_dotenv
from supabase import create_client, Client

os.environ["SSL_CERT_FILE"] = certifi.where()
load_dotenv("backend/.env")

url = os.environ.get("SUPABASE_URL")
if url.endswith('/'):
    url = url[:-1]
key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

try:
    supabase: Client = create_client(url, key)
    # Search recursively via DB
    print("Searching recursively via DB...")
    db_res = supabase.table("File").select("*").ilike("filename", "%reflection%").execute()
    for row in db_res.data:
        print(row)
except Exception as e:
    print("Error:", e)
