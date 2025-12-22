import math
import os
import re
import json

import numpy as np
from django.conf import settings
from django.db import transaction
from django.core.files.uploadedfile import UploadedFile
from rest_framework import serializers
from django.db.models import Max

# from .models import Project, Video, VideoData, ObjectTrack, ActivityLog
from .models import Project, VideoFrame, FrameObject, ObjectTrack, ActivityLog, Video
from .TrkFile import Trk
from django.db import transaction
from django.db.models import Case, When, Value, IntegerField
from rest_framework import serializers
from django.db.models import Q
# from .services.object_slot_adapter import ObjectSlotAdapter
# from .services.object_lifecycle_service import ObjectLifecycleService
# from .services.frame_object_range_service import FrameObjectRangeService
# from .services.frame_info_service import FrameInfoService 
from .services.project_upload_service import ProjectUploadService
# from .models import Project, VideoData, ObjectTrack


class VideoSerializer(serializers.ModelSerializer):
    class Meta:
        model = Video
        fields = "__all__"

# =============================
# PROJECT SERIALIZERS
# =============================

class ProjectUploadSerializer(serializers.Serializer):
    """
    Serializer that encapsulates validation + persistence for project uploads.
    Contains all business logic for file handling and TRK parsing.
    """

    project_name = serializers.CharField(max_length=255)
    video_file = serializers.FileField()
    tracking_file = serializers.FileField(write_only=True)

    # BULK_INSERT_CHUNK_SIZE: int = 5000
    # MAX_OBJECT_SLOTS: int = 10

    # def create(self, validated_data: dict) -> dict[str, int]:
    #     project_name = validated_data["project_name"]
    #     video_file = validated_data["video_file"]
    #     trk_file = validated_data["tracking_file"]

    #     # Setup paths
    #     video_folder, track_folder = self._get_media_paths()
    #     video_disk_path = os.path.join(video_folder, os.path.basename(video_file.name))
    #     trk_disk_path = os.path.join(track_folder, os.path.basename(trk_file.name))

    #     project = None

    #     try:
    #         # 1. Save raw files to disk
    #         self._write_file(video_file, video_disk_path)
    #         self._write_file(trk_file, trk_disk_path)

    #         # 2. Parse TRK first BEFORE creating project
    #         trk = Trk(trk_disk_path)

    #         # Validate TRK has usable data
    #         if trk.getframe(trk.T0) is None:
    #             raise ValueError("Upload failed — no TRK data extracted.")

    #         if Project.objects.filter(project_name=project_name).exists():
    #                 raise serializers.ValidationError({
    #                     "project_name": "Project name already exists. Please use a new name."
    #                 })

    #         # 3. Create project ONLY after TRK passes validation
    #         with transaction.atomic():
    #             project = Project.objects.create(
    #                 project_name=project_name,
    #                 video_name=video_file.name,
    #                 video_path=video_disk_path,
    #                 trk_file_name=trk_file.name,
    #                 trk_file_path=trk_disk_path,
    #                 project_status="inprogress",
    #                 status="Completed",   # Directly completed
    #             )

    #             # video_id = project_id
    #             project.video_id = project.project_id
    #             project.save(update_fields=["video_id"])

    #             # 4. Insert TRK Data
    #             rows_inserted = self._persist_trk_data(project.project_id, trk)

    #             if rows_inserted == 0:
    #                 raise ValueError("Upload failed — no TRK frames inserted")

    #             # 5. Insert unique object start/end track data
    #             self._persist_object_tracks(project.project_id, trk)

    #         # ------------------------------------------------------------
    #         #  CORRECT STREAM URL GENERATION FOR PROJECT MODEL
    #         # ------------------------------------------------------------
    #         request = self.context.get("request")

    #         if request:
    #             video_stream_url = request.build_absolute_uri(
    #                 f"/api/v1/videos/{project.project_id}/project-stream/"
    #             )
    #             trk_stream_url = request.build_absolute_uri(
    #                 f"/api/v1/videos/{project.project_id}/project-stream-trk/"
    #             )
    #         else:
    #             video_stream_url = None
    #             trk_stream_url = None

    #         # Save STREAM URLs into DB instead of disk path
    #         project.video_path = video_stream_url
    #         project.trk_file_path = trk_stream_url
    #         project.save(update_fields=["video_path", "trk_file_path"])
    #         # ------------------------------------------------------------

    #         # Return final JSON
    #         return {
    #             "project_id": project.project_id,
    #             "rows_inserted": rows_inserted,
    #             "video_stream_url": video_stream_url,
    #             "trk_stream_url": trk_stream_url,
    #         }

    #     except Exception as exc:
    #         self._cleanup_failed_upload(project, video_disk_path, trk_disk_path)
    #         raise serializers.ValidationError({"detail": f"Upload failed: {str(exc)}"})

    # def _persist_trk_data(self, project_id: int, trk: Trk) -> int:
    #     """
    #     Parses the TRK object and inserts VideoData rows in bulk.
    #     """
    #     bulk_data: list[VideoData] = []
    #     total_rows: int = 0

    #     # =============================================================
    #     # LOAD START/END FRAMES + TRUE OBJECT IDS
    #     # =============================================================
    #     trk_object_ids = np.array(trk.pTrkiTgt).flatten()        # maps index → object ID

    #     # Safely get global frame range
    #     start = int(getattr(trk, "T0", 0))
    #     end = int(getattr(trk, "T1", start))

    #     for frame in range(start, end + 1):
    #         frame_array = trk.getframe(frame)  # Returns (L, D, 1, N) or similar
    #         if frame_array is None:
    #             continue

    #         arr = np.asarray(frame_array)

    #         # Fix dimensions: (L, D, 1, N) -> (L, D, N)
    #         if arr.shape[-2] == 1:
    #             arr = arr.squeeze(axis=-2)

    #         if arr.ndim != 3:
    #             continue

    #         num_objects = arr.shape[-1]
    #         objects_present: list[int] = []
    #         objects_data: dict[int, dict] = {}

    #         # ---- Extract per-object values ----
    #         for obj_id in range(num_objects):
    #             coords = arr[..., obj_id]

    #             # Stricter check: if ANY coordinate is NaN, consider the object invalid for this frame
    #             if np.any(np.isnan(coords)):
    #                 continue

    #             # Extract auxiliary data (Confidence, Tag, Timestamp)
    #             conf = self._extract_aux_data(trk, "pTrkConf", frame, obj_id)
    #             tag = self._extract_aux_data(trk, "pTrkTag", frame, obj_id)
    #             ts = self._extract_aux_data(trk, "pTrkTS", frame, obj_id)

    #             objects_present.append(obj_id)
    #             objects_data[obj_id] = {
    #                 "coordinates": self._sanitize_data(coords.tolist()),
    #                 "confidence": self._sanitize_data(conf),
    #                 "tag": self._sanitize_data(tag),
    #                 "timestamp": self._sanitize_data(ts),
    #             }

    #         if not objects_present:
    #             continue

    #         # ---- Prepare Frame-Wide JSON Arrays ----
    #         all_conf = [objects_data[oid]["confidence"] for oid in objects_present]
    #         all_tag = [objects_data[oid]["tag"] for oid in objects_present]
    #         all_ts = [objects_data[oid]["timestamp"] for oid in objects_present]

    #         # ---- OBJECT SLOTS ----
    #         # Pack the valid objects (up to MAX_SLOTS) into the fixed DB columns.
    #         # Example: If only object_id 1386 exists, it goes into 'object_1_*' slots,
    #         # but 'object_1_id' will store 1386 to preserve identity.
    #         slots = {}
    #         slot_num = 1
    #         for obj_id in objects_present[:self.MAX_OBJECT_SLOTS]:

    #             coords = objects_data[obj_id]["coordinates"]

    #             # TRUE OBJECT ID from TRK (this is CRITICAL)
    #             true_id = int(trk_object_ids[obj_id])
    #             # Assign slot values
    #             slots[f"object_{slot_num}_id"] = true_id
    #             slots[f"object_{slot_num}_coordinates"] = coords

    #             slot_num += 1

    #         # Fill remaining slots with None
    #         for s in range(slot_num, self.MAX_OBJECT_SLOTS + 1):
    #             slots[f"object_{s}_id"] = None
    #             slots[f"object_{s}_coordinates"] = None

    #         # ---- Add to Bulk List ----
    #         bulk_data.append(
    #             VideoData(
    #                 video_id=project_id,
    #                 frame_no=frame,
    #                 frame_timestamp=float(frame),
    #                 trk_timestamp=float(frame),
    #                 confidence=all_conf,
    #                 tag=all_tag,
    #                 timestamp=all_ts,
    #                 **slots,
    #             )
    #         )

    #         # Execute Bulk Insert if Chunk Size Reached
    #         if len(bulk_data) >= self.BULK_INSERT_CHUNK_SIZE:
    #             VideoData.objects.bulk_create(bulk_data)
    #             total_rows += len(bulk_data)
    #             bulk_data = []

    #     # Insert remaining rows
    #     if bulk_data:
    #         VideoData.objects.bulk_create(bulk_data)
    #         total_rows += len(bulk_data)

    #     return total_rows


    # # =====================================================================
    # # INSERT INTO object_track TABLE
    # # =====================================================================
    # def _persist_object_tracks(self, project_id: int, trk: Trk):

    #     # Extract arrays from TRK
    #     trk_start_frames = np.array(trk.startframes).flatten()
    #     trk_end_frames = np.array(trk.endframes).flatten()
    #     trk_object_ids = np.array(trk.pTrkiTgt).flatten()  # TRUE OBJECT IDs

    #     num_objects = len(trk_object_ids)
    #     bulk_tracks = []

    #     # Loop through index values 0...N-1
    #     for idx in range(num_objects):

    #         true_object_id = int(trk_object_ids[idx])      # REAL ID
    #         start_f = int(trk_start_frames[idx])           # frame for this index
    #         end_f = int(trk_end_frames[idx])               # frame for this index

    #         bulk_tracks.append(
    #             ObjectTrack(
    #                 project_id_id=project_id,        # ✔ FK saved correctly
    #                 object_id=true_object_id,     # ✔ Save REAL object ID
    #                 start_frame=start_f,
    #                 end_frame=end_f,
    #                 object_status=1,          # active by default
    #                 operation_note=None,      # no operation yet
    #             )
    #         )

    #     ObjectTrack.objects.bulk_create(bulk_tracks)


    # # =====================================================================
    # # HELPERS
    # # =====================================================================

    # def _sanitize_data(self, data):
    #     """
    #     Recursively replace NaN values with None for JSON compatibility.
    #     """
    #     if data is None:
    #         return None
    #     if isinstance(data, (list, tuple, np.ndarray)):
    #         return [self._sanitize_data(x) for x in data]
    #     if isinstance(data, (float, np.floating)) and np.isnan(data):
    #         return None
    #     return data

    # def _extract_aux_data(self, trk: Trk, attr_name: str, frame: int, obj_id: int) -> list | None:
    #     """
    #     Helper to safely extract auxiliary data (conf, tag, ts) for a specific object/frame.
    #     """
    #     if hasattr(trk, attr_name) and getattr(trk, attr_name) is not None:
    #         data_obj = getattr(trk, attr_name)
    #         val = data_obj.getframe(frame)
    #         val = np.asarray(val)
    #         if val.shape[-2] == 1:
    #             val = val.squeeze(axis=-2)
    #         return np.asarray(val[..., obj_id]).tolist()
    #     return None

    # def _cleanup_failed_upload(self, project: Project | None, video_path: str, trk_path: str):
    #     """
    #     Rollback: Delete DB records and files if processing fails.
    #     """
    #     # 1. Delete VideoData rows (only if project exists)
    #     if project:
    #         VideoData.objects.filter(video_id=project.project_id).delete()

    #     # 2. Delete Files
    #     for path in [video_path, trk_path]:
    #         if os.path.exists(path):
    #             try:
    #                 os.remove(path)
    #             except OSError:
    #                 pass

    #     # 3. Delete Project (only if project exists)
    #     if project:
    #         project.delete()

    # @staticmethod
    # def _write_file(file_obj: UploadedFile, destination: str) -> None:
    #     with open(destination, "wb") as dest:
    #         for chunk in file_obj.chunks():
    #             dest.write(chunk)

    # @staticmethod
    # def _get_media_paths():
    #     media_root = settings.MEDIA_ROOT
        
    #     video_folder = os.path.join(media_root, "video_folder")
    #     track_folder = os.path.join(media_root, "track_folder")

    #     os.makedirs(video_folder, exist_ok=True)
    #     os.makedirs(track_folder, exist_ok=True)

    #     return video_folder, track_folder

    def validate_project_name(self, value):
        if Project.objects.filter(project_name=value).exists():
            raise serializers.ValidationError("Project name already exists.")
        return value

    def create(self, validated_data):
        return ProjectUploadService.create(
            project_name=validated_data["project_name"],
            video_file=validated_data["video_file"],
            tracking_file=validated_data["tracking_file"],
            request=self.context.get("request"),
        )


