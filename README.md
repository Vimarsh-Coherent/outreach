# Coherent Outreach (standalone)

Multi-channel cold-outreach platform — runs locally. Email via SMTP/IMAP, LinkedIn via Chrome extension bridge, sentiment-tagged dashboard, AI follow-up drafting powered by VectorVault.

See [docs/PLAN.md](docs/PLAN.md) for the full architecture, schema, and milestone plan.

## Local quickstart

```powershell
# 1. Create DB (after switching Postgres to trust auth — see PLAN §0)
.\scripts\init_db.ps1

# 2. Backend
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
copy .env.example .env       # then fill in ANTHROPIC_API_KEY etc.
alembic upgrade head
uvicorn outreach.main:app --reload --host 127.0.0.1 --port 8000

# 3. Frontend
cd ..\frontend
npm install
npm run dev                   # http://localhost:5173

# 4. Chrome extension
# chrome://extensions → Developer mode → Load unpacked → select extension/
```
