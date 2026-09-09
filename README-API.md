# APT Utility API Guide

This guide explains which endpoints are available and when a client should use each one. It intentionally summarizes request and response shapes; use the generated Swagger documentation for the complete field-level schema.

## API Access

| Resource | URL |
| --- | --- |
| API base URL | `http://localhost:8002/api/v1/` |
| Swagger UI | <http://localhost:8002/swagger/> |
| ReDoc | <http://localhost:8002/redoc/> |
| Health check | <http://localhost:8002/health/> |
| Django admin | <http://localhost:8002/admin/> |

Replace `localhost:8002` with the deployed host when calling another environment. All paths below are relative to `/api/v1/` and require a trailing slash.

## Authentication

The API supports session authentication and JWT bearer tokens.

```http
Authorization: Bearer <access-token>
```

| Method | Path | Why you use it | Main input |
| --- | --- | --- | --- |
| `POST` | `token/` | Exchange user credentials for access and refresh tokens. | `username`, `password` |
| `POST` | `token/refresh/` | Obtain a new access token without signing in again. | `refresh` |
| `POST` | `password_reset/` | Start the password-reset workflow. | Email address |

Password-reset confirmation and validation routes are provided under `password_reset/`; see Swagger for the installed package's exact schemas.

> **Current access policy:** video/project endpoints explicitly use `AllowAny`. File upload requires authentication. User creation is public; a user may update only their own user object. Review these permissions before exposing the API publicly.

## Users and General Files

| Method | Path | Why you use it | Main input |
| --- | --- | --- | --- |
| `POST` | `users/` | Register a user and receive that user's tokens. | `username`, `password`, `email`, optional name/profile fields |
| `GET` | `users/{user_id}/` | Read one user's public profile fields. | User UUID in the path |
| `PUT`, `PATCH` | `users/{user_id}/` | Update the signed-in user's profile. | Fields to update |
| `GET` | `users/me/` | Get the profile associated with the current token/session. | Valid authentication |
| `POST` | `files/` | Upload a general file owned by the current user. | `multipart/form-data` with `file` |

There is no user-list, user-delete, file-list, file-detail, or file-delete endpoint in the current view sets.

### Common paths that are not exposed

| Path | Present? | Use instead |
| --- | --- | --- |
| `api/v1/videos/upload/` | No; the old route is commented out. | `POST api/v1/videos/project-upload/` |
| `api/v1/frame/` | No. | `GET api/v1/videos/frame/` |
| `api/v1/users/` with `GET` | No user-list action. | Retrieve `users/{user_id}/` or `users/me/` |
| `api/v1/files/` with `GET` | No file-list action. | Only `POST api/v1/files/` is exposed |
| `api/v1/notifications/` | No API router is registered. | Notification code is internal |
| `api/v1/social/` | No API router is registered. | Browser account routes are under `/accounts/` |

## Project Lifecycle

Use the dedicated project actions for the normal upload and deletion workflow because they handle video/TRK processing and associated files.

| Method | Path | Why you use it | Main input |
| --- | --- | --- | --- |
| `POST` | `videos/project-upload/` | Create a project from a video and APT tracking file, then import its tracking data. | Multipart: `project_name`, `video_file`, `tracking_file` |
| `GET` | `videos/project-list/` | Show active and completed projects in a project picker. | Query: optional `page` (default `1`), `page_size` (default `18`, max `100`) |
| `DELETE` | `videos/{project_id}/delete-project/` | Delete a project, related database rows, source files, and exports. | Project ID in the path |

For example, `GET videos/project-list/?page=2&page_size=10` returns the project records in `data` and a
`pagination` object containing `current_page`, `page_size`, `total_pages`, `total_items`, `next`, and `previous`.
Each project also contains `last_updated`, the latest `ActivityLog.activity_updated_at` datetime in the API's
configured ISO-style UTC format, or `null` when the project has no activity.
`active_object_count` contains the number of distinct related object IDs whose track `object_status` is `1`.

The `VideoViewSet` also exposes standard router-generated project CRUD routes:

| Method | Path | Present? | Guidance |
| --- | --- | --- | --- |
| `GET`, `POST` | `videos/` | Yes | List/create `Project` records. Prefer `project-upload/` when importing real project files. |
| `GET`, `PUT`, `PATCH`, `DELETE` | `videos/{project_id}/` | Yes | Standard project record operations. Prefer `delete-project/` for complete file cleanup. |