class ProjectSerializer(serializers.ModelSerializer):
    """
    Serializer for listing projects with essential fields.
    """
    #video_file = serializers.CharField(source='video_name', read_only=True)
    class Meta:
        model = Project
        fields = [
            "project_id",
            "project_name",
            # "video_id",
            "video_name",
            "video_path", 
            "trk_file_name",
            "trk_file_path",
            "project_status",
            "created_at",
            "updated_at",
        ]

# # =============================
# # FRAME SERIALIZERS
# # =============================

# class FrameObjectRangeSerializer(serializers.Serializer):
#     """
#     Serializer to handle fetching object data for a range of frames.
#     """
#     start = serializers.IntegerField(required=True, help_text="Start frame id (inclusive)")
#     end = serializers.IntegerField(required=True, help_text="End frame id (inclusive, max span 150)")
#     video_id = serializers.IntegerField(required=True, help_text="Video ID (passed from view)")

#     def validate(self, attrs):
#         start = attrs.get('start')
#         end = attrs.get('end')
#         video_id = attrs.get('video_id')

#         # Validate that frame numbers are non-negative
#         if start < 0 or end < 0:
#             raise serializers.ValidationError({"start": "Start frame number must be non-negative"})
        
#         # Validate start <= end
#         if start > end:
#             raise serializers.ValidationError({"start": "start must be <= end"})

