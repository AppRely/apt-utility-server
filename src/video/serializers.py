import math
import os

import numpy as np
from django.conf import settings
from django.db import transaction
from django.core.files.uploadedfile import UploadedFile
from rest_framework import serializers

from .models import Project, Video, VideoData, ObjectTrack
from .TrkFile import Trk


class VideoSerializer(serializers.ModelSerializer):
    class Meta:
        model = Video
        fields = "__all__"


class ProjectUploadSerializer(serializers.Serializer):
    """
    Serializer that encapsulates validation + persistence for project uploads.
    Contains all business logic for file handling and TRK parsing.
    """

    project_name = serializers.CharField(max_length=255)
    video_file = serializers.FileField()
    tracking_file = serializers.FileField(write_only=True)

    BULK_INSERT_CHUNK_SIZE: int = 5000
    MAX_OBJECT_SLOTS: int = 10

    def create(self, validated_data: dict) -> dict[str, int]:
        project_name = validated_data["project_name"]
        video_file = validated_data["video_file"]
        trk_file = validated_data["tracking_file"]

        # Setup paths
        video_folder, track_folder = self._get_media_paths()
        video_disk_path = os.path.join(video_folder, os.path.basename(video_file.name))
        trk_disk_path = os.path.join(track_folder, os.path.basename(trk_file.name))

        project = None

        try:
            # 1. Save raw files to disk
            self._write_file(video_file, video_disk_path)
            self._write_file(trk_file, trk_disk_path)

            # 2. Parse TRK first BEFORE creating project
            trk = Trk(trk_disk_path)

            # Validate TRK has usable data
            if trk.getframe(trk.T0) is None:
                raise ValueError("Upload failed — no TRK data extracted.")

            # 3. Create project ONLY after TRK passes validation
            with transaction.atomic():
                project = Project.objects.create(
                    project_name=project_name,
                    video_name=video_file.name,
                    video_path=video_disk_path,
                    trk_file_name=trk_file.name,
                    trk_file_path=trk_disk_path,
                    project_status="inprogress",
                    status="Completed",   # Directly completed
                )

                # video_id = project_id
                project.video_id = project.project_id
                project.save(update_fields=["video_id"])

                # 4. Insert TRK Data
                rows_inserted = self._persist_trk_data(project.project_id, trk)

                if rows_inserted == 0:
                    raise ValueError("Upload failed — no TRK frames inserted")

                # 5. Insert unique object start/end track data
                self._persist_object_tracks(project.project_id, trk)

            # ------------------------------------------------------------
            #  CORRECT STREAM URL GENERATION FOR PROJECT MODEL
            # ------------------------------------------------------------
            request = self.context.get("request")

            if request:
                video_stream_url = request.build_absolute_uri(
                    f"/api/v1/videos/{project.project_id}/project-stream/"
                )
                trk_stream_url = request.build_absolute_uri(
                    f"/api/v1/videos/{project.project_id}/project-stream-trk/"
                )
            else:
                video_stream_url = None
                trk_stream_url = None

            # Save STREAM URLs into DB instead of disk path
            project.video_path = video_stream_url
            project.trk_file_path = trk_stream_url
            project.save(update_fields=["video_path", "trk_file_path"])
            # ------------------------------------------------------------

            # Return final JSON
            return {
                "project_id": project.project_id,
                "rows_inserted": rows_inserted,
                "video_stream_url": video_stream_url,
                "trk_stream_url": trk_stream_url,
            }

        except Exception as exc:
            self._cleanup_failed_upload(project, video_disk_path, trk_disk_path)
            raise serializers.ValidationError({"detail": f"Upload failed: {str(exc)}"})

    def _persist_trk_data(self, project_id: int, trk: Trk) -> int:
        """
        Parses the TRK object and inserts VideoData rows in bulk.
        """
        bulk_data: list[VideoData] = []
        total_rows: int = 0

        # =============================================================
        # LOAD START/END FRAMES + TRUE OBJECT IDS
        # =============================================================
        trk_object_ids = np.array(trk.pTrkiTgt).flatten()        # maps index → object ID

        # Safely get global frame range
        start = int(getattr(trk, "T0", 0))
        end = int(getattr(trk, "T1", start))

        for frame in range(start, end + 1):
            frame_array = trk.getframe(frame)  # Returns (L, D, 1, N) or similar
            if frame_array is None:
                continue

            arr = np.asarray(frame_array)

            # Fix dimensions: (L, D, 1, N) -> (L, D, N)
            if arr.shape[-2] == 1:
                arr = arr.squeeze(axis=-2)

            if arr.ndim != 3:
                continue

            num_objects = arr.shape[-1]
            objects_present: list[int] = []
            objects_data: dict[int, dict] = {}

            # ---- Extract per-object values ----
            for obj_id in range(num_objects):
                coords = arr[..., obj_id]

                # Stricter check: if ANY coordinate is NaN, consider the object invalid for this frame
                if np.any(np.isnan(coords)):
                    continue

                # Extract auxiliary data (Confidence, Tag, Timestamp)
                conf = self._extract_aux_data(trk, "pTrkConf", frame, obj_id)
                tag = self._extract_aux_data(trk, "pTrkTag", frame, obj_id)
                ts = self._extract_aux_data(trk, "pTrkTS", frame, obj_id)

                objects_present.append(obj_id)
                objects_data[obj_id] = {
                    "coordinates": self._sanitize_data(coords.tolist()),
                    "confidence": self._sanitize_data(conf),
                    "tag": self._sanitize_data(tag),
                    "timestamp": self._sanitize_data(ts),
                }

            if not objects_present:
                continue

            # ---- Prepare Frame-Wide JSON Arrays ----
            all_conf = [objects_data[oid]["confidence"] for oid in objects_present]
            all_tag = [objects_data[oid]["tag"] for oid in objects_present]
            all_ts = [objects_data[oid]["timestamp"] for oid in objects_present]

            # ---- OBJECT SLOTS ----
            # Pack the valid objects (up to MAX_SLOTS) into the fixed DB columns.
            # Example: If only object_id 1386 exists, it goes into 'object_1_*' slots,
            # but 'object_1_id' will store 1386 to preserve identity.
            slots = {}
            slot_num = 1
            for obj_id in objects_present[:self.MAX_OBJECT_SLOTS]:

                coords = objects_data[obj_id]["coordinates"]

                # TRUE OBJECT ID from TRK (this is CRITICAL)
                true_id = int(trk_object_ids[obj_id])
                # Assign slot values
                slots[f"object_{slot_num}_id"] = true_id
                slots[f"object_{slot_num}_coordinates"] = coords

                slot_num += 1

            # Fill remaining slots with None
            for s in range(slot_num, self.MAX_OBJECT_SLOTS + 1):
                slots[f"object_{s}_id"] = None
                slots[f"object_{s}_coordinates"] = None

            # ---- Add to Bulk List ----
            bulk_data.append(
                VideoData(
                    video_id=project_id,
                    frame_no=frame,
                    frame_timestamp=float(frame),
                    trk_timestamp=float(frame),
                    confidence=all_conf,
                    tag=all_tag,
                    timestamp=all_ts,
                    **slots,
                )
            )

            # Execute Bulk Insert if Chunk Size Reached
            if len(bulk_data) >= self.BULK_INSERT_CHUNK_SIZE:
                VideoData.objects.bulk_create(bulk_data)
                total_rows += len(bulk_data)
                bulk_data = []

        # Insert remaining rows
        if bulk_data:
            VideoData.objects.bulk_create(bulk_data)
            total_rows += len(bulk_data)

        return total_rows


    # =====================================================================
    # INSERT INTO object_track TABLE
    # =====================================================================
    def _persist_object_tracks(self, project_id: int, trk: Trk):

        # Extract arrays from TRK
        trk_start_frames = np.array(trk.startframes).flatten()
        trk_end_frames = np.array(trk.endframes).flatten()
        trk_object_ids = np.array(trk.pTrkiTgt).flatten()  # TRUE OBJECT IDs

        num_objects = len(trk_object_ids)
        bulk_tracks = []

        # Loop through index values 0...N-1
        for idx in range(num_objects):

            true_object_id = int(trk_object_ids[idx])      # REAL ID
            start_f = int(trk_start_frames[idx])           # frame for this index
            end_f = int(trk_end_frames[idx])               # frame for this index

            bulk_tracks.append(
                ObjectTrack(
                    project_id_id=project_id,        # ✔ FK saved correctly
                    object_id=true_object_id,     # ✔ Save REAL object ID
                    start_frame=start_f,
                    end_frame=end_f,
                    object_status=1,          # active by default
                    operation_note=None,      # no operation yet
                )
            )

        ObjectTrack.objects.bulk_create(bulk_tracks)


    # =====================================================================
    # HELPERS
    # =====================================================================

    def _sanitize_data(self, data):
        """
        Recursively replace NaN values with None for JSON compatibility.
        """
        if data is None:
            return None
        if isinstance(data, (list, tuple, np.ndarray)):
            return [self._sanitize_data(x) for x in data]
        if isinstance(data, (float, np.floating)) and np.isnan(data):
            return None
        return data

    def _extract_aux_data(self, trk: Trk, attr_name: str, frame: int, obj_id: int) -> list | None:
        """
        Helper to safely extract auxiliary data (conf, tag, ts) for a specific object/frame.
        """
        if hasattr(trk, attr_name) and getattr(trk, attr_name) is not None:
            data_obj = getattr(trk, attr_name)
            val = data_obj.getframe(frame)
            val = np.asarray(val)
            if val.shape[-2] == 1:
                val = val.squeeze(axis=-2)
            return np.asarray(val[..., obj_id]).tolist()
        return None

    def _cleanup_failed_upload(self, project: Project | None, video_path: str, trk_path: str):
        """
        Rollback: Delete DB records and files if processing fails.
        """
        # 1. Delete VideoData rows (only if project exists)
        if project:
            VideoData.objects.filter(video_id=project.project_id).delete()

        # 2. Delete Files
        for path in [video_path, trk_path]:
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass

        # 3. Delete Project (only if project exists)
        if project:
            project.delete()

    @staticmethod
    def _write_file(file_obj: UploadedFile, destination: str) -> None:
        with open(destination, "wb") as dest:
            for chunk in file_obj.chunks():
                dest.write(chunk)

    @staticmethod
    def _get_media_paths():
        media_root = settings.MEDIA_ROOT
        
        video_folder = os.path.join(media_root, "video_folder")
        track_folder = os.path.join(media_root, "track_folder")

        os.makedirs(video_folder, exist_ok=True)
        os.makedirs(track_folder, exist_ok=True)

        return video_folder, track_folder