## Video and TRK Delivery

| Method | Path | Why you use it | Main input |
| --- | --- | --- | --- |
| `GET` | `videos/{project_id}/project-stream/` | Play or seek through the project's video without loading the whole file into memory. | Optional HTTP `Range` header |
| `GET` | `videos/{project_id}/project-stream-trk/` | Download the original tracking file associated with a project. | Project ID in the path |
| `POST` | `videos/export-trk/` | Generate a versioned `.trk` file from the project's current edited database state. | JSON: `project_id` |

Video streaming returns `206 Partial Content` when a valid `Range` header is supplied and `200 OK` for a full stream. TRK export returns a version number and download URL.

## Frame and Object Data

These endpoints are read-only and support the editor's timeline, canvas, and object selectors.

| Method | Path | Why you use it | Main input |
| --- | --- | --- | --- |
| `GET` | `videos/frame/` | Load tracking data for one frame. If it is missing, use the nearest earlier stored frame. | Query: `video`, `frame` |
| `GET` | `videos/{project_id}/frame-object-range/` | Load object coordinates for a frame window, with earlier-frame fallback for missing frames. | Query: `start`, `end`; maximum 900 frames |
| `GET` | `videos/{project_id}/frame-object-range-no-fallback/` | Load only the data actually stored in a frame window; useful when fallback would hide gaps. | Query: `start`, `end`; maximum 900 frames |
| `GET` | `videos/{project_id}/unique-ids/` | Populate an object list with each active trajectory's range and tracking coverage. | Optional paired query: `start_frame`, `end_frame` |
| `GET` | `videos/{project_id}/unique-ids/{object_id}/` | Check an object's lifecycle and whether a selected frame belongs to it. | Required query: `frame` |
| `GET` | `videos/{project_id}/frame-timeline/` | Build compact presence/coordinate data for timeline rendering. | Query: `start`, `end`; optional comma-separated `object_ids` |

### Compressed responses

- `frame-object-range-no-fallback/` returns gzip-compressed JSON encoded as a hexadecimal string in `data`, with `compressed: true`.
- `frame-timeline/` returns zlib-compressed bytes as `application/octet-stream` by default. Add `debug=true` to receive readable JSON during development.

## Object Editing

These endpoints change tracking data and create operation snapshots used by undo/redo where implemented.

| Method | Path | Why you use it | Main input |
| --- | --- | --- | --- |
| `PUT` | `videos/{project_id}/link-objects/` | Join two trajectories, resolve overlapping tracks, or bulk-link multiple trajectories. | Pair fields for `link`/`overlap`; `operation: "bulk_link"` with an `objects` list for bulk linking |
| `POST` | `videos/{project_id}/objects/break/` | Split one trajectory at a selected frame. | `object_id`, `break_frame`; query `break_type=before|after` |
| `POST` | `videos/{project_id}/clip-object/` | Move a selected interval from one trajectory into a newly assigned object ID. | `object_id`, `start_frame`, `end_frame` |
| `PUT` | `videos/{project_id}/swap-objects/` | Exchange two object identities from a selected frame onward. | `object_1_id`, `object_2_id`, `current_frame` |
| `POST` | `videos/{project_id}/objects/delete/` | Soft-delete an object's active rows over a selected interval and deactivate its track. | `object_id`, `start_frame`, `end_frame` |
| `POST` | `videos/{project_id}/interpolate-trajectory/` | Fill missing trajectory coordinates between known frames. | One of the interpolation modes described below |

### Link modes

For a normal link, send:

```json
{
  "object_1_id": 10,
  "object_1_start": 1,
  "object_1_end": 100,
  "object_2_id": 20,
  "object_2_start": 101,
  "object_2_end": 200,
  "operation": "link"
}
```

For overlapping trajectories, set `operation` to `overlap` and set `preferred_object` to either `object_1_id` or `object_2_id`.

### Bulk linking trajectories

`PUT /api/v1/videos/{project_id}/link-objects/`

Existing `link` (the default) and `overlap` requests retain their existing fields and strategies. No migration or new endpoint is required.

#### Request

```json
{
  "operation": "bulk_link",
  "objects": [
    {"object_id": 10, "start_frame": 1, "end_frame": 100},
    {"object_id": 20, "start_frame": 101, "end_frame": 200},
    {"object_id": 30, "start_frame": 201, "end_frame": 300}
  ]
}
```