#         # Validate range does not exceed 900 frames
#         if end - start + 1 > 900:
#             raise serializers.ValidationError({"end": "range cannot exceed 900 frames"})

#         # # Check if video exists in Project table
#         # if not Project.objects.filter(project_id=video_id).exists():
#         #     raise serializers.ValidationError({"video_id": f"Video with ID {video_id} does not exist"})

#         # # Check if start frame exists in VideoData table
#         # if not VideoData.objects.filter(video_id=video_id, frame_no=start).exists():
#         #     raise serializers.ValidationError({"start": f"Start frame {start} not found for video {video_id}"})

#         # # Check if end frame exists in VideoData table
#         # if not VideoData.objects.filter(video_id=video_id, frame_no=end).exists():
#         #     raise serializers.ValidationError({"end": f"End frame {end} not found for video {video_id}"})
        
#         if not Project.objects.filter(project_id=video_id).exists():
#             raise serializers.ValidationError({"video_id": "Invalid video_id"})

#         return attrs

#     def get_data(self):
#         # validated_data = self.validated_data
#         # start_frame = validated_data['start']
#         # end_frame = validated_data['end']
#         # video_id = validated_data['video_id']

#         # # Query DB
#         # qs = VideoData.objects.filter(
#         #     video_id=video_id, 
#         #     frame_no__gte=start_frame, 
#         #     frame_no__lte=end_frame
#         # ).order_by('frame_no')

#         # # Include fallback frames from view if present
#         # if hasattr(self, "extra_frames"):
#         #     qs = qs | VideoData.objects.filter(
#         #         video_id=video_id,
#         #         frame_no__in=self.extra_frames
#         #     )
#         #     qs = qs.order_by("frame_no")

#         # # Aggregate by object
#         # objects_map = {}

#         # for row in qs:
#         #     f_id = row.frame_no
#         #     confs = row.confidence if row.confidence else []
#         #     tags = row.tag if row.tag else []
#         #     timestamps = row.timestamp if row.timestamp else []

#         #     for i in range(1, 11):
#         #         obj_id = getattr(row, f'object_{i}_id')
#         #         coords = getattr(row, f'object_{i}_coordinates')
                        
#         #         if obj_id is not None:
#         #             idx = i - 1
#         #             if obj_id not in objects_map:
#         #                 objects_map[obj_id] = {
#         #                     "object_id": obj_id,
#         #                     "frames": []
#         #                 }
                    
#         #             objects_map[obj_id]["frames"].append({
#         #                 "frame_id": f_id,
#         #                 "coordinates": coords,
#         #                 # "confidence": confs[idx] if idx < len(confs) else None,
#         #                 # "tag": tags[idx] if idx < len(tags) else None,
#         #                 # "timestamp": timestamps[idx] if idx < len(timestamps) else None,
#         #             })

#         data = self.validated_data

#         objects = FrameObjectRangeService.fetch(
#             video_id=data["video_id"],
#             start_frame=data["start"],
#             end_frame=data["end"],
#             extra_frames=getattr(self, "extra_frames", None)
#         )

#         return {
#             "video_id": data["video_id"],
#             "start_frame": data["start"],
#             "end_frame": data["end"],
#             "objects": objects,
#         }


# class FrameInfoSerializer(serializers.Serializer):
#     """
#     Serializer to handle fetching frame information by video ID and frame number.
#     Returns all tracking data for the specified frame.
#     """
#     video = serializers.IntegerField(required=True, help_text="Video ID")
#     frame = serializers.IntegerField(required=True, help_text="Frame number")

