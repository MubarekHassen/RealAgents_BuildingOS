import os
import certifi
import urllib.parse
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
    path = "ABC/test #3 again.txt"
    encoded = "/".join(urllib.parse.quote(p) for p in path.split("/"))
    print("Encoded:", encoded)
    res = supabase.storage.from_("test-building-files").upload(encoded, b"hello")
    print(res)
except Exception as e:
    import traceback
    traceback.print_exc()