Ranges are inclusive. Supply at least two unique object IDs. Each must have exactly one active track in the requested project, a selected range inside its lifecycle, and active frame data in that range. Selection order does not matter: the earliest selected start frame determines the surviving object. No new object is created.

#### Success: HTTP 200

Example for project 1 with one FrameObject per frame:

```json
{
  "status": "success",
  "message": "Objects bulk linked successfully",
  "data": {
    "status": "success",
    "message": "Objects bulk linked successfully",
    "video_id": 1,
    "master_object_id": 10,
    "merged_object_ids": [20, 30],
    "deactivated_object_ids": [20, 30],
    "rows_updated_main_table": 200,
    "start_frame": 1,
    "end_frame": 300
  }
}
```

#### Errors: HTTP 400

Missing fields, fewer than two objects, duplicate IDs, missing/foreign/inactive/ambiguous tracks, invalid ranges, and empty active selections use the existing envelope:

```json
{
  "status": "error",
  "message": "Invalid input data",
  "errors": {"objects": ["Object IDs must be unique."]}
}
```

Overlapping selected ranges use a dedicated response. Every overlapping pair is included, with numeric IDs and frame boundaries:

```json
{
  "success": false,
  "code": "TRAJECTORY_OVERLAP",
  "message": "Bulk linking cannot be performed because some selected trajectories have overlapping frames.",
  "overlaps": [
    {"object_1_id": 10, "object_2_id": 20, "overlap_start": 80, "overlap_end": 100}
  ]
}
```

Touching inclusive boundaries overlap. Adjacent ranges such as 1–100 and 101–200 do not. No automatic overlap resolution occurs. Invalid selections and overlaps leave frame ownership, tracks, and history unchanged.

#### Workflow and reuse

1. `LinkObjectSerializer` validates request shape, project, uniqueness, and range ordering. Legacy pair validation remains on its existing path.
2. `BulkLinkStrategy` enters one atomic transaction, locks the project and active selected tracks, and validates current lifecycle bounds and active frame availability.
3. `BulkLinkOverlapValidator` checks all selected range pairs before any mutation.
4. `SnapshotBuilder.build` captures compact FrameObject ownership (`id`, `object_id`) and track states. Affected frame rows are locked, and the same row IDs are used for both snapshots.
5. `NormalLinkMutator.execute_mutations` merges each source into the earliest selected object. Shared lifecycle recalculation retains sources with remaining active frames and uses `ObjectLifecycleService.deactivate_object` only for empty sources. Frame rows are updated in place, retaining payloads and primary keys.
6. `SnapshotLogger.log` records one `bulk_link` activity and one before/after snapshot, reusing redo-history clearing. A failure in any merge or snapshot write rolls back the entire operation.
7. `UndoRedoService._restore_bulk_link` uses existing `_get_snapshot_rows`, `_bulk_update_rows`, and `_bulk_upsert_rows` helpers. Undo restores original ownership and every track state; redo restores the saved after state without validation or master selection. Existing project history controls `is_applied` and operation ordering.
8. Export continues using its existing track rebuild and current database data. No exporter changes were made. Its rebuild can delete empty inactive tracks, so bulk snapshots include track identity and project fields, allowing the existing upsert helper to restore deleted tracks on undo.

##### Partial selections

For single and bulk linking, only active source rows inside the submitted range move. Unselected source frames remain unchanged, and the source stays active while active frames remain. Track bounds are recalculated from actual active frames. A source is deactivated only when no active frames remain. Submit full lifecycles to combine entire trajectories. Both selected-range overlaps and collisions with existing master data outside its selected range are rejected before mutation. Overlap merging also processes only the submitted source range: preferred-object data wins within the selected overlap, and collisions outside that overlap are rejected.

#### Files changed

| File | Reason |
| --- | --- |
| `src/video/serializers.py` | Nested bulk request serializer and conditional required fields; retain legacy validation. |
| `src/video/services/link_object_service.py` | Bulk strategy, complete overlap detection, structured error, and service dispatch; reuse the existing normal mutator. |
| `src/video/services/undo_redo_service.py` | Bulk snapshot replay using existing batch update/upsert helpers. |
| `src/video/views.py` | Return typed overlap errors and update endpoint description. |
| `src/video/test_bulk_link.py` | Database, serializer, endpoint, rollback, history, legacy-link/overlap, and export database-selection regression tests. |
| `README-API.md` | API, workflow, reuse, partial-range semantics, and verification documentation. |

#### Verification

