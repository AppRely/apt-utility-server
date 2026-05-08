```markdown
# Video Tracking API – Reference

**Base URL**: `/api/v1/`  
**Authentication**: Currently `AllowAny` (open). Replace permission class in production.  
**Content-Type**: `application/json` (except file uploads use `multipart/form-data`).

All endpoints return a consistent error envelope on failure:

```json
{
  "status": "error",
  "message": "Description",
  "errors": {}   // optional validation details
}
```

---

## Endpoints

### 1. Project Management

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/videos/project-upload/` | Upload video + TRK file |
| GET | `/videos/project-list/` | List all active/completed projects |
| DELETE | `/videos/{id}/delete-project/` | Delete project and all associated files |

#### `POST /videos/project-upload/`

**Request**: `multipart/form-data`

| Field | Type | Description |
|-------|------|-------------|
| `video` | file | MP4 video file |
| `trk_file` | file | TRK tracking file |
| `project_name` | string | Optional project name |

**Response** `201 Created`:

```json
{
  "status": "success",
  "message": "Files saved and TRK data inserted successfully",
  "data": {
    "project_id": 42,
    "rows_inserted": 12345
  }
}
```

#### `GET /videos/project-list/`

**Response** `200 OK`:

```json
{
  "status": "success",
  "data": [
    {
      "project_id": 42,
      "project_name": "My Project",
      "project_status": "inprogress",
      "video_name": "video.mp4",
      "trk_file_name": "data.trk"
    }
  ]
}
```

#### `DELETE /videos/{id}/delete-project/`

**Response** `200 OK`:

```json
{
  "status": "success",
  "message": "Project deleted successfully"
}
```

---

### 2. Video & TRK Streaming

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/videos/{id}/project-stream/` | Stream video with HTTP Range support |
| GET | `/videos/{id}/project-stream-trk/` | Download raw TRK file (attachment) |

#### `GET /videos/{id}/project-stream/`

**Headers**:

- `Range: bytes=<start>-<end>` (optional) – enables partial content.

**Response**:

- `206 Partial Content` when Range header present.
- `200 OK` for full video.

**Example**:

```bash
curl -H "Range: bytes=0-1048575" http://localhost:8000/api/v1/videos/42/project-stream/
```

#### `GET /videos/{id}/project-stream-trk/`

**Response**: `application/octet-stream` attachment with the original TRK file.

---

### 3. Frame Tracking Data

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/videos/{id}/frame-object-range/` | JSON data for frame range (max 150 frames) |
| GET | `/videos/{id}/frame-object-range-no-fallback/` | Gzipped hex data (max 900 frames, no fallback) |
| GET | `/videos/frame/` | Single frame data (falls back to previous valid frame) |

#### `GET /videos/{id}/frame-object-range/`

**Query parameters**:

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `start` | int | yes | Start frame (inclusive) |
| `end` | int | yes | End frame (inclusive), max span = 150 |

**Response** `200 OK`:

```json
{
  "status": "success",
  "data": {
    "objects": {
      "123": {
        "frames": {
          "100": { "x": 120, "y": 340, "width": 45, "height": 78 },
          "101": { "x": 122, "y": 338, "width": 45, "height": 79 }
        }
      }
    }
  }
}
```

#### `GET /videos/{id}/frame-object-range-no-fallback/`

**Query parameters**: same as above, but max span = 900.

**Response** `200 OK` (compressed):

```json
{
  "status": "success",
  "data": "1f8b0800000000000000...",   // gzipped JSON as hex string
  "compressed": true
}
```

**Client decoding example (Python)**:

```python
import gzip, orjson, binascii
compressed_hex = response["data"]
json_bytes = gzip.decompress(binascii.unhexlify(compressed_hex))
data = orjson.loads(json_bytes)
```

#### `GET /videos/frame/`

**Query parameters**:

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `video` | int | yes | Project ID |
| `frame` | int | yes | Frame number |

**Response** `200 OK` (same object structure as range endpoint, but for one frame).

---

### 4. Object Operations

All operations are atomic and logged in the activity trail.

| Method | Endpoint | Description |
|--------|----------|-------------|
| PUT | `/videos/{id}/link-objects/` | Merge `object_2` into `object_1` |
| PUT | `/videos/{id}/swap-objects/` | Swap two objects entirely |
| POST | `/videos/{id}/objects/break/` | Split an object at a given frame |
| POST | `/videos/{id}/objects/delete/` | Nullify an object over a frame range |

#### `PUT /videos/{id}/link-objects/`

**Request body**:

```json
{
  "object_1": 101,
  "object_2": 202
}
```

**Response** `200 OK`:

```json
{
  "status": "success",
  "message": "Objects merged successfully",
  "data": {
    "affected_frames": 350,
    "deleted_object_id": 202
  }
}
```

#### `PUT /videos/{id}/swap-objects/`

**Request body**:

```json
{
  "object_id_1": 101,
  "object_id_2": 202,
  "object_1_start_frame": 100,
  "object_1_end_frame": 500,
  "object_2_start_frame": 600,
  "object_2_end_frame": 900
}
```

