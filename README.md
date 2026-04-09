# TalentRadar - AI-Powered Job Intelligence Platform

**Production-ready** platform for semantic job search, market trend analysis, and intelligent candidate matching powered by LLMs and vector embeddings.

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.109+-green.svg)](https://fastapi.tiangolo.com/)
[![Next.js 14](https://img.shields.io/badge/Next.js-14-black.svg)](https://nextjs.org/)
[![PostgreSQL 15](https://img.shields.io/badge/PostgreSQL-15-blue.svg)](https://www.postgresql.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## 🚀 Features

### Core Capabilities

- **Semantic Job Search** - Natural language queries powered by vector embeddings
- **AI-Powered Insights** - LLM-generated summaries and market analysis
- **Smart Candidate Matching** - ML scoring based on skills, seniority, and location
- **Real-Time Market Trends** - Skill demands, salary insights, geographic distribution
- **Automated Data Pipeline** - Airflow-driven ingestion from multiple sources

### Technical Highlights

- **Production-Ready Architecture** - Microservices with PostgreSQL, Redis, ChromaDB
- **RESTful API** - FastAPI with OpenAPI documentation
- **Modern Frontend** - Next.js 14 with Tailwind CSS
- **Scalable Deployment** - Docker, Kubernetes, Cloud Run support
- **CI/CD Ready** - GitHub Actions workflows for testing and deployment

---

## 🏗️ Architecture

```
┌──────────────┐     ┌───────────────┐     ┌──────────────┐
│   Frontend   │────▶│   FastAPI     │────▶│  PostgreSQL  │
│  (Next.js)   │     │   (Python)    │    │   (v15)      │
└──────────────┘     └──────┬────────┘     └──────────────┘
                            │
                     ┌──────▼────────┐     ┌──────────────┐
                     │  AI Agents    │────▶│   ChromaDB   │
                     │  (RAG/ML)     │     │  (Vectors)   │
                     └──────┬────────┘     └──────────────┘
                            │
                     ┌──────▼────────┐
                     │   Airflow     │
                     │ (Scheduler)   │
                     └───────────────┘
```

### Component Overview

| Component | Technology | Purpose |
|-----------|-----------|---------|
| **API Server** | FastAPI + Uvicorn | REST API, AI agent orchestration |
| **Frontend** | Next.js 14 + Tailwind | User interface |
| **Database** | PostgreSQL 15 | Structured job/company data |
| **Vector DB** | ChromaDB | Semantic search embeddings |
| **Cache** | Redis 7 | Caching, session storage |
| **Pipeline** | Apache Airflow 2.9 | Data ingestion automation |
| **AI** | Groq (Llama 3.1) | LLM parsing, summaries |

---

## 📁 Project Structure

```
talentRadar/
├── agents/                  # AI agent layer
│   ├── prompts/            # System prompts for LLMs
│   ├── orchestrator.py     # Intent classification & routing
│   ├── rag_agent.py        # Retrieve-And-Generate agent
│   ├── trend_agent.py      # Market trend analysis
│   ├── ml_scorer.py        # ML-powered job matching
│   ├── state.py            # Agent state definitions
│   └── graph.py            # LangGraph state machine
│
├── api/                     # REST API
│   ├── routers/            # Endpoint handlers
│   │   ├── search.py       # Job search (structured + semantic)
│   │   ├── query.py        # Natural language queries
│   │   ├── recommend.py    # Candidate-job matching
│   │   ├── trends.py       # Market trends
│   │   └── ingest.py       # Pipeline management
│   ├── schemas/            # Pydantic request/response models
│   ├── main.py             # FastAPI application
│   ├── auth.py             # JWT authentication
│   └── dependencies.py     # DI providers
│
├── ingestion/               # Data pipeline
│   ├── scrapers/           # Data source connectors
│   ├── parsers/            # LLM-powered JD parsing
│   ├── embeddings/         # Vector embedding utilities
│   └── dags/               # Airflow DAG definitions
│
├── storage/                 # Data layer
│   ├── models.py           # SQLAlchemy ORM models
│   ├── repository.py       # Data access layer
│   ├── database.py         # Engine & session setup
│   └── migrations/         # Alembic migrations
│
├── frontend/                # Next.js web app
│   ├── app/                # Pages (App Router)
│   ├── components/         # React components
│   └── lib/                # API client, types, utils
│
├── infra/                   # Infrastructure
│   ├── Dockerfile          # API container
│   ├── Dockerfile.airflow  # Airflow container
│   ├── kubernetes/         # K8s manifests
│   ├── cloudrun.yaml       # Google Cloud Run config
│   └── prometheus.yml      # Monitoring config
│
├── tests/                   # Test suite
│   ├── conftest.py         # Pytest fixtures
│   ├── test_api.py         # API endpoint tests
│   ├── test_agents.py      # Agent layer tests
│   └── test_pipeline_e2e.py # End-to-end pipeline tests
│
├── docs/                    # Documentation
├── config/                  # Application settings
├── data/                    # Shared data directory
├── docker-compose.yml       # Local development stack
├── pyproject.toml           # Python dependencies
└── DEPLOYMENT.md            # Production deployment guide
```

---

## 🚦 Quick Start

### Prerequisites

- **Docker & Docker Compose** - [Install](https://docs.docker.com/get-docker/)
- **Python 3.11+** (for local development)
- **API Keys**:
  - [Groq API](https://console.groq.com/) - LLM inference
  - [Tavily API](https://tavily.com/) - Job search

### 1. Clone & Configure

```bash
git clone <repository-url>
cd talentRadar
cp .env.example .env
```

Edit `.env` and add your API keys:

```bash
GROQ_API_KEY=your_groq_api_key
TAVILY_API_KEY=your_tavily_api_key
```

### 2. Start All Services

```bash
docker compose up -d
```

This starts:
- **PostgreSQL** (port 5432)
- **Redis** (port 6379)
- **ChromaDB** (port 8001)
- **API Server** (port 8000)
- **Airflow** (port 8080)
- **Frontend** (port 3000)

### 3. Run Database Migrations

```bash
docker exec talentradar-api alembic upgrade head
```

### 4. Verify Setup

```bash
# API health check
curl http://localhost:8000/health

# API documentation
open http://localhost:8000/docs

# Frontend
open http://localhost:3000

# Airflow UI (admin/admin)
open http://localhost:8080
```

---

## 📡 API Endpoints

### Search

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/v1/search/structured` | Filter-based job search |
| `POST` | `/api/v1/search/semantic` | Natural language search |
| `GET` | `/api/v1/search/{job_id}` | Job details |

### AI-Powered

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/v1/query` | Unified AI query |
| `POST` | `/api/v1/recommend/match` | Candidate-job matching |
| `POST` | `/api/v1/trends` | Market trend analysis |
| `GET` | `/api/v1/trends/skills` | Top in-demand skills |
| `GET` | `/api/v1/trends/salaries` | Salary insights |

### Pipeline

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/v1/ingest/trigger` | Start data ingestion |
| `GET` | `/api/v1/ingest/runs` | View pipeline status |

**Full API Documentation:** http://localhost:8000/docs

---

## 🧪 Testing

```bash
# Install test dependencies
pip install .[test,dev]

# Run all tests
pytest tests/ -v

# Run specific test suite
pytest tests/test_agents.py -v

# Run with coverage
pytest tests/ --cov=. --cov-report=html

# End-to-end pipeline test
python tests/test_pipeline_e2e.py --quick
```

---

## 🚀 Production Deployment

See **[DEPLOYMENT.md](DEPLOYMENT.md)** for complete deployment guides:

- ✅ Google Cloud Run (Serverless)
- ✅ AWS ECS/Fargate
- ✅ Kubernetes (Any Cloud)
- ✅ Environment configuration
- ✅ Monitoring & alerts
- ✅ Security checklist
- ✅ Scaling guide

### Quick Deploy to Cloud Run

```bash
# Build and push
gcloud builds submit --tag gcr.io/YOUR_PROJECT/talentradar-api .

# Deploy
gcloud run deploy talentradar-api \
  --image gcr.io/YOUR_PROJECT/talentradar-api \
  --platform managed \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars POSTGRES_HOST=your-db,POSTGRES_PASSWORD=your-pass,GROQ_API_KEY=your-key
```

---

## 📊 Tech Stack

### Backend
- **Python 3.11** - Primary language
- **FastAPI** - Web framework
- **SQLAlchemy 2.0** - ORM
- **Pydantic** - Validation & serialization
- **Alembic** - Database migrations
- **Apache Airflow** - Data pipeline orchestration

### AI/ML
- **Groq (Llama 3.1)** - LLM inference
- **ChromaDB** - Vector database
- **Sentence Transformers** - Embeddings
- **Custom ML Scorer** - Job matching algorithm

### Frontend
- **Next.js 14** - React framework
- **TypeScript** - Type safety
- **Tailwind CSS** - Styling
- **Lucide Icons** - Icon library

### Infrastructure
- **PostgreSQL 15** - Primary database
- **Redis 7** - Caching
- **Docker** - Containerization
- **Kubernetes** - Orchestration
- **GitHub Actions** - CI/CD

---

## 📖 Documentation

- **[QUICKSTART.md](QUICKSTART.md)** - Step-by-step setup guide
- **[DEPLOYMENT.md](DEPLOYMENT.md)** - Production deployment guides
- **[docs/PYPROJECT_SUMMARY.md](docs/PYPROJECT_SUMMARY.md)** - Project configuration
- **[docs/DEPENDENCIES.md](docs/DEPENDENCIES.md)** - Dependency documentation
- **API Docs** - http://localhost:8000/docs (when running)

---

## 🔒 Security

- JWT authentication for protected endpoints
- CORS configuration for specific origins
- Environment variable secret management
- SQL injection prevention (parameterized queries)
- Rate limiting ready

**Before production deployment:** Review the [Security Checklist](DEPLOYMENT.md#security-checklist) in DEPLOYMENT.md.

---

## 🤝 Contributing

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

### Development Guidelines

- Follow PEP 8 for Python code
- Use TypeScript strict mode
- Write tests for new features
- Update documentation for API changes

---

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

## 🙏 Acknowledgments

- **Groq** - Ultra-fast LLM inference
- **Tavily** - Job search API
- **ChromaDB** - Open-source vector database
- **Apache Airflow** - Data pipeline orchestration

---

## 📧 Support

- **Issues:** [GitHub Issues](https://github.com/your-org/talentRadar/issues)
- **Discussions:** [GitHub Discussions](https://github.com/your-org/talentRadar/discussions)
- **API Docs:** http://localhost:8000/docs

---

**Built with ❤️ for connecting talent with opportunities**
