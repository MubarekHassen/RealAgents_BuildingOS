from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
import uuid
import asyncio
import random
import os
from contextlib import asynccontextmanager
from supabase import create_client, Client
from dotenv import load_dotenv
from pyiceberg.catalog import load_catalog
import pyarrow as pa
import datetime

# Load environment variables
load_dotenv()

# Initialize Supabase client
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", os.getenv("SUPABASE_ANON_KEY", ""))
supabase: Client = None

# Supabase Vectors configuration (for RAG embeddings)
VECTORS_TABLE = "documents-openai"  # 1536 dimensions for OpenAI embeddings

# Initialize Supabase client with error handling
def init_supabase_client():
    """Initialize Supabase client with proper error handling"""
    global supabase
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("Warning: SUPABASE_URL or SUPABASE_KEY not set in environment variables")
        return None
    
    try:
        client = create_client(SUPABASE_URL, SUPABASE_KEY)
        # Test connection with a simple query
        try:
            # This will fail gracefully if there's a connection issue
            client.table("Building").select("id").limit(1).execute()
        except Exception as test_error:
            # If it's just a table not found or permission error, client is still valid
            if "permission" in str(test_error).lower() or "policy" in str(test_error).lower():
                print(f"Supabase client initialized (connection OK, permissions may need setup)")
            elif "does not exist" in str(test_error).lower() or "relation" in str(test_error).lower():
                print(f"Supabase client initialized (connection OK, table may not exist yet)")
            else:
                # Network/DNS errors
                error_str = str(test_error).lower()
                if "nodename" in error_str or "servname" in error_str or "name resolution" in error_str:
                    print(f"Warning: Network/DNS error connecting to Supabase: {test_error}")
                    print(f"  URL: {SUPABASE_URL}")
                    print(f"  Check your internet connection and Supabase URL")
                    return None
                else:
                    print(f"Supabase client initialized (connection test error: {test_error})")
        
        print(f"Supabase client initialized successfully: {SUPABASE_URL[:50]}...")
        return client
    except Exception as e:
        print(f"Warning: Failed to initialize Supabase client: {e}")
        return None

supabase = init_supabase_client()

# ===== SUPABASE ANALYTICS (ICEBERG) INITIALIZATION =====
PROJECT_REF = os.getenv("ICEBERG_PROJECT_REF", "dummy_ref")
WAREHOUSE = "test"
TOKEN = os.getenv("ICEBERG_TOKEN", "dummy_token")
S3_ACCESS_KEY = os.getenv("ICEBERG_S3_ACCESS_KEY", "dummy_access_key")
S3_SECRET_KEY = os.getenv("ICEBERG_S3_SECRET_KEY", "dummy_secret_key")
S3_REGION = "us-west-2"
S3_ENDPOINT = f"https://{PROJECT_REF}.storage.supabase.co/storage/v1/s3"
CATALOG_URI = f"https://{PROJECT_REF}.storage.supabase.co/storage/v1/iceberg"

iceberg_catalog = None
iceberg_table = None

def init_iceberg():
    global iceberg_catalog, iceberg_table
    try:
        print("Initializing Iceberg REST Catalog...")
        iceberg_catalog = load_catalog(
            "supabase",
            type="rest",
            warehouse=WAREHOUSE,
            uri=CATALOG_URI,
            token=TOKEN,
            **{
                "py-io-impl": "pyiceberg.io.pyarrow.PyArrowFileIO",
                "s3.endpoint": S3_ENDPOINT,
                "s3.access-key-id": S3_ACCESS_KEY,
                "s3.secret-access-key": S3_SECRET_KEY,
                "s3.region": S3_REGION,
                "s3.force-virtual-addressing": False,
            },
        )
        
        # Create namespace
        iceberg_catalog.create_namespace_if_not_exists("default")
        
        # Define schema for building metrics
        metrics_schema = pa.schema([
            pa.field("building_id", pa.string()),
            pa.field("building_name", pa.string()),
            pa.field("temperature", pa.float64()),
            pa.field("humidity", pa.float64()),
            pa.field("energy_usage", pa.int64()),
            pa.field("timestamp", pa.timestamp("ms")),
        ])
        
        # Create or load table
        iceberg_table = iceberg_catalog.create_table_if_not_exists(("default", "building_metrics"), schema=metrics_schema)
        print("Iceberg catalog and table 'building_metrics' initialized successfully.")
    except Exception as e:
        print(f"Warning: Failed to initialize Iceberg catalog: {e}")

# Try to init iceberg
init_iceberg()

# In-memory storage (backup/sync with Supabase)
buildings_db = []

# Environmental data storage
buildings_environmental = {}

async def update_environmental_data():
    """Update environmental data for all buildings with realistic random changes"""
    while True:
        await asyncio.sleep(20)  # Updated to 20 seconds as requested (less volume)
        
        # Ensure all current buildings have environmental data
        for building in buildings_db:
            building_id = building["id"]
            if building_id not in buildings_environmental:
                # Initialize if missing
                buildings_environmental[building_id] = {
                    "temperature": 70.0,
                    "humidity": 45.0,
                    "airQuality": "Good",
                    "energyUsage": 2000.0,
                    "utilization": 85,
                    # System Health (0-100)
                    "hvacHealth": 98,
                    "electricalHealth": 100,
                    "waterHealth": 100,
                    "fireSafetyHealth": 100
                }
        
        # Update environmental data for all buildings
        for building_id in list(buildings_environmental.keys()):
            # Skip if building no longer exists
            if not any(b["id"] == building_id for b in buildings_db):
                continue
                
            env = buildings_environmental[building_id]
            
            # Temperature: realistic change ±0.5°F to ±1.5°F, keep between 65-75°F
            temp_change = random.uniform(-1.5, 1.5)
            env["temperature"] = round(max(65, min(75, env["temperature"] + temp_change)), 2)
            
            # Humidity: realistic change ±1% to ±3%, keep between 35-55%
            humidity_change = random.uniform(-3, 3)
            env["humidity"] = round(max(35, min(55, env["humidity"] + humidity_change)), 2)
            
            # Energy Usage: realistic change ±50 to ±150 kWh, keep positive
            energy_change = random.uniform(-150, 150)
            env["energyUsage"] = round(max(500, env["energyUsage"] + energy_change))
            
            # Air Quality: cycle through good states occasionally
            if random.random() < 0.1:  # 10% chance to change air quality
                env["airQuality"] = random.choice(["Excellent", "Good", "Fair"])

            # Utilization: realistic change ±1% to ±2%, keep between 0-100%
            util_change = random.randint(-2, 2)
            env["utilization"] = max(0, min(100, env.get("utilization", 85) + util_change))

            # System Health Updates
            # HVAC
            if random.random() < 0.3: # 30% chance to update
                change = random.randint(-2, 2)
                env["hvacHealth"] = max(60, min(100, env.get("hvacHealth", 98) + change))
            
            # Electrical
            if random.random() < 0.3:
                change = random.randint(-2, 2)
                env["electricalHealth"] = max(60, min(100, env.get("electricalHealth", 100) + change))

            # Water
            if random.random() < 0.3:
                change = random.randint(-2, 2)
                env["waterHealth"] = max(60, min(100, env.get("waterHealth", 100) + change))

            # Fire Safety
            if random.random() < 0.3:
                change = random.randint(-2, 2)
                env["fireSafetyHealth"] = max(60, min(100, env.get("fireSafetyHealth", 100) + change))

            # Sync to Supabase
            if supabase:
                try:
                    # 1. Update Current State
                    supabase.table("Building").update({
                        "temperature": env["temperature"],
                        "humidity": env["humidity"],
                        "energyUsage": int(env["energyUsage"]),
                        "airQuality": env.get("airQuality", "Good"),
                        "utilization": int(env.get("utilization", 85)),
                        "hvacHealth": int(env.get("hvacHealth", 98)),
                        "electricalHealth": int(env.get("electricalHealth", 100)),
                        "waterHealth": int(env.get("waterHealth", 100)),
                        "fireSafetyHealth": int(env.get("fireSafetyHealth", 100))
                    }).eq("id", building_id).execute()
                    print(f"Sync Building {building_id}: Temp={env['temperature']}, Energy={int(env['energyUsage'])}")

                    # 2. Log to Analytics (History / Iceberg)
                    # Try Iceberg first if available
                    if iceberg_table:
                        try:
                            # Use building name for context in analytics
                            b_name = next((b["name"] for b in buildings_db if b["id"] == building_id), "Unknown")
                            current_time = datetime.datetime.now()
                            
                            data = pa.table({
                                "building_id": [str(building_id)],
                                "building_name": [str(b_name)],
                                "temperature": [float(env["temperature"])],
                                "humidity": [float(env["humidity"])],
                                "energy_usage": [int(env["energyUsage"])],
                                "timestamp": [current_time],
                            })
                            iceberg_table.append(data)
                        except Exception as ie:
                            print(f"Iceberg log error for {building_id}: {ie}")
                    
                    # Also log to Postgres History
                    try:
                        supabase.table("Analytics").insert({
                            "building_id": str(building_id), 
                            "metric_name": "environmental",
                            "metrics": {
                                "temperature": env["temperature"],
                                "humidity": env["humidity"],
                                "energy": int(env["energyUsage"]),
                                "hvac": int(env.get("hvacHealth", 98)),
                                "electrical": int(env.get("electricalHealth", 100)),
                                "water": int(env.get("waterHealth", 100)),
                                "fire": int(env.get("fireSafetyHealth", 100))
                            }
                        }).execute()
                        print(f"Logged history to Analytics for {building_id}")
                    except Exception as pe:
                        print(f"Analytics history log error for {building_id}: {pe}")
                except Exception as e:
                    print(f"CRITICAL: Error syncing to Supabase for {building_id}: {e}")
        
        print(f"Updated environmental data for {len([b for b in buildings_db if b['id'] in buildings_environmental])} buildings")




