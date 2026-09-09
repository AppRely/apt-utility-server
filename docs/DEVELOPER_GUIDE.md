# Developer Guide

This guide is for backend developers changing APT Utility Server. It focuses on the active Video application, its persistence and service layers, and the checked-in development workflow.

## Development Environment

The primary development environment is Docker Compose.

| Component | Repository Configuration |
| --- | --- |
| Python | 3.11 (`Dockerfile`) |
| Django | 5.2 (`requirements/prod.txt`) |
| PostgreSQL | 14.2 (`docker-compose.yml`) |
| API host port | `8002` mapped to container port `8000` |
| Django settings | `src.config.local` |

Create `.env` from `.env.dist` and review its placeholder values. The core local settings are:

| Variable | Purpose |
| --- | --- |
| `DJANGO_SETTINGS_MODULE` | Selects the Django settings module. |
| `DJANGO_SECRET_KEY` | Signs Django security-sensitive values. |
| `DJANGO_DEBUG` | Enables local debug behavior when `True`. |
| `DB_NAME` | PostgreSQL database name. |
| `DB_USER` | PostgreSQL user. |
| `DB_PASSWORD` | PostgreSQL password. |
| `DB_HOST` | Database host; use `db` inside Compose. |
| `DB_PORT` | PostgreSQL container port, normally `5432`. |
| `VIDEO_OPERATION_BATCH_SIZE` | Batch size for snapshot and undo/redo writes; defaults to `500`. |

See the [User Guide](USER_GUIDE.md) for platform-specific setup and runtime commands.

## Project Structure

```text
apt-utility-server/
├── docs/                         # Project documentation
├── docker/                       # Container entrypoint scripts
├── requirements/
│   ├── dev.txt                   # Development and test dependencies
│   └── prod.txt                  # Runtime dependencies
├── src/
│   ├── config/                   # Django settings and root configuration
│   ├── video/
│   │   ├── external/             # External/compatibility TRK implementation
│   │   ├── migrations/           # Database migrations
│   │   ├── services/             # Business and data-processing services
│   │   ├── FlyMovieFormat.py     # FlyMovie format support
│   │   ├── TrkFile.py            # TRK reader/writer implementation
│   │   ├── models.py             # Tracking persistence models
│   │   ├── movies.py             # Movie format and AVI helpers
│   │   ├── serializers.py        # Validation and API orchestration
│   │   ├── urls.py               # Video router registration
│   │   └── views.py              # VideoViewSet and custom actions
│   ├── urls.py                   # Root API and documentation routes
│   └── wsgi.py                   # WSGI entry point
├── .pre-commit-config.yaml       # Repository quality hooks
├── docker-compose.yml            # Local service definitions
├── Dockerfile                    # Development application image
├── manage.py                     # Django CLI entry point
└── pyproject.toml                # Python tool configuration
```

## Application Architecture

The Video application uses a mixed DRF/service architecture:

- `src/urls.py` combines the Video router under `/api/v1/`.
- `VideoViewSet` declares standard Project CRUD plus custom project, frame, object, trajectory, activity, and export actions.
- serializers validate query parameters or request bodies and sometimes orchestrate a service call.
- focused services perform larger business operations, database mutations, bulk writes, media handling, and exports.
- simpler read actions may query Django models directly after validation.
- models and local media files are the persistent sources used to build responses and exports.

This is not a strict rule that every request must pass through every layer.

## Video App Architecture

### Models

| Model | Responsibility |
| --- | --- |
| `Project` | Project metadata, media/TRK locations, video properties, status, and skeleton graph. |
| `VideoFrame` | One indexed frame within a project. |
| `FrameObject` | Coordinates and related tracking data for one object in one frame; supports soft deletion and interpolation flags. |
| `ObjectTrack` | Active/inactive object lifecycle with start and end frames. |
| `ActivityLog` | Project operation history and applied/unapplied state. |
| `OperationSnapshot` | Before/after state used by undo and redo. |
| `FrameConfusion` | Stored ambiguous matching and crowding review rows. |
| `ObjectLinkingSuggestion` | Ranked links between possible source and target tracks. |

Project deletion cascades through related frame and tracking records. Dedicated deletion logic also removes associated local files and exports.

### Serializers

`src/video/serializers.py` contains input validation and API-facing schemas. It validates project existence, frame bounds, object lifecycles, operation ranges, and response-specific options before services mutate or retrieve data.

Important serializer groups include:

