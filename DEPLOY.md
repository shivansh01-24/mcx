# Deployment Guide (Production Deployments)

This guide documents the procedures for deploying the MCX Market Data Platform to production environments.

---

## 1. Deploying to Railway (One-Click)

The platform is fully optimized for [Railway](https://railway.app/) and includes native configurations for fast setups.

### Steps:
1. **Fork or Push** the repository to your GitHub account.
2. Log in to [Railway](https://railway.app/).
3. Click **New Project** -> **Deploy from GitHub repo**.
4. Choose the repository containing the codebase.
5. Railway will automatically parse the `Dockerfile` and `railway.json` configurations.
6. Under the **Variables** tab, ensure you add:
   * `PORT`: `8000` (or leave blank; Railway binds the port automatically).
7. Go to **Settings** and click **Generate Domain** to get a public HTTPS URL.
8. The server will boot, run database migrations, connect to the auto-provisioned Postgres/Redis databases, and start accepting connections.

---

## 2. Deploying to a Standard Linux VPS (Ubuntu/Debian)

For deployment on standard cloud instances (AWS EC2, DigitalOcean, Linode):

### Steps:
1. **Clone the repository**:
   ```bash
   git clone <repo-url> /opt/mcx-platform
   cd /opt/mcx-platform
   ```

2. **Configure Environment Variables**:
   Create a `.env` file in `/opt/mcx-platform` to secure production secrets:
   ```env
   MCX_DATABASE__URL=postgresql://postgres:secure_db_pass@db:5432/mcx_platform
   MCX_PLATFORM__LOG_LEVEL=INFO
   ```

3. **Deploy using Docker Compose**:
   ```bash
   docker compose up -d --build
   ```

4. **Verify Container Health**:
   ```bash
   docker compose ps
   ```

5. **SSL Certificate Configuration (HTTPS)**:
   By default, Nginx binds to port 80. To secure it using Let's Encrypt:
   * Install Certbot on the host:
     ```bash
     sudo apt-get install certbot python3-certbot-nginx
     ```
   * Generate SSL certificates:
     ```bash
     sudo certbot --nginx -d yourdomain.com
     ```