#     def validate(self, attrs):
#         video_id = attrs.get('video')
#         frame_num = attrs.get('frame')

#         # Validate that frame number is non-negative
#         if frame_num < 0:
#             raise serializers.ValidationError({"frame": "Frame number must be non-negative"})

#         # Check if video exists in Project table
#         if not Project.objects.filter(project_id=video_id).exists():
#             raise serializers.ValidationError({"video": f"Video with ID {video_id} does not exist"})

#         # Check if frame exists in VideoData table
#         # if not VideoData.objects.filter(video_id=video_id, frame_no=frame_num).exists():
#         #     raise serializers.ValidationError({"frame": f"Frame {frame_num} not found for video {video_id}"})

#         return attrs

#     def get_data(self):
#         """
#         Fetch frame data from the database and return structured response.
#         """
#         # validated_data = self.validated_data
#         # video_id = validated_data['video']
#         # frame_num = validated_data['frame']

#         # # Query the database for the specific frame (already validated to exist)
#         # frame_data = VideoData.objects.get(video_id=video_id, frame_no=frame_num)

#         # # Extract frame-level data
#         # confs = frame_data.confidence if frame_data.confidence else []
#         # tags = frame_data.tag if frame_data.tag else []
#         # timestamps = frame_data.timestamp if frame_data.timestamp else []

#         # # Build objects list
#         # objects = []
#         # for i in range(1, 11):  # 10 object slots
#         #     obj_id = getattr(frame_data, f'object_{i}_id')
#         #     coords = getattr(frame_data, f'object_{i}_coordinates')
            
            
#         #     if obj_id is not None:
#         #         track = ObjectTrack.objects.filter(
#         #         project_id_id=video_id,
#         #         object_id=obj_id
#         #     ).first()

#         #         objects.append({
#         #             "object_id": obj_id,
#         #             "coordinates": coords,
#         #             "start_frame": track.start_frame if track else None,
#         #             "end_frame": track.end_frame if track else None,
#         #             # "confidence": confs[idx] if idx < len(confs) else None,
#         #             # "tag": tags[idx] if idx < len(tags) else None,
#         #             # "timestamp": timestamps[idx] if idx < len(timestamps) else None,
#         #             })

#         data = self.validated_data

#         return FrameInfoService.fetch(
#             video_id=data["video"],
#             frame_no=data["frame"],
#         )
#         # Return structured response
#         # return {
#         #     "video_id": data["video"],
#         #     "frame_number": data["frame"],
#         #     # "frame_timestamp": frame_data.frame_timestamp,
#         #     # "trk_timestamp": frame_data.trk_timestamp,
#         #     # "objects": objects,
#         # }


# class ListUniqueIdsSerializer(serializers.Serializer):
#     def validate(self, data):
#         project_id = self.context.get("project_id")

#         if not project_id:
#             raise serializers.ValidationError({"project_id": "project_id is required"})

#         if not Project.objects.filter(project_id=project_id).exists():
#             raise serializers.ValidationError({"project_id": "Invalid project ID"})

#         return data

#     def get_all_ids(self):
#         project_id = self.context.get("project_id")

#         ids = (
#             ObjectTrack.objects.filter(project_id_id=project_id,object_status=1)
#             .order_by("object_id")
#             .values_list("object_id", flat=True)
#         )

#         return {
#             "project_id": project_id,
#             "unique_ids": list(ids),
#         }


# class ObjectTrackDetailsSerializer(serializers.Serializer):
#     object_id = serializers.IntegerField(required=True)
#     frame = serializers.IntegerField(required=True)

#     def validate(self, data):
#         project_id = self.context.get("project_id")
#         # obj_id = data.get("object_id")
#         frame = data.get("frame")

#         # Validate project exists
#         if not Project.objects.filter(project_id=project_id).exists():
#             raise serializers.ValidationError({"project_id": "Invalid project ID"})

#         # Validate frame >= 0
#         if frame < 0:
#             raise serializers.ValidationError({"frame": "Frame must be >= 0"})

#         # Validate object exists
#         # obj = ObjectTrack.objects.filter(project_id_id=project_id, object_id=obj_id).first()
#         # if not obj:
#         #     # object doesn't exist at all → mark and continue
#         #     data["object_missing"] = True
#         #     return data

#         # pass object row to next method
#         # data["object_row"] = obj

#         return data

#     def get_object_data(self):
#         # project_id = self.context.get("project_id")
#         # obj_id = self.validated_data["object_id"]
#         # frame = self.validated_data["frame"]

#         # row = self.validated_data.get("object_row")

#         # # Case 1 — Object doesn't exist
#         # if self.validated_data.get("object_missing"):
#         #     return {
#         #         "project_id": project_id,
#         #         "object_id": obj_id,
#         #         "message": "Object ID not found in database",
#         #         "is_active": False
#         #     }

#         # # Case 2 — Object exists but inactive
#         # if row.object_status == 0:
#         #     return {
#         #         "project_id": project_id,
#         #         "object_id": obj_id,
#         #         "message": "Object is inactive",
#         #         "is_active": False,
#         #         "operation_note": row.operation_note
#         #     }

#         # # Case 3 — Active: normal logic
#         # is_inside = row.start_frame <= frame <= row.end_frame


#         # return {
#         #     "project_id": project_id,
#         #     "object_id": obj_id,
#         #     "start_frame": row.start_frame,
#         #     "end_frame": row.end_frame,
#         #     "is_inside": is_inside,
#         #     "object_status": row.object_status,
#         #     "operation_note": row.operation_note,
#         # }
#         return ObjectLifecycleService.fetch(
#             project_id=self.context["project_id"],
#             object_id=self.validated_data["object_id"],
#             frame=self.validated_data["frame"],
#         )

# # =============================
# # ACTIVITY SERIALIZERS
# # =============================
# class ActivityLogSerializer(serializers.Serializer):

#     project_id = serializers.IntegerField(required=True)
#     objects_data = serializers.JSONField(required=True)
#     operation = serializers.CharField(max_length=255, required=True)

