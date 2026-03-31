# BuildingOS Backend

Real AI document analysis using Claude claude-sonnet-4-6. Replaces the simulated data in the
BuildingOS MVP demo with actual intelligence extracted from your building documents.

## What it does

Receives a building document (PDF, PNG, JPG), sends it to Claude with a structured
extraction prompt, and returns a JSON object with 10 categories of building intelligence:

- Asset Inventory
- Capital Planning
- Energy & Envelope
- Code Compliance
- Space Intelligence
- Structural & Safety
- Commissioning Deviations
- Vendor & Procurement
- Sustainability & Certifications
- Insurance & Valuation

## Setup (5 minutes)

### 1. Prerequisites
- Python 3.10 or higher
- An Anthropic API key — get one at https://console.anthropic.com

### 2. Install dependencies

```bash
cd buildingos-backend
pip install -r requirements.txt
```

### 3. Configure your API key

```bash
cp .env.example .env
```

Open `.env` and replace the placeholder with your real API key:
```
ANTHROPIC_API_KEY=sk-ant-your-actual-key-here
```

### 4. Start the server

```bash
uvicorn main:app --reload --port 8000
```

You should see:
```
INFO:     Uvicorn running on http://127.0.0.1:8000
```

### 5. Open the frontend

Open `buildingos-mvp.html` in Chrome. The status bar at the top of
the Upload section will turn green:

> ● Real AI active — Claude claude-sonnet-4-6 will analyze your documents

Upload any PDF or building document and you'll receive real extracted intelligence.

---

## API Endpoints

### `GET /health`
Returns backend status and whether the API key is configured.

```json
{"status": "ok", "api_key_configured": true}
```

### `POST /analyze`
Upload a file for analysis.

**Request:** `multipart/form-data` with a `file` field.

**Supported formats:**
- PDF (`application/pdf`) — best for specs, manuals, multi-page documents
- PNG / JPG / WEBP — floor plan scans, drawings

**Response:** JSON with all 10 intelligence categories, plus `_meta` with
token usage and file info.

---

## Deploying to Production

### Step 1: Deploy Backend to Railway

1. Go to [railway.app](https://railway.app) and sign in with GitHub
2. Click "New Project" → "Deploy from GitHub repo"
3. Select `BuildingOS26/MVP-mvp`
4. Railway will auto-detect Python and start deploying
5. Go to **Variables** tab and add these environment variables:

```
ANTHROPIC_API_KEY=sk-ant-your-key-here
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your-service-role-key
SUPABASE_STORAGE_BUCKET=documents
EMBEDDING_API_URL=https://api.openai.com/v1/embeddings
EMBEDDING_API_KEY=sk-your-openai-key
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIMENSIONS=1536
```

6. Once deployed, copy your Railway URL (e.g., `https://mvp-mvp-production.up.railway.app`)

### Step 2: Update Frontend

In `buildingos-mvp.html`, find this line near the top:
```javascript
const PRODUCTION_BACKEND_URL = ''; // <-- SET THIS AFTER DEPLOYING BACKEND
```

Change it to your Railway URL:
```javascript
const PRODUCTION_BACKEND_URL = 'https://mvp-mvp-production.up.railway.app';
```

### Step 3: Deploy Frontend to Vercel

**Option A: Drag & Drop**
1. Go to [vercel.com](https://vercel.com)
2. Drag `buildingos-mvp.html` into the dashboard
3. Done!

**Option B: From GitHub**
1. Push the updated `buildingos-mvp.html` to GitHub
2. Connect Vercel to your repo
3. Deploy

### Alternative: Railway Only

You can also serve both frontend and backend from Railway:
1. The HTML file is served at the root `/` endpoint
2. Just use your Railway URL directly

---

## Supported document types and accuracy

| Document Type | Best Categories Extracted |
|---|---|
| Equipment specs / O&M manuals | Assets, Capital, Predictive Maintenance, Vendors |
| Architectural drawings / floor plans | Space, Structural, Assets |
| MEP drawings | Assets, Energy, Commissioning |
| Specifications (CSI format) | Compliance, Vendors, Energy, Commissioning |
| Maintenance reports | Predictive Maintenance, Capital Planning |
| General building reports | All categories (variable depth) |

**DWG files:** CAD files (.dwg, .dxf) are not directly supported by the Claude API.
Convert them to PDF first using Autodesk software, BricsCAD, or a free online converter.

---

## Cost estimate

claude-sonnet-4-6 pricing (as of 2025):
- Input: $3 per million tokens
- Output: $15 per million tokens

A typical building document analysis uses 3,000–10,000 tokens total.
Cost per document: roughly **$0.01–$0.05**.

Analyzing 100 documents: **~$1–$5**.
