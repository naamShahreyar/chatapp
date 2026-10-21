# ChatApp

[![CI/CD](https://github.com/naamShahreyar/chatapp/actions/workflows/ci.yml/badge.svg)](https://github.com/naamShahreyar/chatapp/actions/workflows/ci.yml)

A full-stack AI chat application with user accounts and persistent conversation memory: a **Streamlit** frontend, a **FastAPI** backend, a **LangGraph** agent, and **PostgreSQL**, fully containerized with **Docker** and deployed to **AWS ECS Fargate** by a CI/CD pipeline.

## Features

- **User accounts** — sign up and log in; passwords hashed with Argon2, sessions via JWT
- **Multiple conversations per user**, listed in a sidebar and titled from the first message
- **Persistent memory** — every message is stored in Postgres, and the full history is sent to the model on each turn
- **Per-user isolation** — users can only see, read, or delete their own conversations
- **Swappable LLM** — Google Gemini, or a built-in echo model for local development and tests (no API key, no cost)
- **Graceful LLM failures** — if the model is overloaded or out of quota, users get a clear "try again" message (HTTP 503) and nothing half-finished is saved
- **One-command local setup** with Docker Compose
- **CI/CD** — every push is tested and built; pushes to `main` deploy automatically to AWS with no stored AWS keys

## Architecture

```mermaid
flowchart LR
    U([Browser]) -->|:8501| F[Streamlit frontend]
    F -->|REST + JWT| B[FastAPI backend]
    B --> D[(PostgreSQL)]
    B --> A[LangGraph agent]
    A --> L[LLM: Gemini / echo]
```

The frontend never talks to the database or the LLM directly. All access goes through the backend, which checks the user's token first.

### How memory works

LLM APIs are stateless, so the app re-sends the conversation on every turn:

1. The backend loads the conversation's previous messages from Postgres.
2. It appends the new user message and passes the full history to the agent.
3. The agent (`backend/app/agent/`) returns a reply.
4. The user message and the reply are saved together in one transaction. If the LLM call fails, nothing is written.

The agent module takes plain message dicts and returns a string. It doesn't know about HTTP or the database, so the model or the graph can change without touching the rest of the app.

## Tech stack

| Layer | Technology |
|---|---|
| Frontend | Streamlit |
| Backend | FastAPI, Pydantic, SQLAlchemy 2.0 |
| Agent | LangGraph, LangChain (Google Gemini) |
| Database | PostgreSQL 16 |
| Auth | Argon2 password hashing (pwdlib), JWT (PyJWT) |
| Testing | pytest, FastAPI TestClient, in-memory SQLite |
| Containers | Docker, Docker Compose |
| CI/CD | GitHub Actions, OIDC federation to AWS |
| Cloud | AWS: ECS Fargate, ECR, RDS, Application Load Balancer, SSM Parameter Store, CloudWatch Logs, IAM, Organizations |

## Project structure

```
.
├── backend/
│   ├── app/
│   │   ├── agent/            # LangGraph agent: model selection, graph, public interface
│   │   ├── routers/          # API endpoints: auth.py, chat.py
│   │   ├── config.py         # Settings loaded from environment variables
│   │   ├── db.py             # Database engine and session dependency
│   │   ├── deps.py           # get_current_user dependency
│   │   ├── models.py         # SQLAlchemy tables: users, conversations, messages
│   │   ├── schemas.py        # Pydantic request/response models
│   │   ├── security.py       # Password hashing and JWT helpers
│   │   └── main.py           # FastAPI app
│   ├── tests/                # pytest suite
│   ├── Dockerfile
│   ├── .env.example
│   ├── requirements.txt
│   └── requirements-dev.txt
├── frontend/
│   ├── app.py                # Streamlit UI
│   ├── Dockerfile
│   └── requirements.txt
├── infra/                    # AWS config as code: ECS task definitions, Service Connect, IAM policies
├── docs/
│   └── aws-runbook.md        # Step-by-step AWS setup, operations, troubleshooting, teardown
├── docker-compose.yml        # Local development: builds images from source
├── docker-compose.prod.yml   # Single-server deployment: runs images from ECR, secrets from .env
└── .github/workflows/ci.yml  # Test → build → deploy to ECS
```

## Quick start (Docker)

Requires [Docker Desktop](https://www.docker.com/products/docker-desktop/).

```bash
git clone https://github.com/naamShahreyar/chatapp.git
cd chatapp
cp backend/.env.example backend/.env    # Windows PowerShell: Copy-Item backend\.env.example backend\.env
docker compose up --build
```

Then open:

- **App:** http://localhost:8501
- **API docs:** http://localhost:8000/docs

The example config uses the built-in echo model, so it works without an API key. To use Gemini, see [Configuration](#configuration).

Useful commands:

```bash
docker compose up -d                 # start in the background
docker compose logs -f backend       # follow backend logs
docker compose down                  # stop (data is kept)
docker compose down -v               # stop and DELETE all data
```

## Configuration

The backend reads its settings from environment variables, or from `backend/.env`:

| Variable | Required | Description |
|---|---|---|
| `DATABASE_URL` | Yes | SQLAlchemy URL, e.g. `postgresql+psycopg://user:pass@host:5432/db` |
| `JWT_SECRET` | Yes | Secret for signing login tokens. Use a long random value |
| `LLM_PROVIDER` | No | `fake` (default, echo bot) or `google` |
| `LLM_MODEL` | With `google` | Gemini model name, e.g. `gemini-2.5-flash` |
| `GOOGLE_API_KEY` | With `google` | API key from [Google AI Studio](https://aistudio.google.com) |

The frontend reads one variable, `API_URL` (default `http://localhost:8000`).

**Never commit `.env` files.** They're listed in `.gitignore` and `.dockerignore`.

## Local development (without Docker)

Run Postgres in Docker, then the backend and frontend directly on your machine.

```bash
# Postgres
docker run -d --name chatapp-db -e POSTGRES_USER=chatapp -e POSTGRES_PASSWORD=chatapp \
  -e POSTGRES_DB=chatapp -p 5432:5432 -v chatapp-pgdata:/var/lib/postgresql/data postgres:16

# Backend (terminal 1)
cd backend
python -m venv .venv
source .venv/bin/activate               # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
uvicorn app.main:app --reload

# Frontend (terminal 2)
cd frontend
python -m venv .venv
source .venv/bin/activate               # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run app.py
```

## Testing

```bash
cd backend
pytest -v
```

Tests run against a fresh in-memory SQLite database for each test and always use the echo model, so they need no Docker, network, or API key. They cover signup and login, token protection, conversation memory, titles, deletion, isolation between users, and LLM failures (a simulated outage must return 503 and save nothing).

## API

All endpoints except signup, login, and health checks require an `Authorization: Bearer <token>` header. Interactive documentation is available at `/docs`.

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness check |
| `GET` | `/health/db` | Database connectivity check |
| `POST` | `/auth/signup` | Create an account |
| `POST` | `/auth/login` | Log in (form data) and receive a JWT |
| `GET` | `/auth/me` | The current user |
| `POST` | `/conversations` | Start a conversation |
| `GET` | `/conversations` | List your conversations |
| `GET` | `/conversations/{id}/messages` | A conversation's message history |
| `POST` | `/conversations/{id}/messages` | Send a message and get the AI reply (`503` if the model is unavailable) |
| `DELETE` | `/conversations/{id}` | Delete a conversation and its messages |

Requests for another user's conversation return `404`, so the API never confirms that a conversation exists.

## CI/CD

GitHub Actions ([`ci.yml`](.github/workflows/ci.yml)) runs on every pull request and every push to `main`:

1. **test** — installs dependencies and runs pytest
2. **build** — builds both Docker images; runs only if the tests pass
3. **deploy** — pushes to `main` only:
   - gets **temporary AWS credentials through OIDC**. No AWS keys are stored in GitHub, and the IAM role trusts only this repository's `main` branch
   - builds and pushes both images to ECR, tagged with the **commit SHA**
   - registers new ECS task definition revisions from [`infra/`](infra/) and rolls them out
   - waits until ECS reports the services stable, and fails the run otherwise

Pull requests never deploy. Deploys are serialized, so a second push waits for the first to finish.

## Deployment (AWS)

```mermaid
flowchart LR
    U([Browser]) -->|:80| ALB[Application Load Balancer]
    ALB -->|:8501| F[frontend · ECS Fargate]
    F -->|:8000 Service Connect| B[backend · ECS Fargate]
    B -->|:5432| D[(RDS PostgreSQL)]
    B --> G[Gemini API]
    S[SSM Parameter Store] -. secrets at start-up .-> B
```

### Current: ECS Fargate

- **ECS Fargate** runs the frontend and backend as services, with no servers to manage. Failed tasks are replaced automatically, and deploys are rolling with zero downtime.
- An **Application Load Balancer** provides a stable URL and health-checks the frontend.
- **ECS Service Connect** lets the frontend reach the backend at `http://backend:8000`, the same address as in Docker Compose.
- **Amazon RDS** for PostgreSQL has no public IP.
- **Security groups are chained:** load balancer → frontend → backend → database. Each layer accepts traffic only from the one in front of it.
- **Secrets** (database URL, JWT secret, Gemini key) are stored encrypted in **SSM Parameter Store** and injected at container start. They're never in images or in Git.
- Logs go to **CloudWatch Logs**.

Full setup, operations and teardown: **[docs/aws-runbook.md](docs/aws-runbook.md)**.

### Alternative: a single EC2 instance

The same images can also run on one **EC2** instance with `docker-compose.prod.yml`:

- The instance pulls from ECR using an **IAM role**, so no access keys are stored on the server.
- Admin access is through **AWS Systems Manager Session Manager**, so port 22 isn't open.
- The security group exposes only port 8501.
- Production secrets are generated on the server and kept in a `.env` file readable only by its owner.
- `restart: unless-stopped` and a named volume keep the app and its data running across reboots.

```bash
# Push images (from the repo root)
aws ecr get-login-password | docker login --username AWS --password-stdin <account-id>.dkr.ecr.<region>.amazonaws.com
docker build -t chatapp-backend ./backend
docker tag chatapp-backend:latest <account-id>.dkr.ecr.<region>.amazonaws.com/chatapp-backend:latest
docker push <account-id>.dkr.ecr.<region>.amazonaws.com/chatapp-backend:latest
# ...and the same for chatapp-frontend

# On the server, in the directory containing docker-compose.prod.yml and .env
sudo docker compose -f docker-compose.prod.yml up -d
```

## Roadmap

- [x] ECS Fargate + RDS deployment
- [x] Continuous deployment from GitHub Actions
- [ ] HTTPS with a custom domain
- [ ] Fallback LLM model when the primary is overloaded or out of quota
- [ ] Database migrations with Alembic (replacing `create_all`)
- [ ] Run CI tests against a real Postgres service container
- [ ] Stream responses token by token
- [ ] Infrastructure as code (Terraform)