class FrameObjectRangeSerializer(serializers.Serializer):
    """
    Serializer to handle fetching object data for a range of frames.
    """
    start = serializers.IntegerField(required=True, help_text="Start frame id (inclusive)")
    end = serializers.IntegerField(required=True, help_text="End frame id (inclusive, max span 150)")
    video_id = serializers.IntegerField(required=True, help_text="Video ID (passed from view)")

    def validate(self, attrs):
        start = attrs.get('start')
        end = attrs.get('end')
        video_id = attrs.get('video_id')

        # Validate that frame numbers are non-negative
        if start < 0:
            raise serializers.ValidationError({"start": "Start frame number must be non-negative"})
        
        if end < 0:
            raise serializers.ValidationError({"end": "End frame number must be non-negative"})

        # Validate start <= end
        if start > end:
            raise serializers.ValidationError({"start": "start must be <= end"})

        # Validate range does not exceed 900 frames
        if end - start + 1 > 900:
            raise serializers.ValidationError({"end": "range cannot exceed 900 frames"})

        # Check if video exists in Project table
        if not Project.objects.filter(project_id=video_id).exists():
            raise serializers.ValidationError({"video_id": f"Video with ID {video_id} does not exist"})

        # Check if start frame exists in VideoData table
        if not VideoData.objects.filter(video_id=video_id, frame_no=start).exists():
            raise serializers.ValidationError({"start": f"Start frame {start} not found for video {video_id}"})

        # Check if end frame exists in VideoData table
        if not VideoData.objects.filter(video_id=video_id, frame_no=end).exists():
            raise serializers.ValidationError({"end": f"End frame {end} not found for video {video_id}"})
            
        return attrs

    def get_data(self):
        validated_data = self.validated_data
        start_frame = validated_data['start']
        end_frame = validated_data['end']
        video_id = validated_data['video_id']

        # Query DB
        qs = VideoData.objects.filter(
            video_id=video_id, 
            frame_no__gte=start_frame, 
            frame_no__lte=end_frame
        ).order_by('frame_no')

        # Include fallback frames from view if present
        if hasattr(self, "extra_frames"):
            qs = qs | VideoData.objects.filter(
                video_id=video_id,
                frame_no__in=self.extra_frames
            )
            qs = qs.order_by("frame_no")

        # Aggregate by object
        objects_map = {}

        for row in qs:
            f_id = row.frame_no
            confs = row.confidence if row.confidence else []
            tags = row.tag if row.tag else []
            timestamps = row.timestamp if row.timestamp else []

            for i in range(1, 11):
                obj_id = getattr(row, f'object_{i}_id')
                coords = getattr(row, f'object_{i}_coordinates')
                        
                if obj_id is not None:
                    idx = i - 1
                    if obj_id not in objects_map:
                        objects_map[obj_id] = {
                            "object_id": obj_id,
                            "frames": []
                        }
                    
                    objects_map[obj_id]["frames"].append({
                        "frame_id": f_id,
                        "coordinates": coords,
                        "confidence": confs[idx] if idx < len(confs) else None,
                        "tag": tags[idx] if idx < len(tags) else None,
                        "timestamp": timestamps[idx] if idx < len(timestamps) else None,
                    })

        return {
            "video_id": video_id,
            "start_frame": start_frame,
            "end_frame": end_frame,
            "objects": list(objects_map.values()),
        }