#     def validate_project_id(self, value):
#         if not Project.objects.filter(project_id=value).exists():
#             raise serializers.ValidationError("Invalid project_id")
#         return value

#     def validate_objects_data(self, value):
#         # -------------------------------
#         # 1. If Swagger sends string → convert to JSON
#         # -------------------------------
#         if isinstance(value, str):
#             try:
#                 value = json.loads(value)
#             except json.JSONDecodeError:
#                 raise serializers.ValidationError("objects_data must be valid JSON.")

#         # -------------------------------
#         # 2. Must be a dict containing "objects"
#         # -------------------------------
#         if not isinstance(value, dict):
#             raise serializers.ValidationError("objects_data must be a JSON object.")

#         if "objects" not in value:
#             raise serializers.ValidationError("objects_data must contain key 'objects'.")

#         objects_list = value["objects"]

#         if not isinstance(objects_list, list):
#             raise serializers.ValidationError("'objects' must be a list.")

#         # -------------------------------
#         # 3. Validate each object
#         # -------------------------------
#         for obj in objects_list:
#             if not isinstance(obj, dict):
#                 raise serializers.ValidationError("Each object must be a dictionary.")

#             required = ["id", "start_frame", "end_frame"]

#             for field in required:
#                 if field not in obj:
#                     raise serializers.ValidationError(f"Object missing '{field}'")

#                 if not isinstance(obj[field], int):
#                     raise serializers.ValidationError(f"'{field}' must be integer.")

#         return value

#     def create(self, validated_data):
#         activity = ActivityLog.objects.create(
#             project_id=validated_data["project_id"],
#             objects_data=validated_data["objects_data"],
#             operation=validated_data["operation"]
#         )

#         return {
#             "activity_id": activity.activity_id,
#             "project_id": activity.project_id,
#             "objects_data": activity.objects_data,
#             "operation": activity.operation,
#             "activity_updated_at": activity.activity_updated_at
#         }


# class ActivityLogRequestSerializer(serializers.Serializer):
#     """
#     Serializer to validate video ID and fetch all Activity Logs based on video_id.
#     """
#     video_id = serializers.IntegerField(required=True, help_text="Video ID")

#     def validate(self, attrs):
#         video_id = attrs.get('video_id')

#         # Video ID must exist in Project table
#         if not Project.objects.filter(video_id=video_id).exists():
#             raise serializers.ValidationError({"video_id": f"Video with ID {video_id} does not exist"})

#         return attrs

#     def get_data(self):
#         """
#         Fetch all activity logs for the given video_id.
#         """
#         video_id = self.validated_data['video_id']

#         # # 1. Get all projects linked to this video
#         # project_ids = list(
#         #     Project.objects.filter(video_id=video_id)
#         #                    .values_list('project_id', flat=True)
#         # )

#         # # 2. Fetch activity logs for these projects
#         # logs = ActivityLog.objects.filter(project_id__in=project_ids)

#         logs = ActivityLog.objects.filter(
#             project_id__in=Project.objects.filter(video_id=video_id).values("project_id")
#         ).order_by("-activity_updated_at")

#         # 3. Structure the response
#         logs_data = []
#         for log in logs:
#             logs_data.append({
#                 "activity_id": log.activity_id,
#                 "project_id": log.project_id,
#                 "objects_data": log.objects_data,
#                 "operation": log.operation,
#                 "activity_updated_at": log.activity_updated_at,
#             })

#         return {
#             "video_id": video_id,
#             "total_logs": len(logs_data),
#             "logs": logs_data
#         }


# # =============================
# # OBJECT OPERATION SERIALIZERS
# # =============================

# class LinkObjectSerializer(serializers.Serializer):
#     object_1_id = serializers.IntegerField(required=True)
#     object_1_start = serializers.IntegerField(required=True)
#     object_1_end = serializers.IntegerField(required=True)

#     object_2_id = serializers.IntegerField(required=True)
#     object_2_start = serializers.IntegerField(required=True)
#     object_2_end = serializers.IntegerField(required=True)

#     # def validate(self, data):
#     #     video_id = self.context.get("video_id")

#     #     if not Project.objects.filter(project_id=video_id).exists():
#     #         raise serializers.ValidationError({"video_id": "Invalid Video ID"})

#     #     if data["object_1_id"] == data["object_2_id"]:
#     #         raise serializers.ValidationError("Object IDs cannot be the same.")

#     #     return data

#     def validate(self, data):
#         video_id = self.context.get("video_id")

#         if not Project.objects.filter(project_id=video_id).exists():
#             raise serializers.ValidationError({"video_id": "Invalid Video ID"})

#         if data["object_2_start"] > data["object_2_end"]:
#             raise serializers.ValidationError({"object_2_range": "Invalid frame range"})

#         if data["object_1_start"] > data["object_1_end"]:
#             raise serializers.ValidationError({"object_1_range": "Invalid frame range"})

#         if data["object_1_id"] == data["object_2_id"]:
#             raise serializers.ValidationError("Object IDs cannot be the same.")

#         if not ObjectTrack.objects.filter(
#             project_id_id=video_id,
#             object_id=data["object_1_id"]
#         ).exists():
#             raise serializers.ValidationError({"object_1_id": "Object 1 not found"})

#         if not ObjectTrack.objects.filter(
#             project_id_id=video_id,
#             object_id=data["object_2_id"]
#         ).exists():
#             raise serializers.ValidationError({"object_2_id": "Object 2 not found"})


#         # try:
#         #     ObjectLifecycleService.get_active_object(video_id, data["object_1_id"])
#         # except ObjectTrack.DoesNotExist:
#         #     raise serializers.ValidationError({"object_1_id": "Active Object 1 not found"})

#         #
#         # try:
#         #     ObjectLifecycleService.get_active_object(video_id, data["object_2_id"])
#         # except ObjectTrack.DoesNotExist:
#         #     raise serializers.ValidationError({"object_2_id": "Active Object 2 not found"})

#         return data