async def load_buildings_from_supabase():
    """Load buildings from Supabase database"""
    global buildings_db
    if not supabase:
        print("Warning: Supabase not configured. Using in-memory storage.")
        # Fallback to default buildings if Supabase is not configured
        buildings_db = [
            {
                "id": "tower-a",
                "name": "Tower A",
                "address": "123 Main Street",
                "floors": "15",
                "sqft": "450,000",
            },
            {
                "id": "tower-b",
                "name": "Tower B",
                "address": "125 Main Street",
                "floors": "12",
                "sqft": "380,000",
            },
            {
                "id": "building-c",
                "name": "Building C",
                "address": "200 Oak Avenue",
                "floors": "8",
                "sqft": "220,000",
            },
        ]
        return
    
    try:
        # Use 'Building' table (capital B) to match Supabase
        response = supabase.table("Building").select("*").execute()
        raw_data = response.data if response.data else []
        
        # Transform Supabase data to match frontend format
        buildings_db = []
        for item in raw_data:
            building = {
                "id": item.get("id", ""),
                "name": item.get("name", ""),
                "address": item.get("address", ""),
                # Use floors/sqft if they exist, otherwise use default values
                "floors": str(item.get("floors", "0")) if item.get("floors") is not None else "0",
                "sqft": str(item.get("sqft", "0")) if item.get("sqft") is not None else "0",
            }
            buildings_db.append(building)
        
        print(f"Loaded {len(buildings_db)} buildings from Supabase Building table")
    except Exception as e:
        print(f"Error loading buildings from Supabase: {e}")
        buildings_db = []

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Load buildings from Supabase
    await load_buildings_from_supabase()
    
    # Initialize environmental data for all buildings
    for building in buildings_db:
        building_id = building["id"]
        # Initialize with default realistic values based on building name/ID
        if building_id == "tower-a" or building.get("name", "").lower() == "tower a":
            buildings_environmental[building_id] = {
                "temperature": 72.0,
                "humidity": 45.0,
                "airQuality": "Good",
                "energyUsage": 2450.0,
                "utilization": 85,
                "hvacHealth": 89,
                "electricalHealth": 100,
                "waterHealth": 100,
                "fireSafetyHealth": 100
            }
        elif building_id == "tower-b" or building.get("name", "").lower() == "tower b":
            buildings_environmental[building_id] = {
                "temperature": 71.5,
                "humidity": 48.0,
                "airQuality": "Excellent",
                "energyUsage": 1850.0,
                "utilization": 72,
                "hvacHealth": 95,
                "electricalHealth": 98,
                "waterHealth": 99,
                "fireSafetyHealth": 100
            }
        elif building_id == "building-c" or building.get("name", "").lower() == "building c":
            buildings_environmental[building_id] = {
                "temperature": 70.0,
                "humidity": 42.0,
                "airQuality": "Excellent",
                "energyUsage": 1320.0,
                "utilization": 68,
                "hvacHealth": 90,
                "electricalHealth": 99,
                "waterHealth": 98,
                "fireSafetyHealth": 100
            }
        else:
            # Default for new buildings
            buildings_environmental[building_id] = {
                "temperature": 70.0,
                "humidity": 50.0,
                "airQuality": "Good",
                "energyUsage": 1200.0,
                "utilization": 65,
                "hvacHealth": 92,
                "electricalHealth": 100,
                "waterHealth": 100,
                "fireSafetyHealth": 100
            }
    
    print(f"Initialized environmental data for {len(buildings_environmental)} buildings")
    
    # Start background task
    task = asyncio.create_task(update_environmental_data())
    print("Environmental data randomizer started (updates every 20 seconds)")
    yield
    # Shutdown
    task.cancel()
    print("Environmental data randomizer stopped")

app = FastAPI(lifespan=lifespan)

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Health check endpoint
@app.get("/")
async def health_check():
    return {"status": "ok", "service": "BuildingOS API", "version": "1.0"}

class BuildingCreate(BaseModel):
    name: str
    address: str
    city: str
    state: str
    country: str 
    floors: str
    sqft: str
    companyId: Optional[str] = None

class Building(BaseModel):
    id: str
    name: str
    address: str
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    floors: str
    sqft: str
    occupancy: int = 0
    # Health fields matching frontend/DB
    hvacHealth: Optional[int] = 100
    electricalHealth: Optional[int] = 100
    waterHealth: Optional[int] = 100
    fireSafetyHealth: Optional[int] = 100

@app.get("/_api/buildings")
async def get_buildings(companyId: Optional[str] = None) -> List[Building]:
    # Company filter is required for multi-tenant isolation
    if not companyId:
        return []
    
    if supabase:
        try:
            # Fetch buildings filtered by company, ordered by name
            response = supabase.table("Building").select("*").eq("companyId", companyId).order("name", desc=False).execute()
            raw_data = response.data if response.data else []
            
            # Map Supabase data to our schema
            live_buildings = []
            for item in raw_data:
                building = {
                    "id": item.get("id", ""),
                    "name": item.get("name", ""),
                    "address": item.get("address", ""),
                    "city": item.get("city", ""),
                    "state": item.get("country", ""), # Assuming 'country' column holds state/region or we need to check schema
                    "country": "USA", # Default if not in DB, or fetch if available
                    # Use floors/sqft if they exist, otherwise use default values
                    "floors": str(item.get("floors", "0")),
                    "sqft": str(item.get("sqft") or item.get("sqFt", "0")),
                    "occupancy": int(item.get("utilization", 0)) if item.get("utilization") is not None else 85,
                    "temperature": float(item.get("temperature", 72.0)),
                    "humidity": float(item.get("humidity", 45.0)),
                    "energyUsage": int(item.get("energyUsage", 2000)),
                    "airQuality": item.get("airQuality", "Good"),
                    "hvacHealth": int(item.get("hvacHealth", 98)),
                    "electricalHealth": int(item.get("electricalHealth", 100)),
                    "waterHealth": int(item.get("waterHealth", 100)),
                    "fireSafetyHealth": int(item.get("fireSafetyHealth", 100))
                }
                live_buildings.append(building)
            
            # Update local cache
            global buildings_db
            buildings_db = live_buildings
            return live_buildings
        except Exception as e:
            print(f"Error fetching from Supabase: {e}")
            # Fallback to local cache if Supabase fetch fails
            return buildings_db
            
    return buildings_db

@app.post("/_api/buildings")
async def create_building(building: BuildingCreate) -> Building:
    new_building = {
        "id": str(uuid.uuid4()),
        "name": building.name,
        "address": building.address,
        "city": building.city,
        "state": building.state,
        "country": building.country,
        "floors": building.floors,
        "sqft": building.sqft,
        "hvacHealth": 100,
        "electricalHealth": 100,
        "waterHealth": 100,
        "fireSafetyHealth": 100
    }
    
    # Sync to Supabase - use 'Building' table (capital B) to match Supabase
    if supabase:
        try:
            # Clean inputs with regex (find digits)
            import re
            sqft_str = str(new_building["sqft"])
            floors_str = str(new_building["floors"])
            
            # Extract digits only, ignoring everything else
            sqft_digits = "".join(re.findall(r'\d+', sqft_str))
            floors_digits = "".join(re.findall(r'\d+', floors_str))
            
            sqft_int = int(sqft_digits) if sqft_digits else 0
            floors_int = int(floors_digits) if floors_digits else 0
            
            supabase_building = {
                "id": new_building["id"],
                "name": new_building["name"],
                "address": new_building["address"],
                "city": new_building["city"],
                "country": new_building["state"], # Mapping State to DB 'country' column based on observation
                "floors": floors_int,
                "sqft": sqft_int, # Lowercase column
                "sqFt": sqft_int, # CamelCase column (for legacy compatibility)

                "companyId": building.companyId or "default", # Use company from request
                # Initialize health columns
                "hvacHealth": 100,
                "electricalHealth": 100,
                "waterHealth": 100,
                "fireSafetyHealth": 100
            }
            response = supabase.table("Building").insert(supabase_building).execute()
            print(f"Created building in Supabase: {new_building['id']} - {new_building['name']}")
            
            # Create Storage Folder
            try:
                # Create a placeholder file to "create" the folder
                # Bucket: test-building-files
                res = supabase.storage.from_("test-building-files").upload(
                    path=f"{new_building['name']}/.keep",
                    file=b"",
                    file_options={"content-type": "text/plain"}
                )
                print(f"Created storage folder for {new_building['name']}")
            except Exception as se:
                print(f"Error creating storage folder: {se}")
                
        except Exception as e:
            error_str = str(e).lower()
            # If columns don't exist, log helpful message
            if "column" in error_str and "does not exist" in error_str:
                print(f"Error: Building table missing columns. Details: {e}")
            
            print(f"Error creating building in Supabase: {e}")
            # Raise exception so frontend knows it failed
            raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
            # Continue anyway - we'll still add to local storage
    
    # Add to local storage
    buildings_db.append(new_building)
    
    # Initialize environmental data for new building
    buildings_environmental[new_building["id"]] = {
        "temperature": 70.0,
        "humidity": 45.0,
        "airQuality": "Good",
        "energyUsage": 2000.0,
        "hvacHealth": 100,
        "electricalHealth": 100,
        "waterHealth": 100,
        "fireSafetyHealth": 100
    }
    return new_building