class FrameInfoSerializer(serializers.Serializer):
    """
    Serializer to handle fetching frame information by video ID and frame number.
    Returns all tracking data for the specified frame.
    """
    video = serializers.IntegerField(required=True, help_text="Video ID")
    frame = serializers.IntegerField(required=True, help_text="Frame number")

    def validate(self, attrs):
        video_id = attrs.get('video')
        frame_num = attrs.get('frame')

        # Validate that frame number is non-negative
        if frame_num < 0:
            raise serializers.ValidationError({"frame": "Frame number must be non-negative"})

        # Check if video exists in Project table
        if not Project.objects.filter(project_id=video_id).exists():
            raise serializers.ValidationError({"video": f"Video with ID {video_id} does not exist"})

        # Check if frame exists in VideoData table
        # if not VideoData.objects.filter(video_id=video_id, frame_no=frame_num).exists():
        #     raise serializers.ValidationError({"frame": f"Frame {frame_num} not found for video {video_id}"})

        return attrs

    def get_data(self):
        """
        Fetch frame data from the database and return structured response.
        """
        validated_data = self.validated_data
        video_id = validated_data['video']
        frame_num = validated_data['frame']

        # Query the database for the specific frame (already validated to exist)
        frame_data = VideoData.objects.get(video_id=video_id, frame_no=frame_num)

        # Extract frame-level data
        confs = frame_data.confidence if frame_data.confidence else []
        tags = frame_data.tag if frame_data.tag else []
        timestamps = frame_data.timestamp if frame_data.timestamp else []

        # Build objects list
        objects = []
        for i in range(1, 11):  # 10 object slots
            obj_id = getattr(frame_data, f'object_{i}_id')
            coords = getattr(frame_data, f'object_{i}_coordinates')
            
            
            if obj_id is not None:
                track = ObjectTrack.objects.filter(
                project_id_id=video_id,
                object_id=obj_id
            ).first()

                objects.append({
                    "object_id": obj_id,
                    "coordinates": coords,
                    "start_frame": track.start_frame if track else None,
                    "end_frame": track.end_frame if track else None,
                    # "confidence": confs[idx] if idx < len(confs) else None,
                    # "tag": tags[idx] if idx < len(tags) else None,
                    # "timestamp": timestamps[idx] if idx < len(timestamps) else None,
                    })

        # Return structured response
        return {
            "video_id": video_id,
            "frame_number": frame_num,
            # "frame_timestamp": frame_data.frame_timestamp,
            # "trk_timestamp": frame_data.trk_timestamp,
            "objects": objects,
        }


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
            "video_id",
            "video_name",
            "video_path", 
            "trk_file_name",
            "trk_file_path",
            "project_status",
            "created_at",
            "updated_at",
        ]