- project upload and deletion;
- frame, range, timeline, and object lifecycle queries;
- link, break, clip, swap, delete, interpolation, undo, and redo operations;
- trajectory suggestions, gaps, and lengths;
- TRK and activity-log export.

### Services

The `src/video/services/` package separates the larger operations:

| Area | Representative Services |
| --- | --- |
| Project import/storage | `ProjectUploadService`, `ProjectFileStorageService`, `TrkValidationService`, `ProjectDeletionService` |
| Bulk persistence | `VideoFrameBulkInsertService`, `FrameObjectBulkInsertService`, `ObjectTrackRebuildService` |
| Frame/object reads | `FrameInfoService`, `FrameObjectRangeService`, `FrameTimelineService`, `UniqueIdsService` |
| Object edits | `LinkObjectService`, `BreakObjectService`, `ClipObjectService`, `ObjectLifecycleService` |
| Trajectory review | `NextBreakService`, `TrajectoryGapService`, `TrajectoryLengthService`, `TrajectoryMatchingService`, `TrajectoryClipSuggestionService` |
| History | `SnapshotBuilder`, `SnapshotLogger`, `UndoRedoService` |
| Confusion review | `ConfusionStoreService`, `ConfusionTableService` |
| Export | `TrkBuilderExportService`, `ActivityLogExportService` |

### TRK and Video Processing

`TrkFile.py` and `src/video/external/` provide APT/TRK loading and conversion behavior. Upload storage uses FFmpeg/ffprobe to inspect video metadata and converts non-compatible video to H.264/yuv420p MP4 for browser playback. The Docker image installs the required system media and numerical libraries.

## Request Flow

Typical service-backed operation:

```text
HTTP request
  -> /api/v1/ router
  -> VideoViewSet action
  -> serializer validation
  -> focused service
  -> transaction / ORM / media operation
  -> serialized response
```

Typical read operation:

```text
HTTP GET
  -> VideoViewSet action
  -> query serializer
  -> read service or direct ORM query
  -> JSON or compressed/streamed response
```

## Data Flow

### Project Upload

`POST /api/v1/videos/project-upload/` follows this sequence:

1. `ProjectUploadSerializer` validates `project_name`, `video_file`, and `tracking_file`.
2. `ProjectFileStorageService` writes both files, converts video when required, and extracts FPS, dimensions, duration, and total frames with FFmpeg/ffprobe.
3. `TrkValidationService` loads the TRK and confirms that frame data exists.
4. A database transaction creates `Project`, bulk inserts `VideoFrame` and `FrameObject` rows, and rebuilds `ObjectTrack` records.
5. After commit, confusion rows and object-linking suggestions are submitted to the application's thread executor.
6. The project stores API stream URLs and returns the project ID and inserted-row count.

The post-commit review jobs use an in-process `ThreadPoolExecutor`. They are not persistent queue jobs and are tied to the running web process.

### Frame and Object Reads

Frame services fetch JSON-friendly object data by frame or range. The fallback range endpoint uses the nearest earlier stored frame when data is missing; the no-fallback endpoint preserves gaps and returns gzip-compressed, hex-encoded JSON. Timeline data uses zlib compression unless `debug=true` is requested.

### Editing Operations

Link, break, clip, swap, soft-delete, and interpolation services validate the selected project, object lifecycle, and frame ranges before mutation. Database changes are transactional. Snapshot-aware operations store affected `FrameObject` and `ObjectTrack` state through `SnapshotLogger`, which creates `ActivityLog` and `OperationSnapshot` records.

`UndoRedoService` replays operation-specific before or after state in configurable batches. When a new applied activity is written, the existing redo stack is cleared.

### Trajectory Review

Review services derive:

- continuous runtime breaks and significant gaps;
- inclusive trajectory frame-span lengths;
- possible trajectory continuations across an internal gap;
- movement spikes that may indicate a clip interval;
- timeline representations for selected objects; and
- stored uncertainty/confusion rows with filtering and ordering.

These suggestion/read endpoints do not apply edits automatically.

### TRK Export

The export service rebuilds active `ObjectTrack` state from authoritative frame objects, copies the original TRK to the next available versioned export path, writes current frame/object data and metadata, and saves the result. It does not reconstruct state by replaying activity logs.

## Database and Migrations

The web service applies existing migrations during startup. After changing models, create and apply a migration explicitly:

```bash
docker compose run --rm web python manage.py makemigrations video
docker compose run --rm web python manage.py migrate
```

To create migrations for all apps with model changes:

```bash
docker compose run --rm web python manage.py makemigrations
```