@app.delete("/_api/storage/file")
async def delete_storage_file(path: str, bucket: str = "test-building-files"):
    """
    Instead of deleting, move file to 'recently-deleted' (trash) folder in Supabase.
    This satisfies the requirement for a recovery location not visible in UI.
    """
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase client not initialized")
    
    try:
        # Move to recently-deleted prefix
        # Check if target file already exists (to avoid conflict)
        # We append a timestamp for uniqueness
        import time
        timestamp = int(time.time())
        target_path = f"recently-deleted/{timestamp}_{path.replace('/', '_')}"
        
        # Move command in Supabase Storage
        res = supabase.storage.from_(bucket).move(path, target_path)
        print(f"Moved {path} to recently-deleted as {target_path}")
        return {"message": "File moved to recently-deleted folder", "details": res}
    except Exception as e:
        print(f"Error moving file to trash: {e}")
        # Fallback: if move fails (maybe file missing), try to report success if already gone
        raise HTTPException(status_code=500, detail=str(e))

class MoveRequest(BaseModel):
    from_path: str
    to_path: str
    bucket: str = "test-building-files"

@app.post("/_api/storage/move")
async def move_storage_file(req: MoveRequest):
    """Move a file within storage using service role key — bypasses RLS."""
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase client not initialized")
    try:
        supabase.storage.from_(req.bucket).move(req.from_path, req.to_path)
        print(f"[Move] {req.from_path} -> {req.to_path}")
        return {"status": "success", "from": req.from_path, "to": req.to_path}
    except Exception as e:
        print(f"[Move] FAILED: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/_api/storage/upload")
async def upload_storage_file(
    path: str,
    bucket: str = "test-building-files",
    file: UploadFile = File(...),
    building_id: str = "",
    company_id: str = "",
    folder: str = "",
    uploaded_by: str = ""
):
    """Universal file upload endpoint using service role key — bypasses all RLS.
    Returns: { status, path, publicUrl, db_id } where db_id is the File table record ID."""
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase client not initialized")
    try:
        contents = await file.read()
        filename = file.filename or path.split('/')[-1]

        # Upload to storage with upsert=True (overwrite if same path somehow)
        supabase.storage.from_(bucket).upload(
            path,
            contents,
            file_options={"content-type": file.content_type or "application/octet-stream", "upsert": "true"}
        )
        print(f"[Upload] Stored at {path}")

        # Build public URL
        public_url = f"{SUPABASE_URL}/storage/v1/object/public/{bucket}/{path}"

        # Create/upsert File DB record
        db_id = None
        if building_id and company_id:
            file_ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else 'unknown'
            folder_parts = path.split('/')
            folder_type = folder_parts[1].upper() if len(folder_parts) > 1 else (folder.upper() or 'OTHER')
            db_payload = {
                "id": str(__import__('uuid').uuid4()),
                "companyId": company_id,
                "buildingId": building_id,
                "folder": folder_type,
                "filename": filename,
                "fileType": file_ext,
                "s3Key": path,
                "uploadedBy": uploaded_by or None,
            }
            try:
                db_res = supabase.table("File").insert(db_payload).execute()
                if db_res.data:
                    db_id = db_res.data[0].get("id")
                    print(f"[Upload] DB record created: {db_id}")
            except Exception as db_err:
                print(f"[Upload] DB record creation failed (non-critical): {db_err}")

        return {"status": "success", "path": path, "publicUrl": public_url, "db_id": db_id, "filename": filename}
    except Exception as e:
        print(f"[Upload] FAILED: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/_api/storage/replace")
async def replace_storage_file(
    old_path: str,
    bucket: str = "test-building-files",
    file: UploadFile = File(...),
    folder_path: str = "",
    db_file_id: str = ""
):
    """Replace a file: trash the old one, upload new file under its OWN filename,
    and update the File DB record's s3Key + filename.
    Uses service role key — bypasses all RLS."""
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase client not initialized")
    try:
        import time
        new_filename = file.filename or old_path.split('/')[-1]

        # Determine the folder from old_path or explicit folder_path param
        if not folder_path:
            parts = old_path.rsplit('/', 1)
            folder_path = parts[0] if len(parts) == 2 else ''

        new_path = f"{folder_path}/{new_filename}" if folder_path else new_filename

        # Step 1: move old file to trash (non-fatal)
        trash_path = None
        try:
            timestamp = int(time.time())
            trash_path = f"recently-deleted/{timestamp}_{old_path.replace('/', '_')}"
            supabase.storage.from_(bucket).move(old_path, trash_path)
            print(f"[Replace] Trashed {old_path} -> {trash_path}")
        except Exception as move_err:
            print(f"[Replace] Trash move failed (may not exist, continuing): {move_err}")

        # Step 2: upload new file under its own name
        contents = await file.read()
        supabase.storage.from_(bucket).upload(
            new_path,
            contents,
            file_options={"content-type": file.content_type or "application/octet-stream", "upsert": "true"}
        )
        print(f"[Replace] Uploaded new file at {new_path}")

        # Get the new S3 ID so we can migrate notes
        new_s3_id = None
        try:
            # We don't use search because it's not supported by all SDKs properly, just list the folder
            folder_res = supabase.storage.from_(bucket).list(folder_path, {"limit": 1000})
            for x in folder_res:
                if x["name"] == new_filename:
                    new_s3_id = x["id"]
                    break
        except Exception as e:
            print(f"[Replace] Could not fetch new S3 ID: {e}")

        # Migrate existing notes from old S3 ID to new S3 ID
        if db_file_id and new_s3_id:
            try:
                supabase.table("DocumentUpdates").update({"document_id": new_s3_id}).eq("document_id", db_file_id).execute()
                print(f"[Replace] Migrated notes from {db_file_id} to {new_s3_id}")
            except Exception as e:
                print(f"[Replace] Note migration failed: {e}")

        # Step 3: update File DB record if we have the id
        if db_file_id:
            try:
                file_ext = new_filename.rsplit('.', 1)[-1].lower() if '.' in new_filename else 'unknown'
                supabase.table("File").update({
                    "filename": new_filename,
                    "s3Key": new_path,
                    "fileType": file_ext,
                }).eq("id", db_file_id).execute()
                print(f"[Replace] Updated DB record {db_file_id}: s3Key={new_path}")
            except Exception as db_err:
                print(f"[Replace] DB update failed (non-critical): {db_err}")

        public_url = f"{SUPABASE_URL}/storage/v1/object/public/{bucket}/{new_path}"
        return {
            "status": "success", 
            "old_path": old_path, 
            "new_path": new_path, 
            "publicUrl": public_url, 
            "new_filename": new_filename,
            "new_s3_id": new_s3_id,
            "trash_path": trash_path
        }
    except Exception as e:
        print(f"[Replace] FAILED: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/_api/files/list")
async def list_db_files(building_name: str, type: str, subtype: str):
    """List files from the database for a specific building/folder path.
    This ensures we have persistent IDs for notes and versioning."""
    if not supabase:
        raise HTTPException(status_code=500, detail="Database not configured")
    try:
        # Construct the prefix path used in s3Key
        path_prefix = f"{building_name}/{type}/{subtype}/"
        # Search by s3Key starting with the prefix
        result = supabase.table("File").select("*")\
            .ilike("s3Key", f"{path_prefix}%")\
            .execute()
        return result.data or []
    except Exception as e:
        print(f"Error listing DB files: {e}")
        return []

class FileRecord(BaseModel):
    buildingId: str
    folder: str
    filename: str
    fileType: str
    s3Key: str
    companyId: str
    uploadedBy: Optional[str] = None

@app.post("/_api/files/record")
async def create_file_record(file: FileRecord):
    """Create a metadata record for an uploaded file"""
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase client not initialized")
    try:
        # Map to Documents table schema (Phase 1.3)
        # We try Documents first, fallback to File if needed
        doc_data = {
            "building_id": file.buildingId,
            "category": file.folder.upper(),
            "filename": file.filename,
            "s3_key": file.s3Key,
            "company_id": file.companyId,
            "uploaded_by": file.uploadedBy
        }
        try:
            res = supabase.table("Documents").insert(doc_data).execute()
            return res.data[0]
        except Exception:
            # Fallback for older schema if migration hasn't fully run
            print("Notice: Falling back to 'File' table")
            res = supabase.table("File").insert(file.dict()).execute()
            return res.data[0]
    except Exception as e:
        print(f"Error creating file record: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/_api/files/list")