class ListUniqueIdsSerializer(serializers.Serializer):
    def validate(self, data):
        project_id = self.context.get("project_id")

        if not Project.objects.filter(project_id=project_id).exists():
            raise serializers.ValidationError({"project_id": "Invalid project ID"})

        return data

    def get_all_ids(self):
        project_id = self.context.get("project_id")

        ids = (
            ObjectTrack.objects.filter(project_id=project_id)
            .values_list("object_id", flat=True)
        )

        return {
            "project_id": project_id,
            "unique_ids": list(ids),
        }


class ObjectTrackDetailsSerializer(serializers.Serializer):
    object_id = serializers.IntegerField(required=True)
    frame = serializers.IntegerField(required=True)

    def validate(self, data):
        project_id = self.context.get("project_id")
        obj_id = data.get("object_id")
        frame = data.get("frame")

        # Validate project exists
        if not Project.objects.filter(project_id=project_id).exists():
            raise serializers.ValidationError({"project_id": "Invalid project ID"})

        # Validate object exists
        obj = ObjectTrack.objects.filter(project_id_id=project_id, object_id=obj_id).first()
        if not obj:
            # object doesn't exist at all → mark and continue
            data["object_missing"] = True
            return data

        # pass object row to next method
        data["object_row"] = obj


        # Validate frame >= 0
        if frame < 0:
            raise serializers.ValidationError({"frame": "Frame must be >= 0"})

        return data

    def get_object_data(self):
        project_id = self.context.get("project_id")
        obj_id = self.validated_data["object_id"]
        frame = self.validated_data["frame"]

        row = self.validated_data.get("object_row")

        # Case 1 — Object doesn't exist
        if self.validated_data.get("object_missing"):
            return {
                "project_id": project_id,
                "object_id": obj_id,
                "message": "Object ID not found in database",
                "is_active": False
            }

        # Case 2 — Object exists but inactive
        if row.object_status == 0:
            return {
                "project_id": project_id,
                "object_id": obj_id,
                "message": "Object is inactive",
                "is_active": False,
                "operation_note": row.operation_note
            }

        # Case 3 — Active: normal logic
        is_inside = row.start_frame <= frame <= row.end_frame


        return {
            "project_id": project_id,
            "object_id": obj_id,
            "start_frame": row.start_frame,
            "end_frame": row.end_frame,
            "is_inside": is_inside,
            "object_status": row.object_status,
            "operation_note": row.operation_note,
        }