To apply migrations for a particular app (for example, `video`, including required dependencies):

```bash
docker compose run --rm web python manage.py migrate video
```

Inspect migration state:

```bash
docker compose run --rm web python manage.py showmigrations
```

Open PostgreSQL using the credentials already available inside the database container:

```bash
docker compose exec db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

Inside `psql`, list tables and inspect a table, replacing `table_name` with a listed name:

```sql
\dt
SELECT * FROM table_name LIMIT 10;
```

Do not delete committed migration files to resolve conflicts. Create a corrective migration or a migration merge.

## Run a Command in the Web Container

Replace `COMMAND` with the command and arguments you need:

```bash
docker compose run --rm web COMMAND
```

## Create a New App

Create the destination directory first. For example, to add a `settlement` app under `src`:

```bash
mkdir src/settlement
docker compose run --rm web python manage.py startapp settlement src/settlement
```

Set the generated app configuration's `name` to `src.settlement` and register it in `INSTALLED_APPS`.

## Important Development Commands

| Command | Purpose |
| --- | --- |
| `docker compose run --rm web python manage.py check` | Run Django's static system checks. |
| `docker compose run --rm web python manage.py makemigrations video` | Create migrations after Video model changes. |
| `docker compose run --rm web python manage.py migrate` | Apply committed migrations. |
| `docker compose run --rm web python manage.py showmigrations` | Display migration state. |
| `docker compose run --rm web pre-commit run --all-files` | Run configured quality hooks. |

## Code Quality

The pre-commit configuration includes Ruff linting/formatting, repository safety checks, pyupgrade, django-upgrade, yesqa, and djLint.

Install the Git hook:

```bash
docker compose run --rm web pre-commit install
```

To clear cached hook environments when troubleshooting:

```bash
docker compose run --rm web pre-commit clean
```

Run the configured hooks:

```bash
docker compose run --rm web pre-commit run --all-files
```

Important current limitation: `.pre-commit-config.yaml` excludes `src/video/` and migration paths. Changes in those paths require deliberate manual review because the configured hooks do not check them.

## Testing

Development requirements include pytest and pytest-django. The repository currently contains checked-in user and notification test suites; it does not contain an active Video application test suite in `src/video/tests.py`.

Run all discoverable tests:

```bash
docker compose run --rm web pytest -vv
```

Run one checked-in test file:

```bash
docker compose run --rm web pytest src/users/test/test_views.py -vv
```

Run one test node:

```bash
docker compose run --rm web pytest src/users/test/test_views.py::TestUserListTestCase::test_post_request_with_valid_data_succeeds -vv
```

## Development Workflow

1. Pull the current development base and create a feature branch.
2. Implement the change in the appropriate view, serializer, service, or model layer.
3. Create and review a migration if the schema changed.
4. Add or update tests for the changed behavior.
5. Run the relevant tests and configured quality hooks.
6. Manually review Video changes because the current pre-commit exclusions skip `src/video/`.
7. Review the complete diff, then commit, push, and open a pull request.

## Production Considerations

`src.config.production` configures S3-backed media/static storage, and the repository includes Swarm, static-image, Nginx, and deploy files. Those files contain environment-specific image names or placeholders and are not a turnkey deployment.

Before production use:

- use a strong externally managed Django secret key and disable debug mode;
- configure the target PostgreSQL and storage credentials securely;
- restrict allowed hosts, CORS, and Video endpoint permissions;
- use a production WSGI server and reverse proxy;
- decide how in-process post-upload jobs should behave across restarts and multiple web replicas; and
- validate backup, media retention, observability, and rollback procedures.

## Troubleshooting

### Database connection failures

Inside Compose, use `DB_HOST=db`. Confirm the remaining `DB_*` values match the PostgreSQL service configuration and inspect `docker compose logs db`.

### Migration conflicts

Run `showmigrations`, inspect the conflicting dependency graph, and create a merge or corrective migration. Do not remove migration history that may already be applied elsewhere.

### Upload processing failures

Check web logs for TRK validation, FFmpeg/ffprobe, file-path, or bulk-insert errors. The Docker image includes FFmpeg and the native libraries needed by the upload path.

### Dependency changes are missing

Rebuild the web image after modifying requirement files or the Dockerfile:

```bash
docker compose up -d --build web
```

### API schema differs from this guide

Inspect current action decorators, serializers, Swagger UI, and [API Documentation](API_DOCUMENTATION.md). Source code and generated schemas are authoritative.