async def list_files(buildingId: str, folder: str, companyId: str):
    """List files for a specific building and folder"""
    if not supabase:
        return []
    try:
        # Try Documents table first
        try:
            res = supabase.table("Documents").select("*")\
                .eq("building_id", buildingId)\
                .eq("category", folder.upper())\
                .eq("company_id", companyId)\
                .execute()
            
            if res.data:
                # Map snake_case to camelCase for frontend compatibility
                return [
                    {
                        "id": d["id"],
                        "buildingId": d["building_id"],
                        "folder": d["category"],
                        "filename": d["filename"],
                        "s3Key": d["s3_key"],
                        "createdAt": d["created_at"],
                        "fileType": d["filename"].split('.')[-1] if '.' in d["filename"] else "file"
                    } for d in res.data
                ]
            
            # If no data in Documents, try File table
            raise Exception("No data in Documents")
        except Exception:
            res = supabase.table("File").select("*")\
                .eq("buildingId", buildingId)\
                .eq("folder", folder.upper())\
                .eq("companyId", companyId)\
                .execute()
            return res.data or []
    except Exception as e:
        print(f"Error listing files: {e}")
        return []

@app.get("/_api/files/counts")
async def get_file_counts(companyId: str):
    """Get file counts per building and category for a company"""
    if not supabase:
        return {}
    try:
        # Try Documents table
        try:
            res = supabase.table("Documents").select("building_id, category").eq("company_id", companyId).execute()
            if not res.data: raise Exception("No data")
            
            counts = {}
            for item in res.data:
                bid = item['building_id']
                folder = item['category'].capitalize()
                if bid not in counts:
                    counts[bid] = {"total": 0, "categories": {}}
                counts[bid]["total"] += 1
                counts[bid]["categories"][folder] = counts[bid]["categories"].get(folder, 0) + 1
            return counts
        except Exception:
            res = supabase.table("File").select("buildingId, folder").eq("companyId", companyId).execute()
            counts = {}
            for item in (res.data or []):
                bid = item['buildingId']
                folder = item['folder'].capitalize()
                if bid not in counts:
                    counts[bid] = {"total": 0, "categories": {}}
                counts[bid]["total"] += 1
                counts[bid]["categories"][folder] = counts[bid]["categories"].get(folder, 0) + 1
            return counts
    except Exception as e:
        print(f"Error getting file counts: {e}")
        return {}

@app.post("/_api/files/url")
async def get_file_url(request: dict):
    """Get a URL for viewing a file - returns the public URL since bucket is public"""
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase not configured")
    
    path = request.get("path")
    bucket = request.get("bucket", "test-building-files")
    
    if not path:
        raise HTTPException(status_code=400, detail="File path is required")
        
    try:
        # Try signed URL first
        try:
            res = supabase.storage.from_(bucket).create_signed_url(path, 3600)
            if isinstance(res, dict) and res.get('signedURL'):
                return {"url": res['signedURL']}
            elif isinstance(res, str) and res.startswith('http'):
                return {"url": res}
        except Exception as sign_err:
            print(f"Signed URL failed, using proxy: {sign_err}")
        
        # Fallback: return a proxy URL that serves the file through our backend
        return {"url": f"/_api/files/download/{bucket}/{path}"}
    except Exception as e:
        print(f"Error generating file URL: {e}")
        raise HTTPException(status_code=500, detail=str(e))

from fastapi.responses import Response

@app.get("/_api/files/download/{bucket}/{path:path}")
async def proxy_file_download(bucket: str, path: str):
    """Proxy file download through the backend (bypasses storage RLS)"""
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase not configured")
    
    try:
        file_bytes = supabase.storage.from_(bucket).download(path)
        
        # Determine content type
        import mimetypes
        content_type, _ = mimetypes.guess_type(path)
        if not content_type:
            content_type = "application/octet-stream"
        
        filename = path.split("/")[-1]
        
        return Response(
            content=file_bytes,
            media_type=content_type,
            headers={
                "Content-Disposition": f'inline; filename="{filename}"',
                "Cache-Control": "public, max-age=3600"
            }
        )
    except Exception as e:
        print(f"Error downloading file: {e}")
        raise HTTPException(status_code=404, detail=f"File not found: {str(e)}")

@app.delete("/_api/storage/record")
async def delete_file_record_endpoint(s3Key: str, filename: Optional[str] = None):
    """Delete a file record from the 'Documents' or 'File' table and remove embeddings"""
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase client not initialized")
    
    try:
        # Try Documents table first
        try:
            supabase.table("Documents").delete().eq("s3_key", s3Key).execute()
        except Exception:
            pass

        # Try File table
        res = supabase.table("File").delete().eq("s3Key", s3Key).execute()
        
        # If no record removed from File and filename provided, try filename
        if not res.data and filename:
            print(f"Retrying delete by filename: {filename}")
            res = supabase.table("File").delete().eq("filename", filename).execute()
        
        # Also remove embeddings for this file
        try:
            supabase.table(VECTORS_TABLE).delete().filter("metadata->>file_path", "eq", s3Key).execute()
        except Exception:
            pass
            
        return {"message": "Record deleted successfully"}
    except Exception as e:
        print(f"Error deleting file record: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/_api/buildings/{building_id}")
async def delete_building(building_id: str):
    global buildings_db
    building_index = next((i for i, b in enumerate(buildings_db) if b["id"] == building_id), None)
    if building_index is None:
        raise HTTPException(status_code=404, detail="Building not found")
    
    # Get building name for storage folder cleanup
    building_name = buildings_db[building_index].get("name", "") if building_index is not None else ""
    
    # Sync to Supabase - use 'Building' table (capital B) to match Supabase
    if supabase:
        try:
            # If we don't have the name from local cache, fetch from Supabase
            if not building_name:
                building_record = supabase.table("Building").select("name").eq("id", building_id).execute()
                if building_record.data:
                    building_name = building_record.data[0].get("name", "")

            # 1. Delete file records from File table
            supabase.table("File").delete().eq("buildingId", building_id).execute()
            print(f"Deleted file records from DB for building {building_id}")

            # 2. Delete the building record from Building table
            supabase.table("Building").delete().eq("id", building_id).execute()
            print(f"Deleted building from Supabase: {building_id}")
            
            # 3. Move all storage files to recently-deleted (safety net), then clean the building folder
            #    NEVER touch recently-deleted or all-uploads — they are backup/safety nets
            if building_name:
                try:
                    import time
                    timestamp = int(time.time())
                    bucket = supabase.storage.from_("test-building-files")
                    
                    def move_folder_to_trash(folder_path):
                        try:
                            items = bucket.list(folder_path)
                            if not items:
                                return
                            for item in items:
                                item_path = f"{folder_path}/{item['name']}"
                                if item.get('id') is None:
                                    move_folder_to_trash(item_path)
                                else:
                                    safe_name = item_path.replace('/', '_')
                                    target = f"recently-deleted/{timestamp}_{safe_name}"
                                    try:
                                        bucket.move(item_path, target)
                                        print(f"Moved {item_path} -> {target}")
                                    except Exception as move_err:
                                        print(f"Warning: Failed to move {item_path}: {move_err}")
                        except Exception as list_err:
                            print(f"Warning listing {folder_path}: {list_err}")
                    
                    move_folder_to_trash(building_name)
                    print(f"Moved all files from {building_name}/ to recently-deleted/")
                except Exception as storage_err:
                    print(f"Warning: Storage cleanup failed for {building_name}: {storage_err}")
            
        except Exception as e:
            print(f"Error syncing deletion to Supabase: {e}")
            # We continue removing from local storage below
    
    # Remove from local storage
    buildings_db.pop(building_index)
    
    # Clean up environmental data
    if building_id in buildings_environmental:
        del buildings_environmental[building_id]
    return {"message": "Building deleted successfully"}

@app.get("/_api/buildings/{building_id}/environmental")
async def get_environmental_data(building_id: str):
    """Get environmental data for a specific building"""
    # Check if building exists in our database
    building_exists = any(b.get("id") == building_id for b in buildings_db)
    
    if not building_exists:
        # Debug: log available building IDs
        available_ids = [b.get("id") for b in buildings_db[:3]]
        print(f"Environmental data requested for {building_id}, but not found. Available IDs: {available_ids}")
        raise HTTPException(status_code=404, detail=f"Building not found: {building_id}")
    
    # If data not found, initialize it on the fly (fallback)
    if building_id not in buildings_environmental:
        # Initialize with default values
        buildings_environmental[building_id] = {
            "temperature": 70.0,
            "humidity": 45.0,
            "airQuality": "Good",
            "energyUsage": 2000.0,
        }
        print(f"Initialized environmental data for building: {building_id}")
    
    env = buildings_environmental[building_id]
    return {
        "temperature": f"{env['temperature']:.1f}°F",
        "humidity": f"{env['humidity']:.0f}%",
        "airQuality": env["airQuality"],
        "energyUsage": f"{env['energyUsage']:,.0f} kWh",
    }

# ===== AI ASSISTANT ENDPOINTS WITH RAG =====
from openai import OpenAI
import pdfplumber
import httpx
import io

# Initialize OpenAI (used for both chat and embeddings)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
openai_client = None
if OPENAI_API_KEY:
    openai_client = OpenAI(api_key=OPENAI_API_KEY)
    print("OpenAI configured successfully (chat: gpt-4o-mini, embeddings: text-embedding-3-small)")
else:
    print("Warning: OPENAI_API_KEY not set - AI features will be limited")

# Supabase Vectors bucket (for reference)
VECTORS_BUCKET = "building-embeddings"

# ===== RAG HELPER FUNCTIONS =====

def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """Extract text content from a PDF file"""
    text = ""
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
    except Exception as e:
        print(f"Error extracting PDF text: {e}")
    return text.strip()

def chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> List[str]:
    """Split text into overlapping chunks for embedding"""
    if not text:
        return []
    
    chunks = []
    words = text.split()
    
    if len(words) <= chunk_size:
        return [text]
    
    start = 0
    while start < len(words):
        end = start + chunk_size
        chunk = " ".join(words[start:end])
        chunks.append(chunk)
        start = end - overlap  # Overlap for context continuity
    
    return chunks

def generate_embeddings(texts: List[str]) -> List[List[float]]:
    """Generate embeddings for a list of texts using OpenAI text-embedding-3-small"""
    if not openai_client or not texts:
        return []
    
    try:
        embeddings = []
        # Process in batches of 100 (OpenAI limit)
        batch_size = 100
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            response = openai_client.embeddings.create(
                model="text-embedding-3-small",
                input=batch
            )
            for item in response.data:
                embeddings.append(item.embedding)
        return embeddings
    except Exception as e:
        print(f"Error generating embeddings: {e}")
        return []

async def store_document_embeddings(building_name: str, file_path: str, chunks: List[str], embeddings: List[List[float]]):
    """Store document chunks and embeddings in Supabase Vectors table"""
    if not supabase or not chunks or not embeddings:
        print(f"store_document_embeddings: missing data - supabase={bool(supabase)}, chunks={len(chunks)}, embeddings={len(embeddings)}")
        return False
    
    try:
        # First, delete any existing embeddings for this file
        try:
            # Use filter for JSONB column
            supabase.table(VECTORS_TABLE).delete().filter("metadata->>file_path", "eq", file_path).execute()
            print(f"Cleared old embeddings for {file_path}")
        except Exception as del_err:
            print(f"Delete old embeddings (non-critical): {del_err}")
        
        # Insert new chunks with embeddings using Supabase Vectors format
        # Note: Don't send 'id' - Supabase Vectors tables auto-generate bigint ids
        records = []
        for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
            records.append({
                "content": chunk,
                "embedding": embedding,
                "metadata": {
                    "building_name": building_name,
                    "file_path": file_path,
                    "chunk_index": i
                }
            })
        
        print(f"Attempting to insert {len(records)} chunks into {VECTORS_TABLE}...")
        
        # Insert in batches of 50
        batch_size = 50
        for i in range(0, len(records), batch_size):
            batch = records[i:i + batch_size]
            result = supabase.table(VECTORS_TABLE).insert(batch).execute()
            print(f"Batch {i//batch_size + 1}: inserted {len(batch)} records")
        
        print(f"Stored {len(records)} chunks for {file_path} in {VECTORS_TABLE}")
        return True
    except Exception as e:
        print(f"Error storing embeddings in {VECTORS_TABLE}: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return False

async def query_similar_chunks(query: str, building_names: List[str], top_k: int = 10) -> List[dict]:
    """Query similar document chunks using OpenAI embeddings and Supabase Vectors"""
    if not openai_client or not supabase or not query:
        return []
    
    try:
        # Generate embedding for the query using OpenAI
        response = openai_client.embeddings.create(
            model="text-embedding-3-small",
            input=[query]
        )
        query_embedding = response.data[0].embedding
        
        # Use Supabase RPC for vector similarity search
        result = supabase.rpc(
            "match_documents",
            {
                "query_embedding": query_embedding,
                "match_count": top_k,
                "filter_buildings": building_names
            }
        ).execute()
        
        return result.data if result.data else []
    except Exception as e:
        print(f"Error querying similar chunks (RPC): {e}")
        # Fallback: try direct query without vector search
        try:
            if building_names:
                # Query the vectors table directly
                result = supabase.table(VECTORS_TABLE)\
                    .select("content, metadata")\
                    .limit(top_k * len(building_names))\
                    .execute()
                
                # Filter by building names from metadata
                filtered = []
                for item in (result.data or []):
                    metadata = item.get("metadata", {})
                    if metadata.get("building_name") in building_names:
                        filtered.append({
                            "chunk_text": item.get("content", ""),
                            "file_path": metadata.get("file_path", "Unknown"),
                            "building_name": metadata.get("building_name", "Unknown")
                        })
                return filtered[:top_k]
        except Exception as fallback_error:
            print(f"Fallback query error: {fallback_error}")
        return []

async def process_pdf_for_rag(building_name: str, file_path: str) -> dict:
    """Download PDF from Supabase storage, extract text, embed, and store"""
    if not supabase:
        return {"success": False, "error": "Supabase not configured"}
    
    try:
        # Download the PDF from storage
        response = supabase.storage.from_("test-building-files").download(file_path)
        
        if not response:
            return {"success": False, "error": "Could not download file"}
        
        # Extract text from PDF
        text = extract_text_from_pdf(response)
        
        if not text:
            return {"success": False, "error": "Could not extract text from PDF"}
        
        # Chunk the text
        chunks = chunk_text(text)
        
        if not chunks:
            return {"success": False, "error": "No text chunks generated"}
        
        # Generate embeddings
        embeddings = generate_embeddings(chunks)
        
        if not embeddings:
            return {"success": False, "error": "Could not generate embeddings"}
        
        # Store in database
        print(f"Storing {len(chunks)} chunks with {len(embeddings)} embeddings for {file_path}")
        success = await store_document_embeddings(building_name, file_path, chunks, embeddings)
        
        if success:
            return {
                "success": True,
                "chunks_processed": len(chunks),
                "file_path": file_path,
                "building_name": building_name
            }
        else:
            return {"success": False, "error": f"Failed to store embeddings for {file_path} ({len(chunks)} chunks). Check server logs."}
            
    except Exception as e:
        print(f"Error processing PDF for RAG: {e}")
        return {"success": False, "error": str(e)}

class ChatRequest(BaseModel):
    message: str
    conversationHistory: Optional[List[dict]] = []
    selectedBuildings: Optional[List[str]] = []

class ChatResponse(BaseModel):
    response: str
    success: bool = True

@app.get("/_api/ai/buildings")
async def get_ai_buildings():
    """Get buildings with their file structure for AI context"""
    if not supabase:
        return {"buildings": [], "details": {}}
    
    try:
        # Get buildings
        response = supabase.table("Building").select("id, name").execute()
        buildings_list = [{"id": b["id"], "name": b["name"]} for b in (response.data or [])]
        
        # Get file details per building from storage
        details = {}
        for building in buildings_list:
            building_name = building["name"]
            try:
                # List files in the building folder from storage bucket
                files_response = supabase.storage.from_("test-building-files").list(building_name)
                
                categories = {}
                for item in files_response:
                    # In Supabase storage, virtual folders have id=null, files have id=uuid
                    if item.get("id") is None:  # It's a virtual folder
                        folder_name = item["name"]
                        # List files in this category folder
                        category_files = supabase.storage.from_("test-building-files").list(f"{building_name}/{folder_name}")
                        file_list = [{"name": f["name"], "path": f"{building_name}/{folder_name}/{f['name']}"} 
                                     for f in category_files if f.get("id") is not None]
                        categories[folder_name] = {
                            "fileCount": len(file_list),
                            "files": file_list
                        }
                
                details[building_name] = {
                    "name": building_name,
                    "categories": categories
                }
            except Exception as e:
                print(f"Error listing files for {building_name}: {e}")
                details[building_name] = {"name": building_name, "categories": {}}
        
        return {"buildings": buildings_list, "details": details}
    except Exception as e:
        print(f"Error fetching AI buildings: {e}")
        return {"buildings": [], "details": {}}

@app.post("/_api/ai/vectors/init")
async def init_vectors():
    """Initialize vector storage - creates document_chunks table if needed"""
    if not supabase:
        return {"success": False, "message": "Supabase not configured"}
    
    # The table should be created via SQL in Supabase dashboard
    return {"success": True, "message": "Vector storage ready. Ensure document_chunks table exists in Supabase."}

@app.get("/_api/ai/vectors/test-insert")
async def test_vector_insert():
    """Test inserting a single record into the vectors table to diagnose issues"""
    if not supabase or not openai_client:
        return {"success": False, "error": "Supabase or OpenAI not configured"}
    
    try:
        # Generate a test embedding
        response = openai_client.embeddings.create(
            model="text-embedding-3-small",
            input=["This is a test document chunk for diagnosing vector insert issues."]
        )
        test_embedding = response.data[0].embedding
        
        # Try inserting
        test_record = {
            "content": "Test chunk - safe to delete",
            "embedding": test_embedding,
            "metadata": {
                "building_name": "TEST",
                "file_path": "test/test.pdf",
                "chunk_index": 0
            }
        }
        
        result = supabase.table(VECTORS_TABLE).insert(test_record).execute()
        
        # Clean up test record
        if result.data:
            record_id = result.data[0].get("id")
            if record_id:
                supabase.table(VECTORS_TABLE).delete().eq("id", record_id).execute()
        
        return {"success": True, "message": "Insert and cleanup successful", "data_sample": str(result.data)[:200] if result.data else "no data"}
    except Exception as e:
        import traceback
        return {"success": False, "error": str(e), "traceback": traceback.format_exc()}

@app.post("/_api/ai/vectors/sync/{building_name}")
async def sync_vectors(building_name: str, forceReindex: bool = False, includeRetry: bool = False):
    """Sync/index building documents - process PDFs and create embeddings"""
    if not supabase:
        return {"success": False, "summary": {"error": "Supabase not configured"}}
    
    if not openai_client:
        return {"success": False, "summary": {"error": "OpenAI not configured - set OPENAI_API_KEY"}}
    
    processed = 0
    errors = []
    
    try:
        # List all files in the building folder
        files_response = supabase.storage.from_("test-building-files").list(building_name)
        
        for item in files_response:
            # In Supabase storage, virtual folders have id=null, files have id=uuid
            if item.get("id") is None:  # It's a virtual folder (category)
                folder_name = item["name"]
                # List files in this category folder
                category_files = supabase.storage.from_("test-building-files").list(f"{building_name}/{folder_name}")
                
                for f in category_files:
                    if f.get("id") is not None and f["name"].lower().endswith(".pdf"):
                        file_path = f"{building_name}/{folder_name}/{f['name']}"
                        result = await process_pdf_for_rag(building_name, file_path)
                        
                        if result["success"]:
                            processed += result.get("chunks_processed", 0)
                        else:
                            errors.append(f"{file_path}: {result.get('error', 'Unknown error')}")
        
        return {
            "success": len(errors) == 0,
            "summary": {
                "processed": processed,
                "building": building_name,
                "errors": errors if errors else None
            }
        }
    except Exception as e:
        print(f"Error syncing vectors for {building_name}: {e}")
        return {
            "success": False,
            "summary": {
                "processed": 0,
                "building": building_name,
                "error": str(e)
            }
        }

@app.post("/_api/ai/vectors/process-file")
async def process_single_file(building_name: str, file_path: str):
    """Process a single PDF file and create embeddings"""
    if not openai_client:
        raise HTTPException(status_code=500, detail="OpenAI not configured - set OPENAI_API_KEY")
    
    result = await process_pdf_for_rag(building_name, file_path)
    
    if not result["success"]:
        raise HTTPException(status_code=500, detail=result.get("error", "Processing failed"))
    
    return result

@app.post("/_api/ai/chat")
async def ai_chat(request: ChatRequest):
    """Chat with AI about buildings using OpenAI with RAG (Retrieval Augmented Generation)"""
    if not openai_client:
        return ChatResponse(
            response="AI features are not configured. Please set OPENAI_API_KEY environment variable.",
            success=False
        )
    
    try:
        # Get building context from database
        building_context = ""
        document_content = ""
        
        if request.selectedBuildings and supabase:
            for building_name in request.selectedBuildings:
                # Get building info from database
                building_query = supabase.table("Building").select("*").eq("name", building_name).execute()
                if building_query.data:
                    b = building_query.data[0]
                    building_context += f"\n\nBuilding: {b.get('name', 'Unknown')}\n"
                    building_context += f"- Address: {b.get('address', 'N/A')}\n"
                    building_context += f"- Floors: {b.get('floors', 'N/A')}\n"
                    building_context += f"- Square Feet: {b.get('sqFt', b.get('sqft', 'N/A'))}\n"
            
            # ===== RAG: Query relevant document chunks using OpenAI embeddings =====
            if openai_client:
                try:
                    # Generate embedding for the user's question using OpenAI
                    embed_response = openai_client.embeddings.create(
                        model="text-embedding-3-small",
                        input=[request.message]
                    )
                    query_embedding = embed_response.data[0].embedding
                    
                    # Query similar chunks from Supabase Vectors table
                    # First try with vector similarity (requires match_documents function in Supabase)
                    try:
                        result = supabase.rpc(
                            "match_documents",
                            {
                                "query_embedding": query_embedding,
                                "match_count": 10,
                                "filter_buildings": request.selectedBuildings
                            }
                        ).execute()
                        
                        if result.data:
                            document_content = "\n\n===== RELEVANT DOCUMENT EXCERPTS =====\n"
                            for chunk in result.data:
                                document_content += f"\n[From: {chunk.get('file_path', 'Unknown')}]\n"
                                document_content += f"{chunk.get('chunk_text', chunk.get('content', ''))}\n"
                                document_content += "---\n"
                    except Exception as rpc_error:
                        print(f"RPC match_documents not available: {rpc_error}")
                        # Fallback: Get chunks from Supabase Vectors table (no vector search)
                        for building_name in request.selectedBuildings:
                            try:
                                # Query the vectors table
                                chunks_result = supabase.table(VECTORS_TABLE)\
                                    .select("content, metadata")\
                                    .limit(50)\
                                    .execute()
                                
                                if chunks_result.data:
                                    building_chunks = [c for c in chunks_result.data 
                                                       if c.get("metadata", {}).get("building_name") == building_name]
                                    if building_chunks:
                                        document_content += f"\n\n===== DOCUMENT EXCERPTS FROM {building_name} =====\n"
                                        for chunk in building_chunks[:10]:
                                            file_path = chunk.get("metadata", {}).get("file_path", "Unknown")
                                            document_content += f"\n[From: {file_path}]\n"
                                            document_content += f"{chunk.get('content', '')}\n"
                                            document_content += "---\n"
                            except Exception as table_error:
                                print(f"Table query error for {building_name}: {table_error}")
                except Exception as rag_error:
                    print(f"RAG query error: {rag_error}")
                    document_content = "\n\n[Note: Could not retrieve document content. Please ensure documents have been indexed.]\n"
        
        # Build system prompt with RAG content
        system_prompt = f"""Role: You are a professional AI assistant specializing in building information and compliance. Your role is to provide accurate, actionable insights based strictly on the files associated with the user's selected buildings.

Core Instructions:
- Source Integrity: Answer questions ONLY using the information provided in the document excerpts below. These excerpts are from the actual uploaded PDF files.
- Uncertainty & Accuracy: If the provided documentation does not contain a clear answer, or if you are unsure, explicitly acknowledge the limitation. Do not provide speculative or "confidently wrong" answers.
- Contextual Focus: Only address topics related to the building documentation provided. Politely decline to answer unrelated general knowledge questions.
- Citation: When referencing information, cite the source document file path.

Selected Building Metadata:
{building_context if building_context else "No specific buildings selected."}

{document_content if document_content else "No indexed document content found. Documents may need to be synced/indexed first."}

Remember: Base your answers ONLY on the document excerpts provided above. If the information is not in these excerpts, say so clearly."""

        # Build messages for OpenAI
        messages = [{"role": "system", "content": system_prompt}]
        
        # Add conversation history
        for msg in request.conversationHistory:
            role = msg.get("role", "user")
            if role == "model":
                role = "assistant"
            messages.append({"role": role, "content": msg.get("content", "")})
        
        # Add current user message
        messages.append({"role": "user", "content": request.message})
        
        # Call OpenAI API with gpt-4o-mini
        completion = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            temperature=0.3,  # Lower temperature for more factual responses
            max_tokens=2048,
        )
        
        response_text = completion.choices[0].message.content
        
        return ChatResponse(response=response_text, success=True)
        
    except Exception as e:
        print(f"AI Chat Error: {e}")
        return ChatResponse(
            response=f"I encountered an error: {str(e)}. Please try again.",
            success=False
        )