**Response** `200 OK`:

```json
{
  "status": "success",
  "message": "Objects swapped successfully"
}
```

#### `POST /videos/{id}/objects/break/`

**Request body**:

```json
{
  "object_id": 55,
  "brake_frame": 422,
  "start_frame": 100,
  "end_frame": 800
}
```

**Response** `200 OK`:

```json
{
  "status": "success",
  "message": "Object break operation completed successfully",
  "data": {
    "new_object_id": 99,
    "split_frame": 422
  }
}
```

#### `POST /videos/{id}/objects/delete/`

**Request body**:

```json
{
  "object_id": 55,
  "start_frame": 100,
  "end_frame": 800
}
```

**Response** `200 OK`:

```json
{
  "status": "success",
  "message": "Object deleted successfully",
  "data": {
    "deleted_frames": 701
  }
}
```

---

### 5. Undo / Redo

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/videos/undo/` | Undo the last operation |
| POST | `/videos/redo/` | Redo the last undone operation |

#### `POST /videos/undo/`

**Request body**:

```json
{
  "project_id": 42
}
```

**Response** `200 OK`:

```json
{
  "status": "success",
  "message": "Undo operation completed successfully",
  "data": { /* restored state details */ }
}
```

#### `POST /videos/redo/`

**Request body**:

```json
{
  "project_id": 42
}
```

**Response** `200 OK`:

```json
{
  "status": "success",
  "message": "Redo operation completed successfully",
  "data": { /* redone state details */ }
}
```

---

### 6. TRK Export & Activity Log

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/videos/export-trk/` | Generate a new versioned TRK file from current database state |
| POST | `/videos/add-activity-log/` | Manually add an audit log entry |
| GET | `/videos/activity/logs?video_id=ID` | Retrieve all activity logs for a video |

#### `POST /videos/export-trk/`

**Request body**:

```json
{
  "project_id": 42
}
```

**Response** `200 OK`:

```json
{
  "status": "success",
  "message": "TRK exported successfully",
  "data": {
    "project_id": 42,
    "trk_version": 3,
    "download_url": "http://localhost/media/trk_exports/42/project_42_v3.trk"
  }
}
```

#### `GET /videos/activity/logs?video_id=42`

**Response** `200 OK`:

```json
{
  "status": "success",
  "data": [
    {
      "id": 1,
      "project_id": 42,
      "operation": "LINK_OBJECTS",
      "details": { "object_1": 101, "object_2": 202 },
      "created_at": "2025-01-15T10:30:00Z"
    }
  ]
}
```

#### `POST /videos/add-activity-log/`

**Request body**:

```json
{
  "project_id": 42,
  "operation": "CUSTOM_ACTION",
  "details": { "key": "value" }
}
```

**Response** `201 Created`:

```json
{
  "status": "success",
  "message": "Activity log entry created",
  "data": { /* serialized log entry */ }
}
```

---

### 7. Utility Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/videos/{id}/unique-ids/` | Retrieve all active object trajectories for a project, including their start frame, end frame, total expected frames, and actual tracked frames. |
| GET | `/videos/{id}/unique-ids/{object_id}/?frame=NUM` | Get start/end frame for an object and verify frame |

#### `GET /videos/{id}/unique-ids/`

**Response** `200 OK`:

```json
{
  "status": "success",
  "data": {
    "project_id": 1,
    "objects": [
      {
        "id": 101,
        "start_frame": 10,
        "end_frame": 50,
        "N_frame": 41,
        "trk_len": 35
      }
    ]
  }
}
```

#### `GET /videos/{id}/unique-ids/{object_id}/?frame=150`

**Response** `200 OK`:

```json
{
  "status": "success",
  "data": {
    "object_id": 101,
    "start_frame": 100,
    "end_frame": 500,
    "is_frame_in_range": true
  }
}
```

---

## Important Limits & Behaviour

| Endpoint | Max frames | Fallback behaviour |
|----------|------------|--------------------|
| `frame-object-range` | 150 | Missing frames filled with previous valid frame |
| `frame-object-range-no-fallback` | 900 | Returns only existing frames (no interpolation) |
| `frame` (single) | N/A | Falls back to previous valid frame |

- Video streaming supports HTTP `Range` header – always returns `Accept-Ranges: bytes`.
- All modifying operations are atomic and recorded in the activity log.
- TRK export is versioned – each export increments the version number for the same project.

---

## Error Codes

| HTTP Status | Meaning |
|-------------|---------|
| 400 | Validation error (missing/invalid parameters, frame range out of bounds) |
| 404 | Project not found, video file missing, or TRK file missing |
| 500 | Internal server error (check logs for details) |

**Example validation error**:

```json
{
  "status": "error",
  "message": "Invalid query parameters",
  "errors": { "end": ["Ensure this value is less than or equal to 150."] }
}
```

---

**API Version**: 1.0 (prefix `/api/v1/`)  
**Contact**: platform team  
**License**: Proprietary