#     # @staticmethod
#     # def _get_object_id_fields():
#     #     return [
#     #         field.name
#     #         for field in VideoData._meta.fields
#     #         if field.name.startswith("object_") and field.name.endswith("_id")
#     #     ]

#     def merge_data(self):
#         """
#         Merge object_2 into object_1:
#         - In VideoData: replace object_2_id with object_1_id in the given frame range.
#         - In ObjectTrack: extend object_1 range, mark object_2 as inactive with a 'link' note.
#         """
#         data = self.validated_data
#         project_id = self.context["video_id"]

#         obj1 = data["object_1_id"]
#         obj2 = data["object_2_id"]
#         start2 = data["object_2_start"]
#         end2 = data["object_2_end"]

#         # object_fields = self._get_object_id_fields()

#         qs = VideoData.objects.filter(
#             video_id=project_id,
#             frame_no__gte=start2,
#             frame_no__lte=end2
#         )

#         with transaction.atomic():
#             # update_map = {
#             #     field: Case(
#             #         When(**{field: obj2}, then=Value(obj1)),
#             #         default=field,
#             #         output_field=IntegerField()
#             #     )
#             #     for field in object_fields
#             # }

#             # rows_updated = qs.update(**update_map)

#             # obj1_row = ObjectTrack.objects.get(
#             #     project_id_id=project_id,
#             #     object_id=obj1
#             # )
#             # obj2_row = ObjectTrack.objects.get(
#             #     project_id_id=project_id,
#             #     object_id=obj2
#             # )

#             update_map = ObjectSlotAdapter.build_bulk_replace_map(
#                 old_object_id=obj2,
#                 new_object_id=obj1,
#             )

#             rows_updated = qs.update(**update_map)

#             obj1_row = ObjectTrack.objects.get(
#                 project_id_id=project_id,
#                 object_id=obj1
#             )
#             obj2_row = ObjectTrack.objects.get(
#                 project_id_id=project_id,
#                 object_id=obj2
#             )
#             #Extend object_1 range
#             obj1_row.start_frame = min(obj1_row.start_frame, obj2_row.start_frame)
#             obj1_row.end_frame = max(obj1_row.end_frame, obj2_row.end_frame)
#             obj1_row.object_status = 1
#             obj1_row.operation_note = "link_target"
#             obj1_row.save(update_fields=[
#                 "start_frame", "end_frame", "object_status", "operation_note"
#             ])

#             # Mark object_2 inactive
#             # obj2_row.object_status = 0
#             # obj2_row.operation_note = f"linked_into_object_{obj1}"
#             # obj2_row.save(update_fields=["object_status", "operation_note"])

#             ObjectLifecycleService.deactivate_object(
#                 obj2_row,
#                 note=f"linked_into_object_{obj1}"
#             )


#         return {
#             "status": "success",
#             "message": "Objects merged successfully",
#             "video_id": project_id,
#             "rows_updated_main_table": rows_updated,
#             "object_track_object_1": {
#                 "object_id": obj1,
#                 "start_frame": obj1_row.start_frame,
#                 "end_frame": obj1_row.end_frame,
#                 "object_status": obj1_row.object_status,
#                 "operation_note": obj1_row.operation_note,
#             },
#             "object_track_object_2": {
#                 "object_id": obj2,
#                 "start_frame": obj2_row.start_frame,
#                 "end_frame": obj2_row.end_frame,
#                 "object_status": obj2_row.object_status,
#                 "operation_note": obj2_row.operation_note,
#             },
#         }


# class BreakObjectSerializer(serializers.Serializer):
#     object_id = serializers.IntegerField(required=True)
#     break_frame = serializers.IntegerField(required=True)

#     # optional (frontend may send)
#     start_frame = serializers.IntegerField(required=False)
#     end_frame = serializers.IntegerField(required=False)

#     # ---------------------------
#     # VALIDATION
#     # ---------------------------
#     def validate(self, data):
#         project_id = self.context["project_id"]
#         object_id = data["object_id"]
#         break_frame = data["break_frame"]

#         # 1️ Project validation
#         if not Project.objects.filter(project_id=project_id).exists():
#             raise serializers.ValidationError("Invalid project_id")

#         # 2️ Active object validation
#         try:
#             obj_track = ObjectLifecycleService.get_active_object(
#                 project_id=project_id,
#                 object_id=object_id
#             )
#         except ObjectTrack.DoesNotExist:
#             raise serializers.ValidationError("Active object not found")

#         # db_start = obj_track.start_frame
#         # db_end = obj_track.end_frame

#         # 3️ Break-frame validation
#         if not (obj_track.start_frame < break_frame < obj_track.end_frame):
#             raise serializers.ValidationError(
#                 f"break_frame must be between {obj_track.start_frame} and {obj_track.end_frame}"
#             )

#         # 4️ Optional frontend validation
#         if "start_frame" in data and data["start_frame"] != obj_track.start_frame:
#             raise serializers.ValidationError("start_frame mismatch with DB")

#         if "end_frame" in data and data["end_frame"] != obj_track.end_frame:
#             raise serializers.ValidationError("end_frame mismatch with DB")

#         # Attach DB info
#         data["obj_track"] = obj_track
#         # data["db_start"] = db_start
#         # data["db_end"] = db_end

#         return data

#     # ---------------------------
#     # DYNAMIC OBJECT SLOT FETCH
#     # ---------------------------
#     # def _get_object_id_fields(self):
#     #     return [
#     #         field.name
#     #         for field in VideoData._meta.fields
#     #         if field.name.startswith("object_") and field.name.endswith("_id")
#     #     ]


#     # ---------------------------
#     # CREATE (ALL BREAK LOGIC)
#     # ---------------------------
#     def create(self, validated_data):
#         project_id = self.context["project_id"]

#         object_id = validated_data["object_id"]
#         break_frame = validated_data["break_frame"]
#         obj_track = validated_data["obj_track"]
#         start_frame = obj_track.start_frame
#         end_frame = obj_track.end_frame

#         with transaction.atomic():

#             # 1️ Generate new object_id
#             # max_id = ObjectTrack.objects.filter(
#             #     project_id_id=project_id
#             # ).aggregate(m=Max("object_id"))["m"] or 0

