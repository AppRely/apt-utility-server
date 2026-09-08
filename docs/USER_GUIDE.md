# User Guide

This guide covers the cross-platform Docker workflow for starting and using APT Utility Server. For backend internals, testing, and migrations, see the [Developer Guide](DEVELOPER_GUIDE.md).

## Prerequisites

Install:

- Git
- Docker Engine or Docker Desktop
- Docker Compose v2 (`docker compose`)

The Docker workflow does not require a host Python installation.

## Quick Start

### Clone Repository

```bash
git clone <repository-url>
cd apt-utility-server
```

### Create Environment File

Windows PowerShell:

```powershell
Copy-Item .env.dist .env
```

Windows Command Prompt:

```cmd
copy .env.dist .env
```

Ubuntu, Linux, and macOS:

```bash
cp .env.dist .env
```

Open `.env` and review every value before starting the services. `.env.dist` contains development placeholders; replace `DJANGO_SECRET_KEY` and do not use the template database credentials outside local development.

The normal local configuration uses:

```text
DJANGO_SETTINGS_MODULE=src.config.local
DB_HOST=db
DB_PORT=5432
```

Do not commit the populated `.env` file.

### Build Application

```bash
docker compose build
```

### Start Application

```bash
docker compose up -d
```

The Compose configuration starts the API and PostgreSQL services. PostgreSQL has a health check; the web service starts after the database is healthy, applies existing migrations, and launches Django's development server.

### Check Status

```bash
docker compose ps
```

### Check Application Logs

```bash
docker compose logs -f web
```

Press `Ctrl+C` to stop following logs. This does not stop the background containers.

## Application Access

| Resource | Local URL |
| --- | --- |
| API base | <http://localhost:8002/api/v1/> |
| Swagger UI | <http://localhost:8002/swagger/> |
| ReDoc | <http://localhost:8002/redoc/> |
| Health check | <http://localhost:8002/health/> |

Opening <http://localhost:8002/> redirects to Swagger UI.

## Create a Superuser

After the database migrations have been applied, create an administrator account and follow the prompts:

```bash
docker compose run --rm web python manage.py createsuperuser
```

## Important Commands

| Command | Purpose |
| --- | --- |
| `docker compose ps` | Show service and health status. |
| `docker compose logs -f web` | Follow API logs. |
| `docker compose logs -f db` | Follow PostgreSQL logs. |
| `docker compose restart web` | Restart only the API service. |
| `docker compose up -d --build web` | Rebuild and restart the API after dependency changes. |
| `docker compose down` | Stop and remove the local containers and network. |

## Stop Application

```bash
docker compose down
```

The PostgreSQL data directory is bind-mounted at `./db-data`, so `docker compose down` does not remove the stored local database files.

## Restart Application

Restart the API only:

```bash
docker compose restart web
```

Start the complete stack again after it has been stopped:

```bash
docker compose up -d
```

## Optional Setup Script

`setup.sh` automates environment-file creation, migration checks, image building, and startup. The manual Quick Start remains the recommended cross-platform method.

The script requires a Unix-compatible shell:

- Linux and macOS: run it from a terminal.
- Windows: use WSL or Git Bash.
- Windows Command Prompt and PowerShell cannot run it directly.

```bash
chmod +x setup.sh
./setup.sh
```

## Troubleshooting

### A required port is already in use

The Compose configuration binds host ports `8002`, `5432`, and `6380`. Stop the conflicting local process or change the relevant host-side port in `docker-compose.yml`.

### The web service does not become ready

Inspect service state and API/database logs:

```bash
docker compose ps
docker compose logs web
docker compose logs db
```

### Django cannot connect to PostgreSQL

Confirm that the database values in `.env` match the Compose configuration. Within Docker Compose, the database host must be the service name:

```text
DB_HOST=db
```

### Source changes do not appear

The repository is bind-mounted into the web container, so Django source changes normally reload automatically. If a dependency file or Dockerfile changed, rebuild the web image:

```bash
docker compose up -d --build web
```

### The environment file is missing

Create `.env` from `.env.dist` with the platform-specific command in [Create Environment File](#create-environment-file), then review its values before restarting the stack.
