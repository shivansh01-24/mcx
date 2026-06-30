# Installation Guide (Local Development & Testing)

This guide documents the procedures for installing and running the MCX Market Data Platform locally on your machine.

---

## 1. Prerequisites

Ensure you have the following software installed:
* **Python 3.10+** (if running outside Docker)
* **Docker & Docker Compose** (recommended for all deployments)
* **Git**

---

## 2. Option A: One-Command Startup (Recommended)

The easiest way to install and run the platform is using Docker.

### Windows
Double-click or run from command prompt:
```cmd
start.bat
```

### Linux/macOS
Run from terminal:
```bash
chmod +x start.sh
./start.sh
```

These scripts verify that Docker is installed and running, spin up the Redis and PostgreSQL service containers, build the FastAPI server, run database migrations, warm the caches, and expose the platform endpoints.

---

## 3. Option B: Manual Bare-Metal Installation

If you prefer to run the Python server directly on your host machine:

### 1. Run Databases (Docker recommended)
If you don't have Postgres and Redis installed locally, you can start only the databases using Docker:
```bash
docker compose up -d db redis
```

### 2. Create Python Virtual Environment
```bash
python -m venv venv
source venv/bin/activate  # On Windows use: venv\Scripts\activate
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Configure Environment
Create a `.env` file in the root directory (or override via OS environment variables):
```env
MCX_DATABASE__URL=postgresql://postgres:postgres@localhost:5432/mcx_platform
MCX_REDIS__HOST=localhost
MCX_REDIS__PORT=6379
```

### 5. Run Database Migrations
On startup, the FastAPI app automatically runs migrations via SQLAlchemy schema bindings.

### 6. Start the API Server
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

---

## 4. Verification

After installation completes, navigate to:
* **Landing Page**: `http://localhost:8000/`
* **Dashboard**: `http://localhost:8000/dashboard`
* **API Swagger Docs**: `http://localhost:8000/docs`