# ===== AUTH & USER MANAGEMENT ENDPOINTS =====
import firebase_admin
from firebase_admin import credentials, auth as firebase_auth
import resend

# Initialize Firebase Admin
firebase_initialized = False
try:
    # Try loading from environment variable first (for Railway/production)
    firebase_cred_json = os.getenv("FIREBASE_SERVICE_ACCOUNT")
    if firebase_cred_json:
        import json
        cred_dict = json.loads(firebase_cred_json)
        cred = credentials.Certificate(cred_dict)
        firebase_admin.initialize_app(cred)
        firebase_initialized = True
        print("Firebase Admin SDK initialized from environment variable")
    else:
        # Fall back to file (for local development)
        cred = credentials.Certificate("firebase-service-account.json")
        firebase_admin.initialize_app(cred)
        firebase_initialized = True
        print("Firebase Admin SDK initialized from file")
except Exception as e:
    print(f"Warning: Firebase Admin initialization failed: {e}")

# Initialize Resend
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
if RESEND_API_KEY:
    resend.api_key = RESEND_API_KEY
    print("Resend configured")
else:
    print("Warning: RESEND_API_KEY not set")

COMPANY_ACCESS_CODE = "BuildingOS2026"

# Helper to verify Firebase token
async def verify_firebase_token(authorization: str = None):
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization.replace("Bearer ", "")
    try:
        decoded = firebase_auth.verify_id_token(token)
        return decoded
    except Exception as e:
        print(f"Token verification failed: {e}")
        return None

# Pydantic models for auth
class CheckUserRequest(BaseModel):
    email: str

class CreateInviteRequest(BaseModel):
    name: str
    email: str
    role: str
    company: str

class RegisterAdminRequest(BaseModel):
    name: str
    email: str
    role: str
    company: str
    accessCode: str

class AccessCodeRequest(BaseModel):
    accessCode: str

class CreateUserRequest(BaseModel):
    name: str
    email: str
    role: str
    company: Optional[str] = None
    firebase_uid: Optional[str] = None

class UpdateUserRequest(BaseModel):
    name: Optional[str] = None
    role: Optional[str] = None
    company: Optional[str] = None

# ===== AUTH ROUTES =====
from fastapi import Header