#             # new_object_id = max_id + 1

#             new_object_id = (
#                 ObjectTrack.objects
#                 .filter(project_id_id=project_id)
#                 .aggregate(m=Max("object_id"))["m"] or 0
#             ) + 1

#             # 2️⃣ Find object slot SAFELY (single DB query)
#             update_map = ObjectSlotAdapter.build_bulk_replace_map_for_range(
#                 #project_id=project_id,
#                 #object_id=object_id,
#                 old_object_id=object_id,
#                 new_object_id=new_object_id,
#                 start_frame=break_frame + 1,
#                 end_frame=end_frame
#             )

#             # if not object_slot:
#             #     raise serializers.ValidationError(
#             #         "Object ID not found in video_data"
#             #     )

#             # 3️ Update video_data (frames AFTER break)
#             rows_updated = VideoData.objects.filter(
#                 video_id=project_id,
#                 frame_no__gt=break_frame,
#                 frame_no__lte=end_frame
#             ).update(**update_map)

#             # 4️ Update old object_track
#             obj_track.end_frame = break_frame
#             obj_track.operation_note = f"break_from_{start_frame}_to_{break_frame}"
#             obj_track.save(update_fields=["end_frame", "operation_note"])

#             # 5️ Create new object_track
#             ObjectTrack.objects.create(
#                 project_id_id=project_id,
#                 object_id=new_object_id,
#                 start_frame=break_frame + 1,
#                 end_frame=end_frame,
#                 object_status=1,
#                 operation_note=f"break_from_{break_frame + 1}_to_{end_frame}"
#             )


#         return {
#             "old_object_id": object_id,
#             "new_object_id": new_object_id,
#             "old_range": f"{start_frame}-{break_frame}",
#             "new_range": f"{break_frame + 1}-{end_frame}",
#             "rows_updated_in_video_data": rows_updated,
#         }


# class SwapObjectSerializer(serializers.Serializer):
#     """
#     Swap two object ids and their occurrences in VideoData + ObjectTrack.
#     """
#     object_1_id = serializers.IntegerField(required=True)
#     object_1_start = serializers.IntegerField(required=True)
#     object_1_end = serializers.IntegerField(required=True)

#     object_2_id = serializers.IntegerField(required=True)
#     object_2_start = serializers.IntegerField(required=True)
#     object_2_end = serializers.IntegerField(required=True)

#     def validate(self, data):
#         video_id = self.context.get("video_id")

#         # Check project/video
#         if not Project.objects.filter(project_id=video_id).exists():
#             raise serializers.ValidationError({"video_id": "Invalid Video ID"})

#         # Cannot swap same ID
#         if data["object_1_id"] == data["object_2_id"]:
#             raise serializers.ValidationError("Object IDs cannot be the same.")

#         # Ensure object tracks exist
#         # if not ObjectTrack.objects.filter(project_id_id=video_id, object_id=data["object_1_id"]).exists():
#         #     raise serializers.ValidationError({"object_1_id": "Object 1 not found"})

#         # if not ObjectTrack.objects.filter(project_id_id=video_id, object_id=data["object_2_id"]).exists():
#         #     raise serializers.ValidationError({"object_2_id": "Object 2 not found"})

#         try:
#             ObjectLifecycleService.get_active_object(video_id, data["object_1_id"])
#         except ObjectTrack.DoesNotExist:
#             raise serializers.ValidationError({"object_1_id": "Active Object 1 not found"})

#         try:
#             ObjectLifecycleService.get_active_object(video_id, data["object_2_id"])
#         except ObjectTrack.DoesNotExist:
#             raise serializers.ValidationError({"object_2_id": "Active Object 2 not found"})


#         if data["object_1_start"] > data["object_1_end"]:
#             raise serializers.ValidationError({"object_1_range": "Invalid range"})

#         if data["object_2_start"] > data["object_2_end"]:
#             raise serializers.ValidationError({"object_2_range": "Invalid range"})

#         return data
    
#     # @staticmethod
#     # def _get_object_id_fields():
#     #     return [
#     #         field.name
#     #         for field in VideoData._meta.fields
#     #         if field.name.startswith("object_") and field.name.endswith("_id")
#     #     ]

    

#     def swap_data(self):
#         data = self.validated_data
#         video_id = self.context["video_id"]

#         obj1 = data["object_1_id"]
#         obj2 = data["object_2_id"]

#         s1, e1 = data["object_1_start"], data["object_1_end"]
#         s2, e2 = data["object_2_start"], data["object_2_end"]

#         SENTINEL = -99999999

#         object_fields = ObjectSlotAdapter.get_object_id_fields()

#         qs1 = VideoData.objects.filter(
#             video_id=video_id,
#             frame_no__gte=s1,
#             frame_no__lte=e1,
#         )

#         qs2 = VideoData.objects.filter(
#             video_id=video_id,
#             frame_no__gte=s2,
#             frame_no__lte=e2,
#         )

#         qs_all = VideoData.objects.filter(video_id=video_id)

#         object_fields = ObjectSlotAdapter.get_object_id_fields()

#         affected_q = Q()
#         for field in object_fields:
#             affected_q |= Q(**{field: obj1}) | Q(**{field: obj2})

#         affected_frames = qs_all.filter(affected_q).count()


#         with transaction.atomic():

            
#             # 1️⃣ obj1 → SENTINEL
#             qs1.update(**{
#                 field: Case(
#                     When(**{field: obj1}, then=Value(SENTINEL)),
#                     default=field,
#                     output_field=IntegerField(),
#                 )
#                 for field in object_fields
#             })

#             # 2️⃣ obj2 → obj1
#             qs2.update(**{
#                 field: Case(
#                     When(**{field: obj2}, then=Value(obj1)),
#                     default=field,
#                     output_field=IntegerField(),
#                 )
#                 for field in object_fields
#             })

