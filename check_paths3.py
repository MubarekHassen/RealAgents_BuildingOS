import os
import certifi
import ssl

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
    res = supabase.storage.from_("test-building-files").list("ABC/1. Architectural/Drawing")
    for item in res:
        print(item['name'])

except Exception as e:
    print("Error:", e)