@app.post("/api/auth/check-user")
async def check_user(request: CheckUserRequest):
    """Check if a user exists by email"""
    if not supabase:
        raise HTTPException(status_code=500, detail="Database not configured")
    
    try:
        result = supabase.table("users").select("id, name, email, role, company").eq("email", request.email).execute()
        
        if not result.data:
            return {"exists": False}
        
        user = result.data[0]
        return {
            "exists": True,
            "user": {
                "name": user.get("name"),
                "role": user.get("role"),
                "company": user.get("company")
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ===== USER ROUTES =====
@app.get("/api/users/me")
async def get_current_user(authorization: str = Header(None)):
    """Get current user's profile"""
    if not supabase:
        raise HTTPException(status_code=500, detail="Database not configured")
    
    user_info = await verify_firebase_token(authorization)
    if not user_info:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    firebase_uid = user_info.get("uid")
    firebase_email = user_info.get("email")
    firebase_name = user_info.get("name", user_info.get("email", "User").split("@")[0])
    
    try:
        # Try by firebase_uid first
        result = supabase.table("users").select("*").eq("firebase_uid", firebase_uid).execute()
        
        if not result.data and firebase_email:
            # Fallback to email
            result = supabase.table("users").select("*").eq("email", firebase_email).execute()
            
            if result.data:
                # Sync firebase_uid
                supabase.table("users").update({
                    "firebase_uid": firebase_uid,
                    "updated_at": datetime.datetime.now().isoformat()
                }).eq("id", result.data[0]["id"]).execute()
        
        if not result.data:
            # User must go through invite flow to get a profile
            raise HTTPException(status_code=404, detail="User profile not found. Please complete the invite process.")
        
        return {"user": result.data[0]}
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error in get_current_user: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/users")
async def get_all_users(company: str, authorization: str = Header(None)):
    """Get all users from a company"""
    if not supabase:
        raise HTTPException(status_code=500, detail="Database not configured")
    
    user_info = await verify_firebase_token(authorization)
    if not user_info:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    try:
        result = supabase.table("users").select("*").eq("company", company).order("created_at", desc=True).execute()
        return {"users": result.data or []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/users/email/{email}")
async def get_user_by_email(email: str, authorization: str = Header(None)):
    """Get user by email"""
    if not supabase:
        raise HTTPException(status_code=500, detail="Database not configured")
    
    user_info = await verify_firebase_token(authorization)
    if not user_info:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    try:
        result = supabase.table("users").select("*").eq("email", email).execute()
        return {"user": result.data[0] if result.data else None}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/users/{user_id}")
async def get_user_by_id(user_id: str, authorization: str = Header(None)):
    """Get user by ID"""
    if not supabase:
        raise HTTPException(status_code=500, detail="Database not configured")
    
    user_info = await verify_firebase_token(authorization)
    if not user_info:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    try:
        result = supabase.table("users").select("*").eq("id", user_id).execute()
        if not result.data:
            raise HTTPException(status_code=404, detail="User not found")
        return {"user": result.data[0]}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/users")
async def create_user(request: CreateUserRequest, authorization: str = Header(None)):
    """Create a new user"""
    if not supabase:
        raise HTTPException(status_code=500, detail="Database not configured")
    
    user_info = await verify_firebase_token(authorization)
    if not user_info:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    try:
        now = datetime.datetime.now().isoformat()
        result = supabase.table("users").insert({
            "name": request.name,
            "email": request.email,
            "role": request.role,
            "company": request.company,
            "firebase_uid": request.firebase_uid,
            "created_by": user_info.get("uid"),
            "created_at": now,
            "updated_at": now
        }).execute()
        
        return {"user": result.data[0], "message": "User created successfully"}
    except Exception as e:
        if "duplicate" in str(e).lower() or "23505" in str(e):
            raise HTTPException(status_code=409, detail="User with this email already exists")
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/users/{user_id}")
async def update_user(user_id: str, request: UpdateUserRequest, authorization: str = Header(None)):
    """Update a user"""
    if not supabase:
        raise HTTPException(status_code=500, detail="Database not configured")
    
    user_info = await verify_firebase_token(authorization)
    if not user_info:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    try:
        # Get current user to check admin status
        current_user = supabase.table("users").select("role").eq("firebase_uid", user_info.get("uid")).execute()
        is_admin = current_user.data and current_user.data[0].get("role") == "Admin"
        
        updates = {"updated_at": datetime.datetime.now().isoformat()}
        if request.name:
            updates["name"] = request.name
        if is_admin:
            if request.role:
                updates["role"] = request.role
            if request.company is not None:
                updates["company"] = request.company
        
        result = supabase.table("users").update(updates).eq("id", user_id).execute()
        return {"user": result.data[0] if result.data else None, "message": "User updated successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/users/{user_id}")
async def delete_user(user_id: str, authorization: str = Header(None)):
    """Delete a user"""
    if not supabase:
        raise HTTPException(status_code=500, detail="Database not configured")
    
    user_info = await verify_firebase_token(authorization)
    if not user_info:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    try:
        # Verify admin
        admin_user = supabase.table("users").select("*").eq("firebase_uid", user_info.get("uid")).execute()
        if not admin_user.data or admin_user.data[0].get("role") != "Admin":
            raise HTTPException(status_code=403, detail="Only admins can delete users")
        
        # Get target user
        target_user = supabase.table("users").select("*").eq("id", user_id).execute()
        if not target_user.data:
            raise HTTPException(status_code=404, detail="User not found")
        
        target = target_user.data[0]
        admin = admin_user.data[0]
        
        if target.get("company") != admin.get("company"):
            raise HTTPException(status_code=403, detail="You can only delete users from your own company")
        
        if target.get("id") == admin.get("id"):
            raise HTTPException(status_code=400, detail="You cannot delete yourself")
        
        # Delete from Supabase
        supabase.table("users").delete().eq("id", user_id).execute()
        
        # Delete from Firebase if has firebase_uid
        if firebase_initialized and target.get("firebase_uid"):
            try:
                firebase_auth.delete_user(target["firebase_uid"])
            except Exception as fe:
                print(f"Firebase delete failed: {fe}")
        
        return {"message": "User access revoked successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ===== INVITE ROUTES =====
@app.post("/api/invites")
async def create_invite(request: CreateInviteRequest, authorization: str = Header(None)):
    """Create and send an invite"""
    if not supabase:
        raise HTTPException(status_code=500, detail="Database not configured")
    
    user_info = await verify_firebase_token(authorization)
    if not user_info:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    try:
        invite_token = str(uuid.uuid4())
        expires_at = (datetime.datetime.now() + datetime.timedelta(days=7)).isoformat()
        
        result = supabase.table("invites").insert({
            "token": invite_token,
            "name": request.name,
            "email": request.email,
            "role": request.role,
            "company": request.company,
            "created_by": user_info.get("uid"),
            "expires_at": expires_at,
            "used": False
        }).execute()
        
        frontend_url = os.getenv("FRONTEND_URL", "http://localhost:5173")
        invite_url = f"{frontend_url}/invite/{invite_token}"
        
        email_sent = False
        if RESEND_API_KEY:
            try:
                resend.Emails.send({
                    "from": os.getenv("RESEND_FROM", "BuildingOS <onboarding@resend.dev>"),
                    "to": request.email,
                    "subject": f"{request.name}, you have been added to BuildingOS",
                    "html": f"<p>Hello {request.name},</p><p>You have been invited to join BuildingOS as a <strong>{request.role}</strong>.</p><p><a href='{invite_url}'>Click here to access your account</a></p>"
                })
                email_sent = True
            except Exception as email_err:
                print(f"Email error: {email_err}")
        
        return {
            "invite": result.data[0] if result.data else None,
            "inviteUrl": invite_url,
            "message": "Invite sent successfully" if email_sent else "Invite created but email failed",
            "emailSent": email_sent
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/invites")
async def get_invites(authorization: str = Header(None)):
    """Get all invites"""
    if not supabase:
        raise HTTPException(status_code=500, detail="Database not configured")
    
    user_info = await verify_firebase_token(authorization)
    if not user_info:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    try:
        result = supabase.table("invites").select("*").eq("created_by", user_info.get("uid")).order("created_at", desc=True).execute()
        return {"invites": result.data or []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/invites/verify/{token}")
async def verify_invite(token: str):
    """Verify an invite token (public endpoint)"""
    if not supabase:
        raise HTTPException(status_code=500, detail="Database not configured")
    
    try:
        # Find invite
        result = supabase.table("invites").select("*").eq("token", token).eq("used", False).execute()
        
        invite = None
        if result.data:
            invite = result.data[0]
        else:
            # Check for recently used invite (within 10 min)
            recent = supabase.table("invites").select("*").eq("token", token).eq("used", True).execute()
            if recent.data:
                used_at = recent.data[0].get("used_at")
                if used_at:
                    used_time = datetime.datetime.fromisoformat(used_at.replace("Z", "+00:00"))
                    if (datetime.datetime.now(datetime.timezone.utc) - used_time).total_seconds() < 600:
                        invite = recent.data[0]
        
        if not invite:
            raise HTTPException(status_code=404, detail="Invalid or expired invite link")
        
        # Check expiry
        expires_at = datetime.datetime.fromisoformat(invite["expires_at"].replace("Z", "+00:00"))
        if datetime.datetime.now(datetime.timezone.utc) > expires_at:
            raise HTTPException(status_code=410, detail="This invite link has expired")
        
        # Mark as used
        supabase.table("invites").update({
            "used": True,
            "used_at": datetime.datetime.now().isoformat()
        }).eq("token", token).execute()
        
        # Create or get user
        user_result = supabase.table("users").select("*").eq("email", invite["email"]).execute()
        user = user_result.data[0] if user_result.data else None
        
        if not user:
            now = datetime.datetime.now().isoformat()
            new_user = supabase.table("users").insert({
                "name": invite["name"],
                "email": invite["email"],
                "role": invite["role"],
                "company": invite["company"],
                "created_by": invite["created_by"],
                "created_at": now,
                "updated_at": now
            }).execute()
            user = new_user.data[0] if new_user.data else None
        else:
            # Update user with invite info
            supabase.table("users").update({
                "role": invite["role"],
                "company": invite["company"],
                "name": invite["name"],
                "updated_at": datetime.datetime.now().isoformat()
            }).eq("id", user["id"]).execute()
        
        # Handle Firebase user
        custom_token = None
        firebase_uid = None
        if firebase_initialized:
            try:
                try:
                    fb_user = firebase_auth.get_user_by_email(invite["email"])
                except:
                    fb_user = firebase_auth.create_user(
                        email=invite["email"],
                        display_name=invite["name"],
                        email_verified=True
                    )
                
                firebase_uid = fb_user.uid
                
                # Update user with firebase_uid
                if user and not user.get("firebase_uid"):
                    supabase.table("users").update({"firebase_uid": firebase_uid}).eq("id", user["id"]).execute()
                    user["firebase_uid"] = firebase_uid
                
                # Create custom token
                custom_token = firebase_auth.create_custom_token(fb_user.uid, {
                    "role": invite["role"],
                    "company": invite["company"],
                    "isAdmin": invite["role"] == "Admin"
                }).decode("utf-8") if isinstance(firebase_auth.create_custom_token(fb_user.uid), bytes) else firebase_auth.create_custom_token(fb_user.uid)
            except Exception as fb_err:
                print(f"Firebase error: {fb_err}")
        
        return {
            "user": {
                **(user or {}),
                "role": invite["role"],
                "company": invite["company"],
                "name": invite["name"]
            },
            "customToken": custom_token,
            "firebaseUid": firebase_uid,
            "message": "Welcome to BuildingOS!"
        }
    except HTTPException:
        raise
    except Exception as e:
        print(f"Verify invite error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/invites/session/{token}")
async def verify_session(token: str):
    """Verify session token"""
    if not supabase:
        raise HTTPException(status_code=500, detail="Database not configured")
    
    try:
        result = supabase.table("sessions").select("*, users(*)").eq("token", token).execute()
        
        if not result.data:
            raise HTTPException(status_code=401, detail="Invalid session")
        
        session = result.data[0]
        expires_at = datetime.datetime.fromisoformat(session["expires_at"].replace("Z", "+00:00"))
        if datetime.datetime.now(datetime.timezone.utc) > expires_at:
            raise HTTPException(status_code=401, detail="Session expired")
        
        return {"user": session.get("users")}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/invites/{invite_id}/resend")
async def resend_invite(invite_id: str, authorization: str = Header(None)):
    """Resend invite email"""
    if not supabase or not RESEND_API_KEY:
        raise HTTPException(status_code=500, detail="Service not configured")
    
    user_info = await verify_firebase_token(authorization)
    if not user_info:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    try:
        result = supabase.table("invites").select("*").eq("id", invite_id).execute()
        if not result.data:
            raise HTTPException(status_code=404, detail="Invite not found")
        
        invite = result.data[0]
        if invite.get("used"):
            raise HTTPException(status_code=400, detail="This invite has already been used")
        
        frontend_url = os.getenv("FRONTEND_URL", "http://localhost:5173")
        invite_url = f"{frontend_url}/invite/{invite['token']}"
        
        resend.Emails.send({
            "from": os.getenv("RESEND_FROM", "BuildingOS <onboarding@resend.dev>"),
            "to": invite["email"],
            "subject": "Reminder: You are invited to BuildingOS",
            "html": f"<p>Hello {invite['name']},</p><p>This is a reminder that you have been invited to join BuildingOS as a <strong>{invite['role']}</strong>.</p><p><a href='{invite_url}'>Click here to access your account</a></p>"
        })
        
        return {"message": "Invite resent successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/invites/verify-access-code")
async def verify_access_code(request: AccessCodeRequest):
    """Verify company access code"""
    if request.accessCode != COMPANY_ACCESS_CODE:
        raise HTTPException(status_code=403, detail="Invalid access code")
    return {"valid": True, "message": "Access code verified"}

@app.post("/api/invites/register-admin")
async def register_admin(request: RegisterAdminRequest):
    """Register a new admin (public endpoint protected by access code)"""
    if not supabase:
        raise HTTPException(status_code=500, detail="Database not configured")
    
    if request.accessCode != COMPANY_ACCESS_CODE:
        raise HTTPException(status_code=403, detail="Invalid access code")
    
    try:
        invite_token = str(uuid.uuid4())
        expires_at = (datetime.datetime.now() + datetime.timedelta(days=7)).isoformat()
        
        result = supabase.table("invites").insert({
            "token": invite_token,
            "name": request.name,
            "email": request.email,
            "role": request.role,
            "company": request.company,
            "created_by": "system",
            "expires_at": expires_at,
            "used": False
        }).execute()
        
        frontend_url = os.getenv("FRONTEND_URL", "http://localhost:5173")
        invite_url = f"{frontend_url}/invite/{invite_token}"
        
        email_sent = False
        if RESEND_API_KEY:
            try:
                resend.Emails.send({
                    "from": os.getenv("RESEND_FROM", "BuildingOS <onboarding@resend.dev>"),
                    "to": request.email,
                    "subject": f"{request.name}, your BuildingOS account is ready",
                    "html": f"<p>Hello {request.name},</p><p>You have been set up as a <strong>{request.role}</strong> for <strong>{request.company}</strong> on BuildingOS.</p><p><a href='{invite_url}'>Click here to access your account</a></p>"
                })
                email_sent = True
            except Exception as email_err:
                print(f"Email error: {email_err}")
        
        return {
            "inviteUrl": invite_url,
            "message": "Admin account created! A login link has been sent." if email_sent else "Admin created but email failed.",
            "emailSent": email_sent
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class DocumentUpdate(BaseModel):
    document_id: str
    user_id: Optional[str] = None
    type: str
    metadata: Optional[dict] = {}
    s3_version_id: Optional[str] = None

# NOTE: count route MUST come before /{document_id} to avoid FastAPI route conflict
@app.get("/_api/files/updates/count")
async def get_updates_count(companyId: str):
    """Get total update count for a company's documents"""
    if not supabase: return {"count": 0}
    try:
        res = supabase.table("DocumentUpdates").select("id").execute()
        return {"count": len(res.data) if res.data else 0}
    except Exception as e:
        print(f"Error fetching updates count: {e}")
        return {"count": 0}

# NOTE: 'recent' route MUST come before /{document_id} to avoid route conflict
@app.get("/_api/files/updates/recent")
async def get_recent_updates_batch(ids: str = ""):
    """Get the most recent update for each of a comma-separated list of document IDs.
    Returns a dict mapping document_id -> most recent update row."""
    if not supabase or not ids.strip():
        return {}
    try:
        id_list = [i.strip() for i in ids.split(',') if i.strip()]
        if not id_list:
            return {}
        # Fetch all updates for these IDs in one query, ordered newest first
        res = supabase.table("DocumentUpdates")\
            .select("*")\
            .in_("document_id", id_list)\
            .order("created_at", desc=True)\
            .execute()
        # Keep only the first (most recent) update per document_id
        seen: dict = {}
        for row in (res.data or []):
            doc_id = row.get("document_id")
            if doc_id and doc_id not in seen:
                seen[doc_id] = row
        return seen
    except Exception as e:
        print(f"Error fetching recent updates batch: {e}")
        return {}


@app.post("/_api/files/update")
async def create_document_update(update: DocumentUpdate):
    """Create an update record for a document"""
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase not configured")
    try:
        # Only insert columns that exist in the table
        payload = {
            "document_id": update.document_id,
            "user_id": update.user_id,
            "type": update.type,
            "metadata": update.metadata or {},
        }
        if update.s3_version_id:
            payload["s3_version_id"] = update.s3_version_id

        print(f"[DocumentUpdates] Inserting: doc={update.document_id} type={update.type}")
        res = supabase.table("DocumentUpdates").insert(payload).execute()
        print(f"[DocumentUpdates] Insert result: {res.data}")
        if res.data:
            return res.data[0]
        raise HTTPException(status_code=500, detail="Insert returned no data")
    except HTTPException:
        raise
    except Exception as e:
        print(f"[DocumentUpdates] Insert FAILED: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/_api/files/updates/{document_id}")
async def get_document_updates(document_id: str):
    """Get update history for a document"""
    if not supabase:
        return []
    try:
        res = supabase.table("DocumentUpdates")\
            .select("*")\
            .eq("document_id", document_id)\
            .order("created_at", desc=True)\
            .execute()
        return res.data or []
    except Exception as e:
        print(f"Error fetching document updates: {e}")
        return []

@app.delete("/_api/files/updates/{update_id}")
async def delete_document_update(update_id: str):
    """Delete a single document update (note) by its id"""
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase not configured")
    try:
        supabase.table("DocumentUpdates").delete().eq("id", update_id).execute()
        return {"status": "deleted"}
    except Exception as e:
        print(f"Error deleting document update: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
