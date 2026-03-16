import os
import certifi
import urllib.parse
from dotenv import load_dotenv
import requests

os.environ["SSL_CERT_FILE"] = certifi.where()
load_dotenv("backend/.env")

url = os.environ.get("SUPABASE_URL")
if url.endswith('/'):
    url = url[:-1]

test_path = "ABC/Architectural/Drawing/reflection #3 comm 3440 - Google Docs.pdf"
encoded_path = "/".join([urllib.parse.quote(p) for p in test_path.split("/")])

public_url = f"{url}/storage/v1/object/public/test-building-files/{encoded_path}"
print("Fetching:", public_url)

r = requests.get(public_url)
print("Status:", r.status_code)
print("Body:", r.text[:200])
