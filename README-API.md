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
| `PUT` | `videos/{project_id}/link-objects/` | Join two object trajectories, including overlapping tracks when one object should win. | Two object IDs and their start/end ranges; optional `operation`, `preferred_object` |
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
