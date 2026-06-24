# ServerGuardian: GitHub Actions Monitoring Setup Guide

This guide explains how to migrate ServerGuardian to run its periodic monitoring checks via **GitHub Actions** and deploy the visualization dashboard for free on Render (or other platforms).


## Architecture Diagram

```mermaid
graph TD
    subgraph GitHub Cloud
        GHA[GitHub Actions Scheduler <br> Every 5 mins] -->|runs| MR[monitor_runner.py]
    end
    
    subgraph Data Layer
        MR -->|writes results| DB[(MongoDB Atlas)]
    end

    subgraph Hosting Provider e.g. Render
        DB -->|reads status & logs| DSH[ServerGuardian Dashboard <br> FastAPI]
    end
    
    USR((User)) -->|views browser| DSH
```


## 1. MongoDB Atlas Setup
If you don't have a MongoDB database yet, set up a free tier cluster on MongoDB Atlas:
1. Sign up/Log in to [MongoDB Atlas](https://www.mongodb.com/cloud/atlas).
2. Create a new Shared Cluster (M0 - Free tier).
3. Under **Database Access**, create a user with read/write permissions.
4. Under **Network Access**, add `0.0.0.0/0` (allow access from anywhere) so that GitHub Actions and your hosting provider can connect to it.
5. In **Database/Clusters**, click **Connect** -> **Drivers** and copy your Connection String (URI). Replace `<password>` with the password of your user.


## 2. Configuring GitHub Secrets
GitHub Actions executes `monitor_runner.py` and requires database credentials and monitoring endpoint URLs.

1. Go to your GitHub repository.
2. Navigate to **Settings** -> **Secrets and variables** -> **Actions**.
3. Click **New repository secret** and add the following secrets:

| Secret Name | Description | Example Value |
| :--- | :--- | :--- |
| `MONGO_URI` | **(Required)** MongoDB connection string | `mongodb+srv://user:pass@cluster.mongodb.net/?retryWrites=true&w=majority` |
| `QUILLIX_API_URL` | Quillix API service endpoint url | `https://<domain>.onrender.com/health` |
| `AFFILIATE_HEALTH_URL` | Affiliate Health service endpoint url | `https://<domain>.onrender.com/status` |
| `STOCK_SENTINEL_URL` | Stock Sentinel health metrics endpoint url | `https://<domain>.onrender.com/health` |
| `VISIONRETAIL_IQ_URL` | VisionRetail IQ health metrics endpoint url | `https://<domain>.onrender.com/health` |
| `ENABLE_STOCK_SCRAPER` | Set to `true` to run stock scraping worker | `true` |
| `EMAIL_HOST` | *(Optional)* SMTP mail server host | `smtp.gmail.com` |
| `EMAIL_PORT` | *(Optional)* SMTP mail server port | `587` |
| `EMAIL_USER` | *(Optional)* SMTP sender email address | `alerts@example.com` |
| `EMAIL_PASSWORD` | *(Optional)* SMTP sender app password | `xxxx xxxx xxxx xxxx` |


## 3. GitHub Actions Workflow Execution
The monitoring workflow is defined in `.github/workflows/serverguardian-monitor.yml`.
- It executes automatically every 5 minutes using GitHub's cron scheduler.
- You can manually trigger a run by going to the **Actions** tab in your repository, selecting the **ServerGuardian Monitor** workflow, and clicking **Run workflow**.


## 4. Free Dashboard Deployment (Render)
The FastAPI dashboard is now lightweight and read-only. It reads live state from MongoDB and serves the UI.

1. Create a free account on [Render](https://render.com/).
2. Click **New** -> **Web Service**.
3. Connect your GitHub repository.
4. Use the following configuration:
   - **Runtime**: `Python`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python main.py`
5. Go to **Environment** and add the environment variable:
   - `MONGO_URI` = `your_mongodb_atlas_uri`
6. Deploy the service.

Since Render puts free services to sleep after 15 minutes of inactivity, the dashboard will sleep when you aren't viewing it. However, **your monitoring checks will never sleep** because they are run reliably by GitHub Actions in the background!
When you visit the dashboard web URL, Render will wake it up, and it will immediately display up-to-date monitoring data fetched directly from MongoDB Atlas!
