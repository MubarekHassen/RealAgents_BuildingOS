import os
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

url = os.getenv('SUPABASE_URL')
key = os.getenv('SUPABASE_SERVICE_ROLE_KEY')
supabase = create_client(url, key)

def check_table(name):
    try:
        supabase.table(name).select("*").limit(1).execute()
        print(f"Table '{name}' exists")
    except Exception as e:
        if "does not exist" in str(e).lower():
            print(f"Table '{name}' does not exist")
        else:
            print(f"Error checking table '{name}': {e}")

check_table("Documents")
check_table("DocumentUpdates")
check_table("File")
check_table("Building")
