import os, certifi
from dotenv import load_dotenv
from supabase import create_client

os.environ["SSL_CERT_FILE"] = certifi.where()
load_dotenv("backend/.env")

url = os.environ.get("SUPABASE_URL")
if url.endswith("/"): url = url[:-1]
key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

supabase = create_client(url, key)
bucket = "test-building-files"

# Create a dummy file
supabase.storage.from_(bucket).upload("ABC/Mechanical/Reports/dummy.pdf", b"hello", file_options={"upsert": "true"})

# Attempt to move the dummy file
import time
timestamp = int(time.time())
old_path = "ABC/Mechanical/Reports/dummy.pdf"
trash_path = f"recently-deleted/{timestamp}_" + old_path.replace('/', '_')

try:
    res = supabase.storage.from_(bucket).move(old_path, trash_path)
    print("Move success:", res)
except Exception as e:
    print("Move failed:", e)

    try:
        # Instead, try to download and then delete
        data = supabase.storage.from_(bucket).download(old_path)
        supabase.storage.from_(bucket).remove([old_path])
        supabase.storage.from_(bucket).upload(trash_path, data)
        print("Fallback move success")
    except Exception as e2:
        print("Fallback move failed:", e2)

