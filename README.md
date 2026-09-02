# APT Utility Server

## Overview

APT Utility Server is the backend API for reviewing and correcting video-tracking projects produced with APT tracking data. It imports a source video and `.trk` file, stores frame and trajectory information in PostgreSQL, streams project media, and exposes editing and analysis operations for tracked objects.

## What Problem Does This Solve?

Automated tracking data can contain identity switches, missing frames, broken trajectories, ambiguous matches, and short or abnormal track segments. This service turns raw video/TRK output into a database-backed project that reviewers can inspect and correct without rewriting tracking files directly. The corrected database state can then be exported as a new versioned `.trk` file.

## Main Features

- Video and `.trk` project upload and validation
- Browser-compatible video conversion and metadata extraction
- Frame, coordinate, confidence, object, and trajectory persistence
- Range-based frame data and HTTP Range video streaming
- Object link, overlap, break, clip, swap, delete, and interpolation operations
- Snapshot-based undo and redo for supported editing operations
- Trajectory gap, length, linking, clipping, timeline, and confusion review tools
- Activity history and CSV export
- Versioned `.trk` export rebuilt from the current database state
- Swagger UI, ReDoc, and health-check endpoints

## Technology Stack

| Technology | Purpose | Why It Is Used |
| --- | --- | --- |
| Python 3.11 | Application runtime | Supports the Django API and scientific/video-processing libraries. |
| Django 5.2 | Web framework and ORM | Provides routing integration, settings, migrations, models, and database access. |
| Django REST Framework 3.16 | API layer | Supplies view sets, serializers, validation, parsers, and responses. |
| PostgreSQL 14.2 | Project data store | Persists projects, frames, objects, trajectories, snapshots, and review data. |
| NumPy, h5py, hdf5storage | TRK processing | Reads and transforms numerical and HDF5-based tracking data. |
| OpenCV and FFmpeg | Video processing | Supports video inspection, conversion, metadata extraction, and frame-related operations. |
| drf-yasg | API schema | Generates Swagger UI and ReDoc from the implemented API. |
| Docker and Docker Compose | Development environment | Provides consistent Python and PostgreSQL services across supported platforms. |

## Why These Technologies?

Django and Django REST Framework provide a structured HTTP and persistence layer around complex tracking operations. PostgreSQL handles indexed frame/object relationships and transactional edits. The scientific Python and media tools work with APT/TRK structures and browser video formats. Docker Compose packages these native and Python dependencies into a reproducible local environment.

## High-Level Architecture

```text
Client
  -> Django URL router
  -> DRF VideoViewSet
  -> serializer validation
  -> video service or direct ORM query
  -> PostgreSQL and/or media storage
  -> JSON, streamed media, CSV, or TRK response
```

The larger project operations are implemented in `src/video/services/`. Some read endpoints query models directly after serializer validation, while upload, editing, analysis, snapshot, and export operations delegate to focused services.

## Main Application Flow

```text
Upload video + TRK
  -> store/convert video and extract metadata
  -> validate TRK data
  -> create project
  -> bulk insert video frames and tracked objects
  -> rebuild object trajectories
  -> generate review/confusion data
  -> inspect and edit through the API
  -> export current database state as a versioned TRK
```

Editing services update frame/object state transactionally. Supported operations record activity and before/after snapshots so they can be undone or redone.

## Project Structure

```text
apt-utility-server/
├── docs/                     # User, API, and developer documentation
├── docker/                   # Container entrypoint scripts
├── requirements/             # Development and production dependencies
├── src/
│   ├── config/               # Django settings and application configuration
│   ├── video/
│   │   ├── external/         # TRK compatibility code
│   │   ├── migrations/       # Video database migrations
│   │   ├── services/         # Upload, editing, analysis, and export logic
│   │   ├── models.py         # Project and tracking data models
│   │   ├── serializers.py    # Input validation and response schemas
│   │   ├── urls.py           # Video router registration
│   │   └── views.py          # Video API actions
│   ├── urls.py               # Root routes and API documentation URLs
│   └── wsgi.py               # WSGI application
├── .env.dist                 # Local environment template
├── docker-compose.yml        # Local development services
├── Dockerfile                # Python 3.11 application image
└── manage.py                 # Django command entry point
```

## Documentation

- [User Guide](docs/USER_GUIDE.md) – Cross-platform setup, application access, operational commands, and troubleshooting.
- [API Documentation](docs/API_DOCUMENTATION.md) – Video API groups, endpoint purposes, inputs, formats, and workflows.
- [Developer Guide](docs/DEVELOPER_GUIDE.md) – Architecture, data flows, models, services, migrations, testing, and contribution workflow.

## Quick Start

1. Clone the repository and enter its directory.
2. Copy `.env.dist` to `.env` and replace the development placeholders.
3. Build and start the development services:

   ```bash
   docker compose build
   docker compose up -d
   ```

4. Check startup status and logs:

   ```bash
   docker compose ps
   docker compose logs -f web
   ```

See the [User Guide](docs/USER_GUIDE.md) for Windows, Linux, and macOS environment-file commands and troubleshooting.

## Important Links

With the local stack running:

- API base: <http://localhost:8002/api/v1/>
- Swagger UI: <http://localhost:8002/swagger/>
- ReDoc: <http://localhost:8002/redoc/>
- Health check: <http://localhost:8002/health/>

The root URL redirects to Swagger UI.
