import math
import os

import numpy as np
from django.conf import settings
from django.db import transaction
from django.core.files.uploadedfile import UploadedFile
from rest_framework import serializers

from .models import Project, Video, VideoData
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
        video_path = os.path.join(video_folder, os.path.basename(video_file.name))
        trk_path = os.path.join(track_folder, os.path.basename(trk_file.name))

        project = None

        try:
            # 1. Save files to disk
            self._write_file(video_file, video_path)
            self._write_file(trk_file, trk_path)

            # 2. Create Project record
            project = Project.objects.create(
                project_name=project_name,
                video_name=video_file.name,
                video_path=video_path,
                status="Processing",
            )

            # 3. Parse and Persist TRK Data
            rows_inserted = 0
            with transaction.atomic():
                trk = Trk(trk_path)
                rows_inserted = self._persist_trk_data(project.project_id, trk)
                
                if rows_inserted == 0:
                    raise ValueError("Upload failed — no TRK data extracted.")

            # 4. Update status
            project.status = "Completed"
            project.save(update_fields=["status"])

            return {
                "project_id": project.project_id,
                "rows_inserted": rows_inserted,
            }

        except Exception as exc:
            self._cleanup_failed_upload(project, video_path, trk_path)
            raise serializers.ValidationError({"detail": f"Upload failed: {str(exc)}"})

    def _persist_trk_data(self, project_id: int, trk: Trk) -> int:
        """
        Parses the TRK object and inserts VideoData rows in bulk.
        """
        bulk_data: list[VideoData] = []
        total_rows: int = 0

        # Safely get start/end frames
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
                slots[f"object_{slot_num}_id"] = obj_id
                slots[f"object_{slot_num}_coordinates"] = objects_data[obj_id]["coordinates"]
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
    def _get_media_paths() -> tuple[str, str]:
        video_app_path = os.path.join(settings.BASE_DIR, "src", "video")
        media_root = os.path.join(video_app_path, "media")
        
        video_folder = os.path.join(media_root, "video_folder")
        track_folder = os.path.join(media_root, "track_folder")

        os.makedirs(video_folder, exist_ok=True)
        os.makedirs(track_folder, exist_ok=True)

        return video_folder, track_folder