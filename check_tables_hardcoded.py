import os
from supabase import create_client

url = "https://lxlrwiltjwfbvjkhsgis.supabase.co"
key = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imx4bHJ3aWx0andmYnZqa2hzZ2lzIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc2NjA2ODM3MywiZXhwIjoyMDgxNjQ0MzczfQ.tC2262S-NUjLfmosyoihN--PIt-3qB_DiJ2MHmRBSBU"
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
