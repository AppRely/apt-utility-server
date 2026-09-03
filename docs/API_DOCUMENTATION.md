# API Documentation

## Overview

The Video API imports video/TRK projects, serves frame and media data, applies object and trajectory corrections, provides review tools, and exports corrected tracking data. This guide organizes the active Video endpoints by purpose and explains when to use them.

Swagger and ReDoc provide the complete generated request and response schemas.

## Base URL

Local development:

```text
http://localhost:8002/api/v1/
```

All endpoint paths below are relative to `/api/v1/` and use trailing slashes.

## Authentication

The current `VideoViewSet` explicitly uses `AllowAny`, so Video endpoints do not require a token. Authentication-related routes elsewhere in the repository are not part of the active Video workflow and are outside this guide's scope.

Review this access policy before exposing the API publicly.

## API Documentation Tools

| Tool | Local URL |
| --- | --- |
| Swagger UI | <http://localhost:8002/swagger/> |
| ReDoc | <http://localhost:8002/redoc/> |
| Health check | <http://localhost:8002/health/> |

## API Modules

### Project APIs

Use the dedicated upload and delete actions for the complete project lifecycle.

| Method | Endpoint | Why It Is Used | Main Input |
| --- | --- | --- | --- |
| `POST` | `videos/project-upload/` | Save a video and TRK file, create the project, import tracking rows, and build trajectories. | Multipart: `project_name`, `video_file`, `tracking_file` |
| `GET` | `videos/project-list/` | Populate a project selector with active/completed projects. | Query: optional `page` (default `1`), `page_size` (default `18`, max `100`) |
| `DELETE` | `videos/{project_id}/delete-project/` | Remove a project, its related rows, media, and exports. | Project ID in path |

For example, `GET videos/project-list/?page=2&page_size=10` returns the project records in `data` and a
`pagination` object containing `current_page`, `page_size`, `total_pages`, `total_items`, `next`, and `previous`.
Each project also contains `last_updated`, the latest `ActivityLog.activity_updated_at` datetime in the API's
configured ISO-style UTC format, or `null` when the project has no activity.

The `VideoViewSet` is a `ModelViewSet`, so standard router routes are also present:

| Method | Endpoint | Guidance |
| --- | --- | --- |
| `GET`, `POST` | `videos/` | Standard Project list/create. Prefer `project-upload/` for a real video/TRK import. |
| `GET`, `PUT`, `PATCH`, `DELETE` | `videos/{project_id}/` | Standard Project record operations. Prefer `delete-project/` for associated-file cleanup. |

### Video and Tracking-File APIs

| Method | Endpoint | Why It Is Used | Main Input |
| --- | --- | --- | --- |
| `GET` | `videos/{project_id}/project-stream/` | Stream or seek through project video efficiently. | Optional HTTP `Range` header |
| `GET` | `videos/{project_id}/project-stream-trk/` | Download the project's original TRK file. | Project ID in path |
| `POST` | `videos/export-trk/` | Rebuild the current database state into a new versioned TRK file. | JSON: `project_id` |

Video range requests return `206 Partial Content`; full streams return `200 OK`. TRK export reports its version and download URL.

### Frame APIs

| Method | Endpoint | Why It Is Used | Main Input |
| --- | --- | --- | --- |
| `GET` | `videos/frame/` | Load tracking data for one frame, falling back to the nearest earlier frame when needed. | Query: `video`, `frame` |
| `GET` | `videos/{project_id}/frame-object-range/` | Load object data for a frame window with earlier-frame fallback. | Query: `start`, `end`; max 900 frames |
| `GET` | `videos/{project_id}/frame-object-range-no-fallback/` | Load only stored rows so missing tracking frames remain visible. | Query: `start`, `end`; max 900 frames |
| `GET` | `videos/{project_id}/frame-timeline/` | Build compact timeline data for all or selected objects. | Query: `start`, `end`; optional `object_ids`, `debug` |

`frame-object-range-no-fallback/` returns gzip-compressed JSON as a hexadecimal string. `frame-timeline/` returns zlib-compressed bytes by default; set `debug=true` for readable JSON.

### Object APIs

| Method | Endpoint | Why It Is Used | Main Input |
| --- | --- | --- | --- |
| `GET` | `videos/{project_id}/unique-ids/` | List active object IDs and trajectory coverage, optionally within a frame range. | Optional paired `start_frame`, `end_frame` |
| `GET` | `videos/{project_id}/unique-ids/{object_id}/` | Read one object's lifecycle and check whether a frame lies inside it. | Query: `frame` |
| `PUT` | `videos/{project_id}/link-objects/` | Join separate or overlapping object trajectories. | IDs and start/end ranges for both objects |
| `POST` | `videos/{project_id}/objects/break/` | Split one trajectory before or after a frame. | `object_id`, `break_frame`; optional query `break_type` |
| `POST` | `videos/{project_id}/clip-object/` | Move a selected interval into a newly assigned object ID. | `object_id`, `start_frame`, `end_frame` |
| `PUT` | `videos/{project_id}/swap-objects/` | Exchange two identities from a selected frame onward. | `object_1_id`, `object_2_id`, `current_frame` |
| `POST` | `videos/{project_id}/objects/delete/` | Soft-delete one object's rows over a frame interval. | `object_id`, `start_frame`, `end_frame` |

