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
    res = supabase.storage.from_("test-building-files").list("test 21/Architectural/Drawing")
    for item in res:
        print(item['name'])
    
    print("Listing top level files:")
    res = supabase.table("File").select("*").execute()
    for row in res.data:
        print(row['filename'], row['s3Key'])
except Exception as e:
    print("Error:", e)