Run in the configured backend development environment:

```sh
python manage.py test src.video.test_bulk_link
```

Tests include row/payload preservation, chronological master selection, single history entry, exact undo/redo restoration, all overlapping pairs, inclusive boundaries, invalid selections, missing/foreign/inactive/empty tracks, late mutation failure, snapshot failure, redo-history clearing, partial-range behavior, export rebuild across undo/redo, legacy link/overlap round trips, and endpoint responses.

Syntax parsing and `git diff --check` passed during implementation. Database tests could not run: available Python environments lacked Django and dependency download permission was declined. Export tests exercise the real database rebuild and active-track selection, not final TRK file serialization. PostgreSQL concurrency behavior also remains unverified.

### Linking correctness and frontend handoff

All linking modes reject duplicate active frame rows in the destination trajectory or selected source range before mutation. The error identifies the object and first duplicate frame. Inactive rows are excluded from this check; existing corrupted data is not automatically deleted.

Backend linking now validates active tracks and current ranges inside a project-locked transaction. Single link rejects selected-range overlap and actual destination collisions. Bulk linking validates all selections before mutation and creates one history entry. Overlap honours submitted ranges and `preferred_object`, reports moved and deleted row counts separately, and retains unselected source frames. Inactive FrameObject rows are not moved.

Undo/redo selection uses the same project lock as linking and track rebuilding. New single-link and overlap snapshots contain complete track identities, so undo can recreate tracks removed by export rebuild. Overlap redo also handles an empty after-frame snapshot (all source rows deleted). Legacy snapshots remain readable; already-deleted tracks missing identity fields in older snapshots cannot be reconstructed from those snapshots alone. These fixes do not repair data previously corrupted by incorrect linking.

Frontend team action items (backend endpoint remains unchanged):

| Area | Required change |
| --- | --- |
| `src/lib/api/linkObjects.ts` | Extend the request type to a union: pair payload for `link`/`overlap`, and `{operation: "bulk_link", objects: [...]}` for bulk. Substitute the actual numeric project ID in the URL. |
| `Sidebar.tsx` success handler | Read `response.data`. For single link, select `object_track_object_1`; for overlap, select `winner_object`; for bulk, use `master_object_id` and returned bounds. Do not assume object 1 survives overlap. |
| Automatic interpolation | Use the returned surviving ID and bounds, after a successful link. Never interpolate the deactivated loser. |
| Active-object count | Refetch authoritative state. Do not always decrement by one: partial sources can remain active. Bulk also returns `deactivated_object_ids`; `merged_object_ids` lists processed sources, including partial sources that remain active. |
| Bulk selection | Enable linking of more than two objects and send one bulk request. Do not loop over pair requests, because that creates separate history entries and allows partial completion. |
| Overlap choice | Require an explicit preferred object for `overlap`. Do not silently retry failed bulk/single linking as overlap. |
| Error display | Parse error JSON instead of showing the raw response text. Display `message` and field errors; for `TRAJECTORY_OVERLAP`, display the object pairs and inclusive overlap boundaries. |
| Refresh | Refetch tracks, timeline, active count, and history after link/undo/redo. Treat server-returned lifecycle bounds and statuses as authoritative. |

Frontend acceptance checks: choose object 2 as overlap winner; link a partial source that remains active; bulk-link three full tracks with one undo entry; reject overlapping bulk selections without changing UI state; undo and redo a completely overlapping merge.

Backend regression command remains `python manage.py test src.video.test_bulk_link`. Added coverage checks single-link overlap rejection, master collisions outside selection, partial ranges, inactive rows/tracks, both preferred-object choices, complete-deletion overlap redo, rollback after deletion, and export followed by undo. Runtime execution is still unverified because Django is unavailable locally and container access was declined. No frontend code was changed as part of this backend correction.

### Interpolation modes

The interpolation endpoint accepts either:

- `object_id`, `start_frame`, and `end_frame` to discover and fill every internal gap in that range; or
- `source_object_id`, `source_end_frame`, `target_object_id`, and `target_start_frame` to fill one specific gap between two known endpoints.

Interpolation is limited by the service to gaps of at most 10 frames.

## Trajectory Review and Suggestions

These endpoints help a reviewer find suspicious or incomplete tracking data. They do not apply edits automatically.