class LinkObjectSerializer(serializers.Serializer):
    object_1_id = serializers.IntegerField(required=True)
    object_1_start = serializers.IntegerField(required=True)
    object_1_end = serializers.IntegerField(required=True)

    object_2_id = serializers.IntegerField(required=True)
    object_2_start = serializers.IntegerField(required=True)
    object_2_end = serializers.IntegerField(required=True)

    def validate(self, data):
        video_id = self.context.get("video_id")

        if not Project.objects.filter(project_id=video_id).exists():
            raise serializers.ValidationError({"video_id": "Invalid Video ID"})

        if data["object_1_id"] == data["object_2_id"]:
            raise serializers.ValidationError("Object IDs cannot be the same.")

        return data

    def merge_data(self):
        """
        Merge object_2 into object_1:
        - In VideoData: replace object_2_id with object_1_id in the given frame range.
        - In ObjectTrack: extend object_1 range, mark object_2 as inactive with a 'link' note.
        """
        data = self.validated_data
        project_id = self.context.get("video_id")

        obj1 = data["object_1_id"]
        obj2 = data["object_2_id"]
        start2 = data["object_2_start"]
        end2 = data["object_2_end"]

        # 1) Update main table (video_data)
        rows = VideoData.objects.filter(
            video_id=project_id,
            frame_no__gte=start2,
            frame_no__lte=end2
        )

        rows_updated = 0
        for row in rows:
            changed = False
            for i in range(1, 10 + 1):
                field = f"object_{i}_id"
                if getattr(row, field) == obj2:
                    setattr(row, field, obj1)
                    changed = True
            if changed:
                row.save()
                rows_updated += 1

        # 2) Update object_track table (no delete now)
        obj1_row = ObjectTrack.objects.get(project_id_id=project_id, object_id=obj1)
        obj2_row = ObjectTrack.objects.get(project_id_id=project_id, object_id=obj2)

        # Extend obj1 range
        obj1_row.start_frame = min(obj1_row.start_frame, obj2_row.start_frame)
        obj1_row.end_frame = max(obj1_row.end_frame, obj2_row.end_frame)
        obj1_row.object_status = 1  # keep active
        # Optional: note that it has absorbed another object
        obj1_row.operation_note = "link_target"  # or "merged_from_object_2"
        obj1_row.save()

        # Mark obj2 as inactive + note
        obj2_row.object_status = 0  # inactive
        obj2_row.operation_note = f"linked_into_object_{obj1}"
        obj2_row.save()

        return {
            "status": "success",
            "message": "Objects merged successfully",
            "video_id": project_id,
            "rows_updated_main_table": rows_updated,
            "object_track_object_1": {
                "object_id": obj1,
                "start_frame": obj1_row.start_frame,
                "end_frame": obj1_row.end_frame,
                "object_status": obj1_row.object_status,
                "operation_note": obj1_row.operation_note,
            },
            "object_track_object_2": {
                "object_id": obj2,
                "start_frame": obj2_row.start_frame,
                "end_frame": obj2_row.end_frame,
                "object_status": obj2_row.object_status,
                "operation_note": obj2_row.operation_note,
            },
        }


