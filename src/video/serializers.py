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
from .services.object_lifecycle_service import ObjectLifecycleService
from .services.frame_object_range_service import FrameObjectRangeService
from .services.frame_info_service import FrameInfoService 
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
        if start < 0 or end < 0:
            raise serializers.ValidationError({"start": "Start frame number must be non-negative"})
        
        # Validate start <= end
        if start > end:
            raise serializers.ValidationError({"start": "start must be <= end"})

        # Validate range does not exceed 900 frames
        if end - start + 1 > 900:
            raise serializers.ValidationError({"end": "range cannot exceed 900 frames"})

        
        if not Project.objects.filter(project_id=video_id).exists():
            raise serializers.ValidationError({"video_id": "Invalid video_id"})

        return attrs

    def get_data(self):

        data = self.validated_data

        objects = FrameObjectRangeService.fetch(
            project_id=data["video_id"],
            start_frame=data["start"],
            end_frame=data["end"],
            extra_frames=getattr(self, "extra_frames", None)
        )

        return {
            "video_id": data["video_id"],
            "start_frame": data["start"],
            "end_frame": data["end"],
            "objects": objects,
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

        return attrs

    def get_data(self):
        """
        Fetch frame data from the database and return structured response.
        """

        data = self.validated_data

        return FrameInfoService.fetch(
            video_id=data["video"],
            frame_no=data["frame"],
        )

class ListUniqueIdsSerializer(serializers.Serializer):
    def validate(self, data):
        project_id = self.context.get("project_id")

        if not project_id:
            raise serializers.ValidationError({"project_id": "project_id is required"})

        if not Project.objects.filter(project_id=project_id).exists():
            raise serializers.ValidationError({"project_id": "Invalid project ID"})

        return data

    def get_all_ids(self):
        project_id = self.context.get("project_id")

        ids = (
            ObjectTrack.objects.filter(project_id_id=project_id,object_status=1)
            .order_by("object_id")
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
        # obj_id = data.get("object_id")
        frame = data.get("frame")

        # Validate project exists
        if not Project.objects.filter(project_id=project_id).exists():
            raise serializers.ValidationError({"project_id": "Invalid project ID"})

        # Validate frame >= 0
        if frame < 0:
            raise serializers.ValidationError({"frame": "Frame must be >= 0"})


        return data

    def get_object_data(self):
        return ObjectLifecycleService.fetch(
            project_id=self.context["project_id"],
            object_id=self.validated_data["object_id"],
            frame=self.validated_data["frame"],
        )

# # =============================
# # ACTIVITY SERIALIZERS
# # =============================
class ActivityLogSerializer(serializers.Serializer):

    project_id = serializers.IntegerField(required=True)
    objects_data = serializers.JSONField(required=True)
    operation = serializers.CharField(max_length=255, required=True)

    def validate_project_id(self, value):
        if not Project.objects.filter(project_id=value).exists():
            raise serializers.ValidationError("Invalid project_id")
        return value

    def validate_objects_data(self, value):
        # -------------------------------
        # 1. If Swagger sends string → convert to JSON
        # -------------------------------
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                raise serializers.ValidationError("objects_data must be valid JSON.")

        # -------------------------------
        # 2. Must be a dict containing "objects"
        # -------------------------------
        if not isinstance(value, dict):
            raise serializers.ValidationError("objects_data must be a JSON object.")

        # Validate objects list
        if "objects" not in value:
            raise serializers.ValidationError("objects_data must contain key 'objects'.")

        objects_list = value["objects"]

        if not isinstance(objects_list, list):
            raise serializers.ValidationError("'objects' must be a list.")

        # -------------------------------
        # 3. Validate each object
        # -------------------------------
        for obj in objects_list:
            if not isinstance(obj, dict):
                raise serializers.ValidationError("Each object must be a dictionary.")

            for field in ["id", "start_frame", "end_frame"]:
                if field not in obj:
                    raise serializers.ValidationError(f"Object missing '{field}'")

                if not isinstance(obj[field], int):
                    raise serializers.ValidationError(f"'{field}' must be integer.")

        return value


    def create(self, validated_data):
        project_id = validated_data["project_id"]

        activity = ActivityLog.objects.create(
            project_id_id=project_id,
            objects_data=validated_data["objects_data"],
            operation=validated_data["operation"]
        )

        return {
            "activity_id": activity.activity_id,
            "project_id": activity.project_id_id,
            "objects_data": activity.objects_data,
            "operation": activity.operation,
            "activity_updated_at": activity.activity_updated_at
        }


class ActivityLogRequestSerializer(serializers.Serializer):
    """
    Serializer to validate video ID and fetch all Activity Logs based on video_id.
    """
    video_id = serializers.IntegerField(required=True, help_text="Video ID")

    def validate(self, attrs):
        video_id = attrs.get('video_id')

        # Video ID must exist in Project table
        if not Project.objects.filter(project_id=video_id).exists():
            raise serializers.ValidationError({"video_id": f"Video with ID {video_id} does not exist"})

        return attrs

    def get_data(self):
        """
        Fetch all activity logs for the given video_id.
        """
        video_id = self.validated_data['video_id']

        logs = ActivityLog.objects.filter(project_id_id=video_id).order_by("-activity_updated_at")

        # 3. Structure the response
        logs_data = []
        for log in logs:
            logs_data.append({
                "activity_id": log.activity_id,
                "project_id": log.project_id_id,
                "objects_data": log.objects_data,
                "operation": log.operation,
                "activity_updated_at": log.activity_updated_at,
            })

        return {
            "video_id": video_id,
            "logs": logs_data
        }



# =============================
# OBJECT OPERATION SERIALIZERS
# =============================

class LinkObjectSerializer(serializers.Serializer):
    object_1_id = serializers.IntegerField(required=True)
    object_1_start = serializers.IntegerField(required=True)
    object_1_end = serializers.IntegerField(required=True)

    object_2_id = serializers.IntegerField(required=True)
    object_2_start = serializers.IntegerField(required=True)
    object_2_end = serializers.IntegerField(required=True)


    def validate(self, data):
        # Use 'project_id' as context key for consistency
        project_id = self.context.get("project_id") or self.context.get("video_id")
        if not project_id:
            raise serializers.ValidationError("Missing project_id in context.")

        if not Project.objects.filter(project_id=project_id).exists():
            raise serializers.ValidationError("Invalid project")

        if data["object_2_start"] > data["object_2_end"]:
            raise serializers.ValidationError({"object_2_range": "Invalid frame range"})

        if data["object_1_start"] > data["object_1_end"]:
            raise serializers.ValidationError({"object_1_range": "Invalid frame range"})

        if data["object_1_id"] == data["object_2_id"]:
            raise serializers.ValidationError("Object IDs cannot be the same.")

        if not ObjectTrack.objects.filter(
            project_id_id=project_id,
            object_id=data["object_1_id"]
        ).exists():
            raise serializers.ValidationError({"object_1_id": "Object 1 not found"})

        if not ObjectTrack.objects.filter(
            project_id_id=project_id,
            object_id=data["object_2_id"]
        ).exists():
            raise serializers.ValidationError({"object_2_id": "Object 2 not found"})

        # Fetch obj2_track for lifecycle validation
        obj2_track = ObjectTrack.objects.get(
            project_id_id=project_id,
            object_id=data["object_2_id"]
        )
        if data["object_2_start"] < obj2_track.start_frame or \
            data["object_2_end"] > obj2_track.end_frame:
                raise serializers.ValidationError(
                    {"object_2_range": "Range outside object_2"}
        ) 


        return data


    def merge_data(self):
        """
        Merge object_2 into object_1:
        - In VideoData: replace object_2_id with object_1_id in the given frame range.
        - In ObjectTrack: extend object_1 range, mark object_2 as inactive with a 'link' note.
        """
        data = self.validated_data
        project_id = self.context.get("project_id") or self.context.get("video_id")

        obj1 = data["object_1_id"]
        obj2 = data["object_2_id"]
        start2 = data["object_2_start"]
        end2 = data["object_2_end"]


        # fetch lifecycle rows
        obj1_row = ObjectTrack.objects.get(
            project_id_id=project_id,
            object_id=obj1
        )

        obj2_row = ObjectTrack.objects.get(
            project_id_id=project_id,
            object_id=obj2
        )

        with transaction.atomic():

            rows_updated = FrameObject.objects.filter(
                frame__project_id_id=project_id,
                object_id=obj2,
                frame__frame_no__gte=start2,
                frame__frame_no__lte=end2,
            ).update(object_id=obj1)
            if rows_updated == 0:
                raise serializers.ValidationError(
                    "No frames found for object_2 in given range"
                )

            # Extend object_1 lifecycle
            obj1_row.start_frame = min(obj1_row.start_frame, obj2_row.start_frame)
            obj1_row.end_frame = max(obj1_row.end_frame, obj2_row.end_frame)
            obj1_row.object_status = 1
            obj1_row.operation_note = "link_target"

            obj1_row.save(update_fields=[
                "start_frame",
                "end_frame",
                "object_status",
                "operation_note",
            ])

            # Deactivate object_2
            ObjectLifecycleService.deactivate_object(
                obj2_row,
                note=f"linked_into_object_{obj1}"
            )

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


class BreakObjectSerializer(serializers.Serializer):
    object_id = serializers.IntegerField(required=True)
    break_frame = serializers.IntegerField(required=True)

    # optional (frontend may send)
    start_frame = serializers.IntegerField(required=False)
    end_frame = serializers.IntegerField(required=False)

    # ---------------------------
    # VALIDATION
    # ---------------------------
    def validate(self, data):
        project_id = self.context["project_id"]
        object_id = data["object_id"]
        break_frame = data["break_frame"]

        # 1️ Project validation
        if not Project.objects.filter(project_id=project_id).exists():
            raise serializers.ValidationError("Invalid project_id")

        # 2️ Active object validation
        try:
            obj_track = ObjectLifecycleService.get_active_object(
                project_id=project_id,
                object_id=object_id
            )
        except ObjectTrack.DoesNotExist:
            raise serializers.ValidationError("Active object not found")

        if not (obj_track.start_frame < break_frame < obj_track.end_frame):
            raise serializers.ValidationError(
                f"break_frame must be between {obj_track.start_frame} and {obj_track.end_frame}"
            )

        # 4️ Optional frontend validation
        if "start_frame" in data and data["start_frame"] != obj_track.start_frame:
            raise serializers.ValidationError("start_frame mismatch with DB")

        if "end_frame" in data and data["end_frame"] != obj_track.end_frame:
            raise serializers.ValidationError("end_frame mismatch with DB")

        # Attach DB info
        data["obj_track"] = obj_track

        return data


    def create(self, validated_data):
        project_id = self.context["project_id"]

        object_id = validated_data["object_id"]
        break_frame = validated_data["break_frame"]
        obj_track = validated_data["obj_track"]
        start_frame = obj_track.start_frame
        end_frame = obj_track.end_frame

        with transaction.atomic():

            # Generate new object_id
            new_object_id = (
                ObjectTrack.objects
                .filter(project_id_id=project_id)
                .aggregate(m=Max("object_id"))["m"] or 0
            ) + 1

            # Update FrameObject (frames AFTER break)

            rows_updated = FrameObject.objects.filter(
                frame__project_id_id=project_id,
                frame__frame_no__gt=break_frame,
                frame__frame_no__lte=end_frame,
                object_id=object_id
            ).update(object_id=new_object_id)

            # Update old object_track
            obj_track.end_frame = break_frame
            obj_track.operation_note = f"break_from_{start_frame}_to_{break_frame}"
            obj_track.save(update_fields=["end_frame", "operation_note"])

            # Create new object_track
            ObjectTrack.objects.create(
                project_id_id=project_id,
                object_id=new_object_id,
                start_frame=break_frame + 1,
                end_frame=end_frame,
                object_status=1,
                operation_note=f"break_from_{break_frame + 1}_to_{end_frame}"
            )


        return {
            "old_object_id": object_id,
            "new_object_id": new_object_id,
            "old_range": f"{start_frame}-{break_frame}",
            "new_range": f"{break_frame + 1}-{end_frame}",
            "rows_updated_in_frame_object": rows_updated,
        }


class SwapObjectSerializer(serializers.Serializer):
    """
    HARD swap of two object IDs from current_frame onward.
    Canonical swap implementation.
    """

    object_1_id = serializers.IntegerField(required=True)
    object_2_id = serializers.IntegerField(required=True)
    current_frame = serializers.IntegerField(required=True)

    def validate(self, data):
        project_id = self.context.get("project_id") or self.context.get("video_id")
        if not project_id:
            raise serializers.ValidationError("Missing project_id in context.")

        if data["object_1_id"] == data["object_2_id"]:
            raise serializers.ValidationError("Object IDs cannot be the same.")

        if data["current_frame"] < 0:
            raise serializers.ValidationError({"current_frame": "Must be >= 0"})

        try:
            obj1_track = ObjectTrack.objects.get(
                project_id_id=project_id,
                object_id=data["object_1_id"],
                object_status=1,
            )
        except ObjectTrack.DoesNotExist:
            raise serializers.ValidationError({"object_1_id": "Object not found"})

        try:
            obj2_track = ObjectTrack.objects.get(
                project_id_id=project_id,
                object_id=data["object_2_id"],
                object_status=1,
            )
        except ObjectTrack.DoesNotExist:
            raise serializers.ValidationError({"object_2_id": "Object not found"})

        if data["current_frame"] > min(obj1_track.end_frame, obj2_track.end_frame):
            raise serializers.ValidationError(
                {"current_frame": "Swap frame outside overlapping lifetime"}
            )

        data["project_id"] = project_id
        data["obj1_track"] = obj1_track
        data["obj2_track"] = obj2_track
        return data

    def swap_data(self):
        data = self.validated_data

        project_id = data["project_id"]
        obj1 = data["object_1_id"]
        obj2 = data["object_2_id"]
        current_frame = data["current_frame"]

        obj1_track = data["obj1_track"]
        obj2_track = data["obj2_track"]

        swap_start = current_frame
        swap_end = max(obj1_track.end_frame, obj2_track.end_frame)

        TEMP_ID = -int(project_id)
        rows_updated = 0

        with transaction.atomic():
            # 1️⃣ obj1 → TEMP
            FrameObject.objects.filter(
                frame__project_id_id=project_id,
                frame__frame_no__gte=swap_start,
                frame__frame_no__lte=swap_end,
                object_id=obj1,
            ).update(object_id=TEMP_ID)

            # 2️⃣ obj2 → obj1
            FrameObject.objects.filter(
                frame__project_id_id=project_id,
                frame__frame_no__gte=swap_start,
                frame__frame_no__lte=swap_end,
                object_id=obj2,
            ).update(object_id=obj1)

            # 3️⃣ TEMP → obj2 (restricted to swap window)
            rows_updated = FrameObject.objects.filter(
                frame__project_id_id=project_id,
                frame__frame_no__gte=swap_start,
                frame__frame_no__lte=swap_end,
                object_id=TEMP_ID,
            ).update(object_id=obj2)

            # 4️⃣ Swap ObjectTrack ranges
            obj1_start, obj1_end = obj1_track.start_frame, obj1_track.end_frame
            obj2_start, obj2_end = obj2_track.start_frame, obj2_track.end_frame

            obj1_track.start_frame = obj2_start
            obj1_track.end_frame = obj2_end
            obj1_track.operation_note = (
                f"swap_from_frame_{current_frame}_with_{obj2}"
            )

            obj2_track.start_frame = obj1_start
            obj2_track.end_frame = obj1_end
            obj2_track.operation_note = (
                f"swap_from_frame_{current_frame}_with_{obj1}"
            )

            obj1_track.save(update_fields=["start_frame", "end_frame", "operation_note"])
            obj2_track.save(update_fields=["start_frame", "end_frame", "operation_note"])

        return {
            "status": "success",
            "message": "Objects swapped successfully",
            "video_id": project_id,
            "rows_updated": rows_updated,
            "object_track_object_1": {
                "object_id": obj1,
                "start_frame": obj1_track.start_frame,
                "end_frame": obj1_track.end_frame,
                "object_status": obj1_track.object_status,
                "operation_note": obj1_track.operation_note,
            },
            "object_track_object_2": {
                "object_id": obj2,
                "start_frame": obj2_track.start_frame,
                "end_frame": obj2_track.end_frame,
                "object_status": obj2_track.object_status,
                "operation_note": obj2_track.operation_note,
            },
        }



class DeleteObjectSerializer(serializers.Serializer):
    object_id = serializers.IntegerField(required=True)
    start_frame = serializers.IntegerField(required=True)
    end_frame = serializers.IntegerField(required=True)

    # ---------------------------
    # VALIDATION
    # ---------------------------
    def validate(self, data):
        project_id = self.context["project_id"]
        object_id = data["object_id"]
        start_frame = data["start_frame"]
        end_frame = data["end_frame"]

        # Project validation
        if not Project.objects.filter(project_id=project_id).exists():
            raise serializers.ValidationError("Invalid project_id")

        # Frame range validation
        if start_frame > end_frame:
            raise serializers.ValidationError(
                "start_frame must be less than or equal to end_frame"
            )


        try:
            obj_track = ObjectLifecycleService.get_active_object(
                project_id=project_id,
                object_id=object_id
            )
        except ObjectTrack.DoesNotExist:
            raise serializers.ValidationError("Active object not found")


        # Range must lie inside lifecycle
        if start_frame < obj_track.start_frame or end_frame > obj_track.end_frame:
            raise serializers.ValidationError(
                f"Delete range must be between "
                f"{obj_track.start_frame} and {obj_track.end_frame}"
            )

        data["obj_track"] = obj_track
        return data

    def create(self, validated_data):
        project_id = self.context["project_id"]

        object_id = validated_data["object_id"]
        start_frame = validated_data["start_frame"]
        end_frame = validated_data["end_frame"]
        obj_track = validated_data["obj_track"]

        with transaction.atomic():
            affected_frames, _ = FrameObject.objects.filter(
                frame__project_id_id=project_id,
                object_id=object_id,
                frame__frame_no__gte=start_frame,
                frame__frame_no__lte=end_frame
            ).delete()

            ObjectLifecycleService.deactivate_object(
                obj_track,
                note=f"deleted_frames_{start_frame}_to_{end_frame}"
            )

        return {
            "object_id": object_id,
            "deleted_range": f"{start_frame}-{end_frame}",
            "frames_affected": affected_frames,
            "object_status": 0
        }