| Method | Path | Why you use it | Main input |
| --- | --- | --- | --- |
| `GET` | `videos/{project_id}/next-break/` | Jump from the current frame to the next continuous gap in one trajectory. | Query: `object_id`, `current_frame` |
| `POST` | `videos/{project_id}/trajectory-linking-suggestions/` | Rank other objects that are likely continuations across an internal gap. | `object_id`, `break_start`, `break_end`, optional `limit` (max 20) |
| `POST` | `videos/{project_id}/trajectory-clip-suggestions/` | Find movement spikes that may indicate an interval should be clipped into another object. | `object_id`; optional `start_frame`, `end_frame`, `limit` |
| `GET` | `videos/{project_id}/trajectory-gaps/` | Find the largest and other significant missing-frame gaps for one object. | Query: `object_id`; optional `min_gap`, `limit` |
| `GET` | `videos/{project_id}/trajectory-lengths/` | List active trajectories by frame-span length to find unusually short or long tracks. | Optional `ordering`, `min_length`, `max_length` |

`ordering` accepts `length_desc` (default) or `length_asc`. A trajectory's length is its inclusive span from `first_frame` to `last_frame`; it is not a physical distance measurement.

## Confusion and Timeline Review

| Method | Path | Why you use it | Main input |
| --- | --- | --- | --- |
| `GET` | `videos/{project_id}/confusion-table/` | Review frames where object matching is uncertain or crowded. | Optional filters described below |

Useful confusion-table query parameters:

| Parameter | Purpose |
| --- | --- |
| `start`, `end` | Limit results to a frame range; the range may not exceed 5,000 frames. |
| `event_type` | Filter to one confusion-event type. |
| `is_crowded`, `is_forward` | Filter Boolean event flags. |
| `current_object_id` | Show events for one tracked object. |
| `min_score` | Return confusion scores at or above a threshold. |
| `ordering` | Sort by frame, uncertainty, confusion score, or creation time; prefix with `-` for descending order. |
| `status=true` | Return only the project's confusion-calculation status. |
| `recalculate=true` | Start an in-process recalculation and return immediately. |

Recalculation uses an application thread executor, so its state is tied to the running web process rather than a persistent worker queue.

## Activity History and Undo/Redo

| Method | Path | Why you use it | Main input |
| --- | --- | --- | --- |
| `POST` | `videos/add-activity-log/` | Record a client-defined operation in the project's audit history. | `project_id`, `operation`, `objects_data` containing `objects` |
| `GET` | `videos/activity/logs/` | Display applied activity history and available undo/redo counts. | Query: `video_id` |
| `GET` | `videos/activity/logs/export/` | Download currently applied activity records as CSV. | Query: `project_id` |
| `POST` | `videos/undo/` | Revert the latest applied snapshot for a project. | JSON: `project_id` |
| `POST` | `videos/redo/` | Reapply the latest reverted snapshot for a project. | JSON: `project_id` |

Undo and redo are collection actions: the project ID belongs in the JSON body, not in the URL.

## Typical Client Workflows

### Open and inspect a project

1. Call `videos/project-list/` and let the user select a project.
2. Stream media from `videos/{project_id}/project-stream/`.
3. Load visible objects with `frame-object-range/` or `frame-timeline/`.
4. Use `unique-ids/`, `trajectory-gaps/`, and `confusion-table/` to navigate tracking issues.

### Correct and export tracking data

1. Apply an object edit such as link, break, clip, swap, delete, or interpolation.
2. Refresh the affected frame range and activity log.
3. Use `undo/` or `redo/` when the edit needs to be reverted or restored.
4. Call `export-trk/` to create a versioned tracking file from the current state.

## Response and Error Conventions

Most JSON actions use a status envelope:

```json
{
  "status": "success",
  "data": {}
}
```

Validation failures commonly use:

```json
{
  "status": "error",
  "message": "Invalid input data",
  "errors": {}
}
```

| HTTP status | Meaning |
| --- | --- |
| `200` | Successful read or operation |
| `201` | Resource created |
| `206` | Partial video content returned for a range request |
| `400` | Missing, invalid, or inconsistent input |
| `401`, `403` | Authentication or permission failure |
| `404` | Project, frame, or file was not found |
| `500` | Unexpected processing error |

Streaming, file-download, CSV, and compressed-data endpoints return their documented binary content rather than the normal JSON envelope.

## Documentation Scope

This inventory is based on the active root URLs, DRF routers, view sets, action decorators, serializers, and services in the repository. Commented-out routes are not included. Swagger and the current source code are authoritative if this guide becomes out of date.