Linking accepts `operation: "link"` by default. For overlapping tracks, use `operation: "overlap"` and set `preferred_object` to one of the two object IDs.

### Trajectory APIs

| Method | Endpoint | Why It Is Used | Main Input |
| --- | --- | --- | --- |
| `POST` | `videos/{project_id}/interpolate-trajectory/` | Fill coordinates across missing internal frames. | Range mode or source/target mode |
| `GET` | `videos/{project_id}/next-break/` | Jump to the next continuous gap after the current frame. | Query: `object_id`, `current_frame` |
| `POST` | `videos/{project_id}/trajectory-linking-suggestions/` | Rank likely object continuations across an internal break. | `object_id`, `break_start`, `break_end`, optional `limit` |
| `POST` | `videos/{project_id}/trajectory-clip-suggestions/` | Find movement spikes that may need clipping. | `object_id`; optional range and `limit` |
| `GET` | `videos/{project_id}/trajectory-gaps/` | Find the largest and other significant frame gaps. | Query: `object_id`; optional `min_gap`, `limit` |
| `GET` | `videos/{project_id}/trajectory-lengths/` | Find unusually short or long active trajectories. | Optional `ordering`, `min_length`, `max_length` |

Interpolation accepts either:

- `object_id`, `start_frame`, `end_frame` to find and fill gaps inside a range; or
- `source_object_id`, `source_end_frame`, `target_object_id`, `target_start_frame` to fill one specific gap.

The interpolation service limits a single gap to 10 frames. Trajectory length is the inclusive frame span, not physical distance. `ordering` accepts `length_desc` or `length_asc`.

### Review and Confusion APIs

| Method | Endpoint | Why It Is Used | Main Input |
| --- | --- | --- | --- |
| `GET` | `videos/{project_id}/confusion-table/` | Review crowded or uncertain object matches and prioritize corrections. | Optional filters |

Useful filters include `start`, `end`, `event_type`, `is_crowded`, `is_forward`, `current_object_id`, `min_score`, and `ordering`. Use `status=true` to read calculation state or `recalculate=true` to start an in-process recalculation. A requested frame range may not exceed 5,000 frames.

### Activity and Export APIs

| Method | Endpoint | Why It Is Used | Main Input |
| --- | --- | --- | --- |
| `POST` | `videos/add-activity-log/` | Store a client-defined operation in project history. | `project_id`, `operation`, `objects_data` |
| `GET` | `videos/activity/logs/` | Display applied history and undo/redo counts. | Query: `video_id` |
| `GET` | `videos/activity/logs/export/` | Download currently applied activity records as CSV. | Query: `project_id` |
| `POST` | `videos/undo/` | Revert the latest applied snapshot. | JSON: `project_id` |
| `POST` | `videos/redo/` | Restore the latest reverted snapshot. | JSON: `project_id` |

Undo and redo are collection actions; the project ID belongs in the request body rather than the URL.

### File APIs

The active Video workflow handles files through:

- `project-upload/` for source video and tracking files;
- `project-stream/` and `project-stream-trk/` for delivery; and
- `export-trk/` and `activity/logs/export/` for generated downloads.

The repository's separate generic file-upload app is not required by the Video workflow and is not documented as a primary API module here.

## Main Request Examples

### Upload a project

```bash
curl -X POST http://localhost:8002/api/v1/videos/project-upload/ \
  -F "project_name=Example Project" \
  -F "video_file=@example.mp4" \
  -F "tracking_file=@example.trk"
```

### Load a frame range

```bash
curl "http://localhost:8002/api/v1/videos/42/frame-object-range/?start=100&end=200"
```

### Link two tracks

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

## Request and Response Format

Most requests and responses use `application/json`. Project uploads use `multipart/form-data`; video, TRK, CSV, and compressed endpoints return their corresponding file or binary content.

Typical success response:

```json
{
  "status": "success",
  "data": {}
}
```

## Error Responses

Typical validation response:

```json
{
  "status": "error",
  "message": "Invalid input data",
  "errors": {}
}
```

| Status | Meaning |
| --- | --- |
| `400` | Missing, invalid, or inconsistent input |
| `404` | Project, frame, or file not found |
| `500` | Unexpected processing error |

## Routes Not Present

| Path | Status | Use Instead |
| --- | --- | --- |
| `api/v1/videos/upload/` | Commented out | `POST api/v1/videos/project-upload/` |
| `api/v1/frame/` | Not registered | `GET api/v1/videos/frame/` |

## Swagger Documentation

Swagger and ReDoc are the authoritative sources for detailed field schemas and generated response definitions:

- Swagger UI: <http://localhost:8002/swagger/>
- ReDoc: <http://localhost:8002/redoc/>

This document is based on the active URL router, `VideoViewSet` actions, serializers, models, and services. Commented-out routes are excluded.
