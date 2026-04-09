# TalentRadar Deployment Guide

Complete guide for deploying TalentRadar to production on any cloud provider.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Prerequisites](#prerequisites)
3. [Local Development](#local-development)
4. [Production Deployment Options](#production-deployment-options)
   - [Option A: Google Cloud Run](#option-a-google-cloud-run)
   - [Option B: AWS ECS/Fargate](#option-b-aws-ecsfargate)
   - [Option C: Kubernetes (Any Cloud)](#option-c-kubernetes-any-cloud)
5. [Database Setup](#database-setup)
6. [Environment Configuration](#environment-configuration)
7. [Monitoring & Observability](#monitoring--observability)
8. [Security Checklist](#security-checklist)
9. [Troubleshooting](#troubleshooting)

---

## Architecture Overview

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│   Frontend  │────▶│  FastAPI     │────▶│ PostgreSQL  │
│  (Next.js)  │     │  (Python)    │     │   (v15)     │
└─────────────┘     └──────┬───────┘     └─────────────┘
                           │
                    ┌──────▼───────┐     ┌─────────────┐
                    │  AI Agents   │────▶│  ChromaDB   │
                    │  (RAG/ML)    │     │  (Vectors)  │
                    └──────┬───────┘     └─────────────┘
                           │
                    ┌──────▼───────┐
                    │   Airflow    │
                    │ (Scheduler)  │
                    └──────────────┘
```

### Components

| Component | Technology | Purpose |
|-----------|-----------|---------|
| API Server | FastAPI + Uvicorn | REST API, AI agent orchestration |
| Frontend | Next.js 14 + Tailwind | User interface |
| Database | PostgreSQL 15 | Structured job/company data |
| Vector DB | ChromaDB | Semantic search embeddings |
| Cache | Redis 7 | Caching, session storage |
| Pipeline | Apache Airflow 2.9 | Data ingestion automation |
| AI | Groq (Llama 3.1) | LLM parsing, summaries |

---

## Prerequisites

- **Docker & Docker Compose** (local/dev)
- **Python 3.11+** (development)
- **Node.js 20+** (frontend development)
- **Cloud Provider Account** (GCP/AWS/Azure)
- **API Keys**:
  - Groq API key (LLM)
  - Tavily API key (job search)

---

## Local Development

### 1. Clone & Configure

```bash
git clone <repo-url>
cd talentRadar
cp .env.example .env
# Edit .env with your API keys
```

### 2. Start All Services

```bash
docker compose up -d
```

This starts:
- PostgreSQL (port 5432)
- Redis (port 6379)
- ChromaDB (port 8001)
- API Server (port 8000)
- Airflow (port 8080)
- Frontend (port 3000)

### 3. Run Database Migrations

```bash
docker exec talentradar-api alembic upgrade head
```

### 4. Verify

- API Docs: http://localhost:8000/docs
- Frontend: http://localhost:3000
- Airflow UI: http://localhost:8080 (admin/admin)

---

## Production Deployment Options

### Option A: Google Cloud Run

**Best for:** Serverless, auto-scaling, pay-per-use

#### 1. Set up Cloud SQL (PostgreSQL)

```bash
# Create Cloud SQL instance
gcloud sql instances create talentradar-db \
  --database-version=POSTGRES_15 \
  --tier=db-f1-micro \
  --region=us-central1

# Create database
gcloud sql databases create talentradar \
  --instance=talentradar-db

# Create user
gcloud sql users create talentradar \
  --instance=talentradar-db \
  --password=SECURE_PASSWORD
```

#### 2. Deploy API

```bash
# Build and push image
gcloud builds submit --tag gcr.io/YOUR_PROJECT/talentradar-api .

# Deploy to Cloud Run
gcloud run deploy talentradar-api \
  --image gcr.io/YOUR_PROJECT/talentradar-api \
  --platform managed \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars ^:^POSTGRES_HOST=/cloudsql/YOUR_PROJECT:us-central1:talentradar-db:POSTGRES_USER=talentradar:POSTGRES_PASSWORD=SECURE_PASSWORD:POSTGRES_DB=talentradar:GROQ_API_KEY=your_key:TAVILY_API_KEY=your_key \
  --add-cloudsql-instances YOUR_PROJECT:us-central1:talentradar-db \
  --min-instances=1 \
  --max-instances=10 \
  --memory=2Gi \
  --cpu=2
```

#### 3. Deploy Frontend (Vercel Recommended)

```bash
# Install Vercel CLI
npm i -g vercel

cd frontend
vercel --prod \
  --env NEXT_PUBLIC_API_URL=https://talentradar-api-xxxx.a.run.app
```

#### 4. Setup Airflow (Cloud Composer or self-hosted)

For production, use **Google Cloud Composer** (managed Airflow):

```bash
gcloud composer environments create talentradar-airflow \
  --location=us-central1 \
  --image-version=airflow-2.9.3 \
  --python-version=3.11
```

Or self-host on Cloud Run with Cloud SQL proxy.

---

### Option B: AWS ECS/Fargate

**Best for:** AWS ecosystem, container orchestration

#### 1. Set up RDS (PostgreSQL)

```bash
# Create RDS instance
aws rds create-db-instance \
  --db-instance-identifier talentradar-db \
  --db-instance-class db.t3.micro \
  --engine postgres \
  --engine-version 15 \
  --allocated-storage 20 \
  --master-username talentradar \
  --master-user-password SECURE_PASSWORD \
  --db-name talentradar
```

#### 2. Create ECS Task Definition

```json
{
  "family": "talentradar-api",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "1024",
  "memory": "2048",
  "containerDefinitions": [
    {
      "name": "api",
      "image": "YOUR_ECR_REPO/talentradar-api:latest",
      "portMappings": [{ "containerPort": 8000 }],
      "environment": [
        { "name": "POSTGRES_HOST", "value": "talentradar-db.xxxxx.us-east-1.rds.amazonaws.com" },
        { "name": "POSTGRES_USER", "value": "talentradar" },
        { "name": "GROQ_API_KEY", "value": "your_key" }
      ],
      "secrets": [
        { "name": "POSTGRES_PASSWORD", "valueFrom": "arn:aws:secretsmanager:..." }
      ],
      "logConfiguration": {
        "logDriver": "awslogs",
        "options": {
          "awslogs-group": "/ecs/talentradar-api",
          "awslogs-region": "us-east-1"
        }
      }
    }
  ]
}
```

#### 3. Deploy to ECS

```bash
aws ecs create-service \
  --cluster talentradar \
  --service-name api \
  --task-definition talentradar-api \
  --desired-count 2 \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[subnet-xxxx],securityGroups=[sg-xxxx],assignPublicIp=ENABLED}"
```

---

### Option C: Kubernetes (Any Cloud)

**Best for:** Full control, multi-cloud, custom infrastructure

#### 1. Apply Kubernetes manifests

```bash
# Create namespace
kubectl create namespace talentradar

# Create secrets
kubectl create secret generic talentradar-secrets \
  --namespace talentradar \
  --from-literal=postgres_password=SECURE_PASSWORD \
  --from-literal=groq_api_key=your_key \
  --from-literal=tavily_api_key=your_key

# Deploy all services
kubectl apply -f infra/kubernetes/
```

#### 2. Setup Ingress

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: talentradar-ingress
  annotations:
    kubernetes.io/ingress.class: nginx
    cert-manager.io/cluster-issuer: letsencrypt-prod
spec:
  tls:
    - hosts:
        - api.talentradar.yourdomain.com
      secretName: talentradar-tls
  rules:
    - host: api.talentradar.yourdomain.com
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: talentradar-api
                port:
                  number: 8000
```

---

## Database Setup

### PostgreSQL Configuration

**Production Recommendations:**

| Setting | Value | Notes |
|---------|-------|-------|
| Instance Type | db.r6g.large (AWS) / n1-standard-4 (GCP) | 2 vCPU, 8GB RAM |
| Storage | 100GB SSD | Autoscale enabled |
| Backups | Daily, 7-day retention | Point-in-time recovery |
| High Availability | Multi-AZ | Automatic failover |
| Connection Pooling | PgBouncer | 50-100 connections |

### Running Migrations

```bash
# Production
docker run --rm \
  -e POSTGRES_HOST=your-db-host \
  -e POSTGRES_PASSWORD=your-password \
  your-registry/talentradar-api:latest \
  alembic upgrade head
```

---

## Environment Configuration

### Required Environment Variables

```bash
# AI Services
GROQ_API_KEY=your_groq_api_key
TAVILY_API_KEY=your_tavily_api_key

# Database
POSTGRES_USER=talentradar
POSTGRES_PASSWORD=<secure_password>
POSTGRES_DB=talentradar
POSTGRES_HOST=<db_host>
POSTGRES_PORT=5432

# Redis
REDIS_URL=redis://<redis_host>:6379/0

# ChromaDB
CHROMA_HOST=<chroma_host>
CHROMA_PORT=8000

# JWT
JWT_SECRET_KEY=<generate_secure_32_char_key>
JWT_ALGORITHM=HS256
JWT_EXPIRY_MINUTES=60

# Frontend
NEXT_PUBLIC_API_URL=https://api.talentradar.yourdomain.com
```

### Generate Secure JWT Key

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

---

## Monitoring & Observability

### 1. Health Checks

All services expose `/health` endpoint:

```bash
curl https://api.talentradar.yourdomain.com/health
```

### 2. Prometheus Metrics

Configure Prometheus to scrape:
- API: `/metrics` (add prometheus-fastapi-instrumentator)
- PostgreSQL: postgres-exporter (port 9187)
- Redis: redis-exporter (port 9121)

### 3. Logging

All services output structured JSON logs. Configure your log aggregator (Cloud Logging, CloudWatch, Datadog) to collect from:

```
/api/v1/query          - User queries
/api/v1/search/*       - Search requests
/api/v1/recommend/*    - Matching requests
/api/v1/trends/*       - Trend analysis
```

### 4. Alerts

Recommended alerts:
- API error rate > 5% (5 min window)
- Database connection pool > 80%
- Airflow DAG failures > 0 (1 hour window)
- ChromaDB response time > 2s

---

## Security Checklist

### Before Going Live

- [ ] Change all default passwords
- [ ] Enable HTTPS for all endpoints
- [ ] Configure CORS with specific origins (not `*`)
- [ ] Set up WAF (Web Application Firewall)
- [ ] Enable database encryption at rest
- [ ] Rotate API keys
- [ ] Set up secret management (AWS Secrets Manager / GCP Secret Manager)
- [ ] Enable audit logging
- [ ] Configure rate limiting
- [ ] Test backup and restore procedures
- [ ] Set up monitoring and alerts
- [ ] Review and restrict IAM permissions

### CORS Configuration

Update `api/main.py`:

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://talentradar.yourdomain.com"],  # NOT "*"
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)
```

---

## Troubleshooting

### API Won't Start

```bash
# Check logs
docker logs talentradar-api

# Common issues:
# 1. Database not accessible
docker exec talentradar-api python -c "from storage.database import engine; print('OK')"

# 2. Missing environment variables
docker exec talentradar-api env | grep POSTGRES
```

### Database Migration Fails

```bash
# Check current version
docker exec talentradar-api alembic current

# Rollback if needed
docker exec talentradar-api alembic downgrade -1

# Retry migration
docker exec talentradar-api alembic upgrade head
```

### Airflow DAG Not Running

```bash
# Check DAG status
curl -u admin:admin http://localhost:8080/api/v1/dags/talentradar_fetch_and_parse

# View logs
docker logs talentradar-airflow-scheduler

# Trigger manually
curl -X POST -u admin:admin http://localhost:8080/api/v1/dags/talentradar_fetch_and_parse/dagRuns
```

### Frontend Can't Connect to API

1. Check `NEXT_PUBLIC_API_URL` in `.env`
2. Verify API is accessible: `curl https://api.talentradar.yourdomain.com/health`
3. Check CORS settings in API logs
4. Ensure no firewall blocking port 8000

---

## Scaling Guide

### Horizontal Scaling

| Component | Min | Max | Trigger |
|-----------|-----|-----|---------|
| API | 2 | 10 | CPU > 70% |
| Frontend | 2 | 5 | Requests > 1000/min |
| PostgreSQL | 1 | 1 (read replicas: 3) | Connections > 80% |
| Redis | 1 | 1 (cluster mode if needed) | Memory > 75% |

### Database Optimization

1. Add indexes for frequent queries
2. Use read replicas for analytics
3. Partition `jobs` table by date if > 1M rows
4. Archive old ingestion_runs quarterly

---

## Cost Estimates (Monthly)

### Google Cloud Run (Serverless)

| Service | Configuration | Cost |
|---------|--------------|------|
| Cloud Run API | 2 instances, 2GB RAM | ~$50-150 |
| Cloud SQL | db-f1-micro, 20GB | ~$15 |
| ChromaDB | Cloud Run, 1GB RAM | ~$20 |
| Redis | Memorystore basic | ~$15 |
| Frontend | Vercel Hobby | Free-$20 |
| **Total** | | **~$100-220/mo** |

### AWS ECS/Fargate

| Service | Configuration | Cost |
|---------|--------------|------|
| ECS/Fargate | 2 tasks, 2GB | ~$60-180 |
| RDS | db.t3.micro, 20GB | ~$15 |
| ElastiCache | cache.t3.micro | ~$12 |
| Frontend | S3 + CloudFront | ~$5 |
| **Total** | | **~$90-210/mo** |

---

## Next Steps

1. [ ] Deploy to staging environment
2. [ ] Run load testing (locust.io recommended)
3. [ ] Set up CI/CD pipeline
4. [ ] Configure production monitoring
5. [ ] Test disaster recovery
6. [ ] Document runbooks for on-call
7. [ ] Launch! 🚀

---

**Need Help?**
- API Docs: http://localhost:8000/docs
- GitHub Issues: <your-repo>/issues
- Architecture Diagrams: `/docs/` folder