#             # 3️⃣ SENTINEL → obj2
#             rows_updated = qs_all.update(**{
#                 field: Case(
#                     When(**{field: SENTINEL}, then=Value(obj2)),
#                     default=field,
#                     output_field=IntegerField(),
#                 )
#                 for field in object_fields
#             })

#             # 4️⃣ Swap ObjectTrack IDs SAFELY
#             obj1_row = ObjectLifecycleService.get_active_object(video_id, obj1)
#             obj2_row = ObjectLifecycleService.get_active_object(video_id, obj2)

#             original_obj1 = obj1_row.object_id
#             original_obj2 = obj2_row.object_id

#             obj1_row.object_id = SENTINEL
#             obj1_row.save(update_fields=["object_id"])

#             obj2_row.object_id = original_obj1
#             obj2_row.operation_note = f"swap_with_object_{original_obj2}"
#             obj2_row.save(update_fields=["object_id", "operation_note"])

#             obj1_row.object_id = original_obj2
#             obj1_row.operation_note = f"swap_with_object_{original_obj1}"
#             obj1_row.save(update_fields=["object_id", "operation_note"])


#         return {
#             "status": "success",
#             "message": "Objects swapped successfully",
#             "video_id": video_id,
#             "rows_updated": affected_frames,
#             "object_track_object_1": {
#                 "original_object_id": obj1,
#                 "current_object_id": obj1_row.object_id,
#                 "start_frame": obj1_row.start_frame,
#                 "end_frame": obj1_row.end_frame,
#                 "object_status": obj1_row.object_status,
#                 "operation_note": obj1_row.operation_note,
#             },
#             "object_track_object_2": {
#                 "original_object_id": obj2,
#                 "current_object_id": obj2_row.object_id,
#                 "start_frame": obj2_row.start_frame,
#                 "end_frame": obj2_row.end_frame,
#                 "object_status": obj2_row.object_status,
#                 "operation_note": obj2_row.operation_note,
#             },
#         }


# class DeleteObjectSerializer(serializers.Serializer):
#     object_id = serializers.IntegerField(required=True)
#     start_frame = serializers.IntegerField(required=True)
#     end_frame = serializers.IntegerField(required=True)

#     # ---------------------------
#     # VALIDATION
#     # ---------------------------
#     def validate(self, data):
#         project_id = self.context["project_id"]
#         object_id = data["object_id"]
#         start_frame = data["start_frame"]
#         end_frame = data["end_frame"]

#         # 1️ Project validation
#         if not Project.objects.filter(project_id=project_id).exists():
#             raise serializers.ValidationError("Invalid project_id")

#         # 2️ Frame range validation
#         if start_frame > end_frame:
#             raise serializers.ValidationError(
#                 "start_frame must be less than or equal to end_frame"
#             )

#         # # 3️ObjectTrack validation (ACTIVE CHECK)
#         # try:
#         #     obj_track = ObjectTrack.objects.get(
#         #         project_id_id=project_id,
#         #         object_id=object_id
#         #     )
#         # except ObjectTrack.DoesNotExist:
#         #     raise serializers.ValidationError("Object not found in object_track")

#         try:
#             obj_track = ObjectLifecycleService.get_active_object(
#                 project_id=project_id,
#                 object_id=object_id
#             )
#         except ObjectTrack.DoesNotExist:
#             raise serializers.ValidationError("Active object not found")

#         # IMPORTANT CHECK
#         # if obj_track.object_status != 1:
#         #     raise serializers.ValidationError(
#         #         "Object is already inactive. No delete operation performed."
#         #     )

#         # Range must lie inside lifecycle
#         if start_frame < obj_track.start_frame or end_frame > obj_track.end_frame:
#             raise serializers.ValidationError(
#                 f"Delete range must be between "
#                 f"{obj_track.start_frame} and {obj_track.end_frame}"
#             )

#         data["obj_track"] = obj_track
#         return data

#     # ---------------------------
#     # DYNAMIC SLOT DISCOVERY
#     # ---------------------------
#     # @staticmethod
#     # def _get_object_id_fields():
#     #     return [
#     #         field.name
#     #         for field in VideoData._meta.fields
#     #         if field.name.startswith("object_") and field.name.endswith("_id")
#     #     ]

#     # ---------------------------
#     # CREATE (OPTIMIZED)
#     # ---------------------------
#     def create(self, validated_data):
#         project_id = self.context["project_id"]

#         object_id = validated_data["object_id"]
#         start_frame = validated_data["start_frame"]
#         end_frame = validated_data["end_frame"]
#         obj_track = validated_data["obj_track"]

#         frames_qs = VideoData.objects.filter(
#             video_id=project_id,
#             frame_no__gte=start_frame,
#             frame_no__lte=end_frame
#         )

#         #object_id_fields = self._get_object_id_fields()
#         # object_id_fields = ObjectSlotAdapter.get_object_id_fields()

#         with transaction.atomic():

#             # # BUILD SINGLE BULK UPDATE MAP
#             # update_map = {}

#             # for field in object_id_fields:
#             #     coord_field = field.replace("_id", "_coordinates")

#             #     update_map[field] = Case(
#             #         When(**{field: object_id}, then=Value(None)),
#             #         default=field,
#             #     )

#             #     update_map[coord_field] = Case(
#             #         When(**{field: object_id}, then=Value(None)),
#             #         default=coord_field,
#             #     )

#             update_map = ObjectSlotAdapter.build_bulk_nullify_map(object_id)

#             # ONE SQL UPDATE
#             affected_frames = frames_qs.update(**update_map)

#             # UPDATE OBJECT TRACK (single row)
#             # obj_track.object_status = 0
#             # obj_track.operation_note = (
#             #     f"deleted_frames_{start_frame}_to_{end_frame}"
#             # )
#             # obj_track.save(update_fields=["object_status", "operation_note"])
#             ObjectLifecycleService.deactivate_object(
#                 obj_track,
#                 note=f"deleted_frames_{start_frame}_to_{end_frame}"
#             )


#         return {
#             "object_id": object_id,
#             "deleted_range": f"{start_frame}-{end_frame}",
#             "frames_affected": affected_frames,
#             "object_status": 0
#         }
