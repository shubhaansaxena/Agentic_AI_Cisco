# Cloud Run Deployment Guide

## What You're Deploying

A FastAPI app with 3 endpoints:
- `POST /evaluate` — Score a single product family (triggers the full agent loop)
- `POST /evaluate-batch` — Score multiple product families
- `GET /families` — List available product families with volume data

Behind the scenes, the agent uses Gemini function calling to:
1. Query BigQuery for cost/volume data
2. Run deterministic scoring
3. Write recommendations or escalations back to BigQuery
4. Generate a plain-English explanation


## Project Structure

```
rma-agent/
  Dockerfile
  requirements.txt
  src/
    main.py          ← FastAPI app (Cloud Run entry point)
    agent.py         ← Gemini function calling loop
    config.py        ← GCP project settings
    tools/
      __init__.py
      bq_tools.py    ← The 4 tool functions (BigQuery queries + scoring)
```


## Before You Deploy: Update config.py

Open `src/config.py` and update:
```python
PROJECT_ID = "your-actual-gcp-project-id"   # e.g., "cisco-repair-replace"
LOCATION = "us-central1"                      # or your preferred region
```


## Step 1: Enable Required APIs

Go to https://console.cloud.google.com/apis/library and enable:
- **Cloud Run API**
- **Artifact Registry API**
- **Cloud Build API**
- **Vertex AI API**
- **BigQuery API** (probably already enabled)

You can also do this from the search bar — search each API name and click Enable.


## Step 2: Create an Artifact Registry Repository

This is where your Docker container image will live.

1. Go to https://console.cloud.google.com/artifacts
2. Click **+ CREATE REPOSITORY**
3. Settings:
   - Name: `rma-agent`
   - Format: `Docker`
   - Region: `us-central1` (match your config.py LOCATION)
4. Click **CREATE**


## Step 3: Upload Your Code to Cloud Shell

You have two options:

### Option A: Cloud Shell (easiest)

1. Go to https://console.cloud.google.com
2. Click the **Cloud Shell** icon (terminal icon, top right)
3. In Cloud Shell, create the project directory:

```bash
mkdir -p rma-agent/src/tools
```

4. Use the Cloud Shell Editor (pencil icon) to create each file,
   or upload the files using the **Upload** button (three dots menu → Upload).

5. Make sure the file structure matches:
```
rma-agent/
  Dockerfile
  requirements.txt
  src/
    main.py
    agent.py
    config.py
    tools/
      __init__.py
      bq_tools.py
```

### Option B: Clone from a Git repo

If you push this code to GitHub first:
```bash
git clone https://github.com/your-username/rma-agent.git
cd rma-agent
```


## Step 4: Build and Deploy to Cloud Run

In Cloud Shell, navigate to your project folder:

```bash
cd rma-agent
```

### One-command deploy (builds and deploys in one step):

```bash
gcloud run deploy rma-agent \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --memory 1Gi \
  --timeout 120
```

This will:
- Build your Docker container using Cloud Build
- Push it to Artifact Registry
- Deploy it to Cloud Run

When prompted:
- If asked about enabling APIs, say **yes**
- If asked about creating an Artifact Registry repo, say **yes**
- If asked about the platform, choose **Cloud Run (fully managed)**

**Wait 2-3 minutes.** When it finishes, you'll see a URL like:
```
https://rma-agent-xxxxx-uc.a.run.app
```


## Step 5: Set Up Permissions

The Cloud Run service needs permission to access BigQuery and Vertex AI.

1. Go to https://console.cloud.google.com/run
2. Click on your **rma-agent** service
3. Note the **Service account** shown (usually the default compute service account)
4. Go to https://console.cloud.google.com/iam-admin/iam
5. Find that service account and click the **pencil** icon to edit
6. Add these roles:
   - **BigQuery Data Editor** (to read curated tables and write to output tables)
   - **BigQuery Job User** (to run queries)
   - **Vertex AI User** (to call Gemini)
7. Click **Save**


## Step 6: Test It

### Test health check:
Open your browser and go to:
```
https://rma-agent-xxxxx-uc.a.run.app/health
```
You should see: `{"status": "healthy"}`

### Test listing families:
```
https://rma-agent-xxxxx-uc.a.run.app/families
```

### Test evaluating a product family:

You can use the Cloud Shell terminal:

```bash
curl -X POST https://rma-agent-xxxxx-uc.a.run.app/evaluate \
  -H "Content-Type: application/json" \
  -d '{"product_family": "UCSB"}'
```

Or use the interactive API docs at:
```
https://rma-agent-xxxxx-uc.a.run.app/docs
```
FastAPI auto-generates a Swagger UI where you can test all endpoints
from your browser. This is great for demos.


## Step 7: Check the Results in BigQuery

After calling /evaluate, go back to BigQuery console and run:

```sql
SELECT * FROM rma_out.recommendations
ORDER BY created_at DESC
LIMIT 10;
```

You should see a new row with:
- The product family you evaluated
- A decision (REPAIR/REPLACE/REVIEW)
- A band (GREEN/YELLOW/RED)
- The net benefit amount
- A Gemini-generated explanation

Also check escalations:
```sql
SELECT * FROM rma_out.missing_data_escalations
ORDER BY created_at DESC
LIMIT 10;
```


## Troubleshooting

### "Permission denied" errors
→ Check Step 5 — the service account needs BigQuery and Vertex AI roles.

### "Module not found" errors in logs
→ Go to Cloud Run → your service → Logs tab to see container logs.
   Usually means a file is missing or an import path is wrong.

### Gemini not responding / timeout
→ Make sure Vertex AI API is enabled and LOCATION in config.py
   matches your Cloud Run region.

### "No data found for product family"
→ Make sure the product family name matches exactly
   (case-sensitive). Check with: SELECT DISTINCT product_family FROM rma_curated.unit_costs;

### View logs
Go to Cloud Run → rma-agent → **Logs** tab. This shows all print()
statements from the agent, including tool calls.
