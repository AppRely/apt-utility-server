import json
import os

from django.db import transaction
from django.db.models import Exists, Max, OuterRef, Q, Min, Count, Subquery
from rest_framework import serializers

from .models import ActivityLog, FrameObject, ObjectTrack, Project, VideoFrame, FrameConfusion
from .services.frame_info_service import FrameInfoService
from .services.frame_object_range_no_fallback_service import FrameObjectRangeNoFallbackService
from .services.frame_object_range_service import FrameObjectRangeService
from .services.object_lifecycle_service import ObjectLifecycleService
from .services.project_deletion_service import ProjectDeletionService
from .services.project_upload_service import ProjectUploadService
from .services.snapshot_builder import SnapshotBuilder
from .services.snapshot_logger import SnapshotLogger
from .services.trk_export_service_v2 import TrkBuilderExportService
from .services.undo_redo_service import UndoRedoService
from .services.frame_timeline_service import FrameTimelineService
from .services.activity_log_export_service import ActivityLogExportService
from .services.confusion_service import ConfusionTableService
from .services.trajectory_interpolation_service import TrajectoryInterpolationService
from .services.break_object_service import BreakObjectService
from .services.clip_object_service import ClipObjectService
from .services.link_object_service import LinkObjectService
from .services.next_break_service import NextBreakService
from .services.trajectory_matching_service import TrajectoryMatchingService
from .services.trajectory_clip_suggestion_service import TrajectoryClipSuggestionService
from .services.trajectory_gap_service import TrajectoryGapService
from .services.trajectory_length_service import TrajectoryLengthService

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
    video_name = serializers.SerializerMethodField()
    trk_file_name = serializers.SerializerMethodField()

    class Meta:
        model = Project
        fields = [
            "project_id",
            "project_name",
            "video_name",
            "video_path",
            "video_storage_path",
            "trk_file_name",
            "trk_file_path",
            "trk_storage_path",
            "project_status",
            "fps",
            "width",
            "height",
            "duration",
            "skeleton_graph",
            "total_frames",
            "created_at",
            "updated_at",
        ]

    def get_video_name(self, obj):
        filename = obj.video_name

        # Remove "_converted" first
        base, ext = os.path.splitext(filename)

        if base.endswith("_converted"):
            base = base[:-10]

        # Remove generated UUID
        parts = base.rsplit("_", 1)

        if len(parts) == 2 and len(parts[1]) == 8:
            base = parts[0]

        return f"{base}{ext}"

    def get_trk_file_name(self, obj):
        filename = obj.trk_file_name

        base, ext = os.path.splitext(filename)

        # Remove generated UUID
        parts = base.rsplit("_", 1)

        if len(parts) == 2 and len(parts[1]) == 8:
            base = parts[0]

        return f"{base}{ext}"


class ProjectListSerializer(ProjectSerializer):
    """Project-list representation with activity and active-object information."""

    last_updated = serializers.DateTimeField(source="last_activity_updated_at", read_only=True, allow_null=True)
    active_object_count = serializers.IntegerField(read_only=True)

    class Meta(ProjectSerializer.Meta):
        fields = ProjectSerializer.Meta.fields + ["last_updated", "active_object_count"]


class DeleteProjectSerializer(serializers.Serializer):
    """
    Serializer to validate project deletion request.
    """

    project_id = serializers.IntegerField(required=True)

    def validate_project_id(self, value):
        if not Project.objects.filter(project_id=value).exists():
            raise serializers.ValidationError("Project not found.")
        return value

    def execute(self):
        return ProjectDeletionService.delete_project(self.validated_data["project_id"])


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
        start = attrs.get("start")
        end = attrs.get("end")
        video_id = attrs.get("video_id")

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
            extra_frames=getattr(self, "extra_frames", None),
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
        video_id = attrs.get("video")
        frame_num = attrs.get("frame")

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

    start_frame = serializers.IntegerField(required=False, min_value=0,)
    end_frame = serializers.IntegerField(required=False, min_value=0,)

    def validate(self, data):

        project_id = self.context.get("project_id")

        if not Project.objects.filter(
            project_id=project_id
        ).exists():

            raise serializers.ValidationError(
                {
                    "project_id":
                        "Invalid project ID"
                }
            )

        start_frame = data.get(
            "start_frame"
        )

        end_frame = data.get(
            "end_frame"
        )

        # BOTH REQUIRED

        if (
            start_frame is not None
            and end_frame is None
        ) or (
            start_frame is None
            and end_frame is not None
        ):

            raise serializers.ValidationError(
                (
                    "Both start_frame and "
                    "end_frame are required."
                )
            )

        # RANGE VALIDATION

        if (
            start_frame is not None
            and end_frame is not None
            and start_frame > end_frame
        ):

            raise serializers.ValidationError(
                (
                    "start_frame cannot "
                    "be greater than end_frame"
                )
            )

        return data


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

        m  # # 2️⃣ Validate project_id inside objects_data
        # if "project_id" not in value:
        #     raise serializers.ValidationError(
        #         "objects_data must contain 'project_id'."
        #     )

        # if not isinstance(value["project_id"], int):
        #     raise serializers.ValidationError(
        #         "'project_id' must be an integer."
        #     )

        # if not Project.objects.filter(project_id=value["project_id"]).exists():
        #     raise serializers.ValidationError(
        #         "Invalid project_id inside objects_data."
        #     )

        # 3️⃣ Validate objects list
        if "objects" not in value:
            raise serializers.ValidationError("objects_data must contain key 'objects'.")

        # objects_list = value["objects"]

        # if not isinstance(objects_list, list):
        #     raise serializers.ValidationError("'objects' must be a list.")

        # # -------------------------------
        # # 3. Validate each object
        # # -------------------------------
        # for obj in objects_list:
        #     if not isinstance(obj, dict):
        #         raise serializers.ValidationError("Each object must be a dictionary.")

        #     for field in ["id", "start_frame", "end_frame"]:
        #         if field not in obj:
        #             raise serializers.ValidationError(f"Object missing '{field}'")

        #         if not isinstance(obj[field], int):
        #             raise serializers.ValidationError(f"'{field}' must be integer.")

        return value

    def create(self, validated_data):
        project_id = validated_data["project_id"]

        with transaction.atomic():
            # 1. Clear Redo Stack
            ActivityLog.objects.filter(project_id_id=project_id, is_applied=False).delete()

            # 2. Create activity
            activity = ActivityLog.objects.create(
                project_id_id=project_id,
                objects_data=validated_data["objects_data"],
                operation=validated_data["operation"],
                is_applied=True,
            )

            # 3. Limit Undo Stack to 5 levels
            # applied_activities = ActivityLog.objects.filter(
            #     project_id_id=project_id,
            #     is_applied=True
            # ).order_by("-activity_id")

            # if applied_activities.count() > 5:
            #     ids_to_keep = applied_activities.values_list("activity_id", flat=True)[:5]
            #     ActivityLog.objects.filter(
            #         project_id_id=project_id,
            #         is_applied=True
            #     ).exclude(activity_id__in=ids_to_keep).delete()

        return activity


class ActivityLogRequestSerializer(serializers.Serializer):
    """
    Serializer to validate video ID and fetch all Activity Logs based on video_id.
    """

    video_id = serializers.IntegerField(required=True, help_text="Video ID")

    def validate(self, attrs):
        video_id = attrs.get("video_id")

        # Video ID must exist in Project table
        if not Project.objects.filter(project_id=video_id).exists():
            raise serializers.ValidationError({"video_id": f"Video with ID {video_id} does not exist"})

        return attrs

    def get_data(self):
        """
        Fetch all activity logs for the given video_id and return summary counts.
        """
        video_id = self.validated_data["video_id"]

        #         # # 1. Get all projects linked to this video
        #         # project_ids = list(
        #         #     Project.objects.filter(video_id=video_id)
        #         #                    .values_list('project_id', flat=True)
        #         # )

        #         # # 2. Fetch activity logs for these projects
        #         # logs = ActivityLog.objects.filter(project_id__in=project_ids)

        # Fetch ALL logs for the project (both applied and unapplied) to calculate counts
        all_logs = ActivityLog.objects.filter(project_id_id=video_id).order_by("-activity_updated_at")

        total_length = all_logs.count()
        total_undo_can_perform = all_logs.filter(is_applied=True).count()
        total_redo_can_perform = all_logs.filter(is_applied=False).count()

        # 3. Structure the response
        logs_data = []
        for log in all_logs.filter(is_applied=True):
            logs_data.append(
                {
                    "activity_id": log.activity_id,
                    "project_id": log.project_id_id,
                    "objects_data": log.objects_data,
                    "operation": log.operation,
                    "activity_updated_at": log.activity_updated_at,
                }
            )

        return {
            "video_id": video_id,
            "total_length": total_length,
            "total_undo_can_perform": total_undo_can_perform,
            "total_redo_can_perform": total_redo_can_perform,
            "logs": logs_data,
        }


# =============================
# OBJECT OPERATION SERIALIZERS
# =============================


class BulkLinkObjectSerializer(serializers.Serializer):
    object_id = serializers.IntegerField()
    start_frame = serializers.IntegerField()
    end_frame = serializers.IntegerField()

    def validate(self, data):
        if data["start_frame"] > data["end_frame"]:
            raise serializers.ValidationError("Invalid frame range.")
        return data


class LinkObjectSerializer(serializers.Serializer):
    object_1_id = serializers.IntegerField(required=False)
    object_1_start = serializers.IntegerField(required=False)
    object_1_end = serializers.IntegerField(required=False)

    object_2_id = serializers.IntegerField(required=False)
    object_2_start = serializers.IntegerField(required=False)
    object_2_end = serializers.IntegerField(required=False)

    objects = BulkLinkObjectSerializer(many=True, required=False, min_length=2)

    # Supported operations
    operation = serializers.ChoiceField(
        choices=[
            "link",
            "overlap",
            "bulk_link",
        ],
        default="link",
        required=False,
    )

    preferred_object = serializers.IntegerField(
        required=False,
        allow_null=True,
    )

    def validate(self, data):

        project_id = self.context.get("project_id")

        if not project_id:
            raise serializers.ValidationError("Missing project_id in context.")

        try:
            project_id = serializers.IntegerField(min_value=1, max_value=2147483647).run_validation(project_id)
        except serializers.ValidationError:
            raise serializers.ValidationError({
                "project_id": "Invalid project ID. Use a positive numeric project ID in the URL."
            })

        if not Project.objects.filter(project_id=project_id).exists():
            raise serializers.ValidationError("Invalid project")

        if data["operation"] == "bulk_link":
            objects = data.get("objects")
            if objects is None:
                raise serializers.ValidationError({"objects": "This field is required."})
            ids = [item["object_id"] for item in objects]
            if len(ids) != len(set(ids)):
                raise serializers.ValidationError({"objects": "Object IDs must be unique."})
            data["project_id"] = project_id
            return data

        required = [f"object_{i}_{field}" for i in (1, 2) for field in ("id", "start", "end")]
        missing = {field: "This field is required." for field in required if field not in data}
        if missing:
            raise serializers.ValidationError(missing)

        if data["object_1_id"] == data["object_2_id"]:
            raise serializers.ValidationError(
                "Object IDs cannot be the same."
            )

        if data["object_1_start"] > data["object_1_end"]:
            raise serializers.ValidationError(
                {
                    "object_1_range": "Invalid frame range."
                }
            )

        if data["object_2_start"] > data["object_2_end"]:
            raise serializers.ValidationError(
                {
                    "object_2_range": "Invalid frame range."
                }
            )

        try:

            object_1_track = ObjectTrack.objects.get(
                project_id_id=project_id,
                object_id=data["object_1_id"],
                object_status=1,
            )

        except (ObjectTrack.DoesNotExist, ObjectTrack.MultipleObjectsReturned):

            raise serializers.ValidationError(
                {
                    "object_1_id": "Object 1 not found."
                }
            )

        try:

            object_2_track = ObjectTrack.objects.get(
                project_id_id=project_id,
                object_id=data["object_2_id"],
                object_status=1,
            )

        except (ObjectTrack.DoesNotExist, ObjectTrack.MultipleObjectsReturned):

            raise serializers.ValidationError(
                {
                    "object_2_id": "Object 2 not found."
                }
            )

        if (
            data["object_2_start"] < object_2_track.start_frame
            or data["object_2_end"] > object_2_track.end_frame
        ):
            raise serializers.ValidationError(
                {
                    "object_2_range":
                        "Range outside object_2 lifecycle."
                }
            )

        if (
            data["object_1_start"] < object_1_track.start_frame
            or data["object_1_end"] > object_1_track.end_frame
        ):
            raise serializers.ValidationError(
                {
                    "object_1_range":
                        "Range outside object_1 lifecycle."
                }
            )

        # ------------------------------------------
        # Overlap validation
        # ------------------------------------------

        if data.get("operation") == "overlap":

            preferred = data.get("preferred_object")

            if preferred is None:
                raise serializers.ValidationError(
                    {
                        "preferred_object": "preferred_object is required for overlap operation."
                    }
                )

            if preferred not in (
                data["object_1_id"],
                data["object_2_id"],
            ):
                raise serializers.ValidationError(
                    {
                        "preferred_object": "Must be object_1_id or object_2_id."
                    }
                )

        data["project_id"] = project_id
        data["object_1_track"] = object_1_track
        data["object_2_track"] = object_2_track

        return data

    def save(self):
        return LinkObjectService.execute(
            **self.validated_data
        )


################################################
# Break
################################################
class BreakObjectSerializer(serializers.Serializer):
    """
    Serializer responsible only for validation.

    Business logic is delegated to BreakObjectService.
    """

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

        break_type = self.context.get(
            "break_type",
            "after",
        ).lower()

        if break_type not in ("before", "after"):
            raise serializers.ValidationError(
                {
                    "break_type": "Valid values are 'before' or 'after'."
                }
            )

        object_id = data["object_id"]
        break_frame = data["break_frame"]

        # ----------------------------------------------------
        # Project validation
        # ----------------------------------------------------
        if not Project.objects.filter(
            project_id=project_id
        ).exists():
            raise serializers.ValidationError(
                "Invalid project_id"
            )

        # ----------------------------------------------------
        # Active object validation
        # ----------------------------------------------------
        try:
            obj_track = ObjectLifecycleService.get_active_object(
                project_id=project_id,
                object_id=object_id,
            )

        except ObjectTrack.DoesNotExist:
            raise serializers.ValidationError(
                "Active object not found"
            )

        # ----------------------------------------------------
        # Break frame validation
        # ----------------------------------------------------
        if not (
            obj_track.start_frame
            < break_frame
            < obj_track.end_frame
        ):
            raise serializers.ValidationError(
                f"break_frame must be between "
                f"{obj_track.start_frame} "
                f"and "
                f"{obj_track.end_frame}"
            )

        # ----------------------------------------------------
        # Optional validation
        # ----------------------------------------------------
        if (
            "start_frame" in data
            and data["start_frame"] != obj_track.start_frame
        ):
            raise serializers.ValidationError(
                "start_frame mismatch with DB"
            )

        if (
            "end_frame" in data
            and data["end_frame"] != obj_track.end_frame
        ):
            raise serializers.ValidationError(
                "end_frame mismatch with DB"
            )

        data["obj_track"] = obj_track
        data["break_type"] = break_type

        return data

    def create(self, validated_data):
        """
        Delegate complete business logic
        to BreakObjectService.
        """

        return BreakObjectService.execute(
            project_id=self.context["project_id"],
            object_id=validated_data["object_id"],
            break_frame=validated_data["break_frame"],
            break_type=validated_data["break_type"],
            obj_track=validated_data["obj_track"],
        )


class ClipObjectSerializer(serializers.Serializer):
    """Validate a request to move a frame interval to a new object ID."""

    object_id = serializers.IntegerField(required=True)
    start_frame = serializers.IntegerField(required=True)
    end_frame = serializers.IntegerField(required=True)

    def validate(self, data):
        project_id = self.context["project_id"]
        if not Project.objects.filter(project_id=project_id).exists():
            raise serializers.ValidationError("Invalid project_id")

        try:
            obj_track = ObjectLifecycleService.get_active_object(
                project_id=project_id,
                object_id=data["object_id"],
            )
        except ObjectTrack.DoesNotExist:
            raise serializers.ValidationError("Active object not found")

        start_frame = data["start_frame"]
        end_frame = data["end_frame"]
        if start_frame > end_frame:
            raise serializers.ValidationError(
                {"end_frame": "end_frame must be greater than or equal to start_frame."}
            )
        if start_frame < obj_track.start_frame or end_frame > obj_track.end_frame:
            raise serializers.ValidationError(
                {
                    "clip_range": (
                        f"Clip range must be between {obj_track.start_frame} "
                        f"and {obj_track.end_frame}."
                    )
                }
            )

        project_frames = VideoFrame.objects.filter(
            project_id_id=project_id,
            frame_no__gte=start_frame,
            frame_no__lte=end_frame,
        )
        if project_frames.count() != end_frame - start_frame + 1:
            raise serializers.ValidationError(
                {"clip_range": "The selected frame range does not belong to the project."}
            )
        if not FrameObject.objects.filter(
            frame_id__in=project_frames.values("id"),
            object_id=data["object_id"],
            is_active=True,
        ).exists():
            raise serializers.ValidationError(
                {"clip_range": "No active object rows found in the selected range."}
            )

        data["obj_track"] = obj_track
        return data

    def create(self, validated_data):
        return ClipObjectService.execute(
            project_id=self.context["project_id"],
            object_id=validated_data["object_id"],
            start_frame=validated_data["start_frame"],
            end_frame=validated_data["end_frame"],
            obj_track=validated_data["obj_track"],
        )


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
            raise serializers.ValidationError({"current_frame": "Swap frame outside overlapping lifetime"})

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
            # =====================================================
            # SNAPSHOT — BEFORE (Capture original state)
            # =====================================================
            before_state = SnapshotBuilder.build(
                before_qs_map={
                    "FrameObject": (FrameObject.objects.filter(
                        frame__project_id_id=project_id,
                        frame__frame_no__gte=swap_start,
                        frame__frame_no__lte=swap_end,
                    ).filter(Q(object_id=obj1) | Q(object_id=obj2)), ("id", "object_id")),
                    "ObjectTrack": (ObjectTrack.objects.filter(track_id__in=[obj1_track.track_id, obj2_track.track_id]), ("track_id", "start_frame", "end_frame", "operation_note")),
                },
                after_qs_map={},  # Empty after_qs_map puts everything in 'deleted'
            )

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

            # 4️⃣ Update ObjectTrack (partial swap from current_frame onward)
            # Keep unchanged prefix frames in the bounds used by later deletes.
            for track, other_id in ((obj1_track, obj2), (obj2_track, obj1)):
                bounds = FrameObject.objects.filter(
                    frame__project_id_id=project_id,
                    object_id=track.object_id,
                    is_active=True,
                ).aggregate(start=Min("frame__frame_no"), end=Max("frame__frame_no"))
                if bounds["start"] is None:
                    raise serializers.ValidationError("Swapped object has no active frames")
                track.start_frame = bounds["start"]
                track.end_frame = bounds["end"]
                track.operation_note = f"swap_from_frame_{current_frame}_with_{other_id}"
                track.save(update_fields=["start_frame", "end_frame", "operation_note"])

            # =====================================================
            # SNAPSHOT — AFTER (Capture swapped state)
            # =====================================================
            after_state = SnapshotBuilder.build(
                before_qs_map={},  # Empty before_qs_map puts everything in 'created'
                after_qs_map={
                    "FrameObject": (FrameObject.objects.filter(
                        frame__project_id_id=project_id,
                        frame__frame_no__gte=swap_start,
                        frame__frame_no__lte=swap_end,
                    ).filter(Q(object_id=obj1) | Q(object_id=obj2)), ("id", "object_id")),
                    "ObjectTrack": (ObjectTrack.objects.filter(track_id__in=[obj1_track.track_id, obj2_track.track_id]), ("track_id", "start_frame", "end_frame", "operation_note")),
                },
            )

            # =====================================================
            # SNAPSHOT LOG
            # =====================================================
            SnapshotLogger.log(
                project_id=project_id,
                operation="swap",
                before_state=before_state,
                after_state=after_state,
                objects_data={
                    "object_1_id": obj1,
                    "object_1_start": swap_start,
                    "object_1_end": obj1_track.end_frame,
                    "object_2_id": obj2,
                    "object_2_start": swap_start,
                    "object_2_end": obj2_track.end_frame,
                },
            )

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
    object_id = serializers.IntegerField(required=False)
    start_frame = serializers.IntegerField(required=False)
    end_frame = serializers.IntegerField(required=False)
    object_ids = serializers.ListField(
        child=serializers.IntegerField(),
        required=False,
        allow_empty=False,
    )

    def validate(self, data):
        project_id = self.context["project_id"]
        operation_type = self.context.get("operation_type", "single")

        if not Project.objects.filter(project_id=project_id).exists():
            raise serializers.ValidationError("Invalid project_id")

        if operation_type == "single":
            return self._validate_single(data=data, project_id=project_id)
        if operation_type == "bulk":
            return self._validate_bulk(data=data, project_id=project_id)

        raise serializers.ValidationError(
            "Invalid operation_type. Supported values are single and bulk."
        )

    def _validate_single(self, *, data, project_id):
        required_fields = ("object_id", "start_frame", "end_frame")
        missing_fields = {
            field: ["This field is required."]
            for field in required_fields
            if field not in data
        }
        if missing_fields:
            raise serializers.ValidationError(missing_fields)

        object_id = data["object_id"]
        start_frame = data["start_frame"]
        end_frame = data["end_frame"]

        if start_frame > end_frame:
            raise serializers.ValidationError("start_frame must be less than or equal to end_frame")

        try:
            obj_track = ObjectLifecycleService.get_active_object(project_id=project_id, object_id=object_id)
        except ObjectTrack.DoesNotExist:
            raise serializers.ValidationError("Active object not found")

        if start_frame < obj_track.start_frame or end_frame > obj_track.end_frame:
            raise serializers.ValidationError(
                f"Delete range must be between {obj_track.start_frame} and {obj_track.end_frame}"
            )

        data["obj_track"] = obj_track
        return data

    def _validate_bulk(self, *, data, project_id):
        if "object_ids" not in data:
            raise serializers.ValidationError(
                {"object_ids": ["This field is required."]}
            )

        object_ids = data["object_ids"]
        if not object_ids:
            raise serializers.ValidationError(
                {"object_ids": ["At least one object ID is required."]}
            )
        if len(object_ids) != len(set(object_ids)):
            raise serializers.ValidationError(
                {"object_ids": ["Duplicate object IDs are not allowed."]}
            )

        active_tracks = list(
            ObjectTrack.objects.filter(
                project_id_id=project_id,
                object_id__in=object_ids,
                object_status=1,
            )
        )
        tracks_by_object_id = {}
        for track in active_tracks:
            tracks_by_object_id.setdefault(track.object_id, []).append(track)

        invalid_object_ids = []
        for object_id in object_ids:
            matching_tracks = tracks_by_object_id.get(object_id, [])
            if len(matching_tracks) != 1:
                invalid_object_ids.append(object_id)
                continue
            if matching_tracks[0].start_frame > matching_tracks[0].end_frame:
                invalid_object_ids.append(object_id)

        if invalid_object_ids:
            raise serializers.ValidationError(
                {"invalid_object_ids": invalid_object_ids}
            )

        data["obj_tracks"] = [
            tracks_by_object_id[object_id][0] for object_id in object_ids
        ]
        return data


class UndoSerializer(serializers.Serializer):
    project_id = serializers.IntegerField(required=True)

    def validate_project_id(self, value):
        if not Project.objects.filter(project_id=value).exists():
            raise serializers.ValidationError("Invalid project_id")
        return value

    def execute(self):
        try:
            return UndoRedoService.undo(self.validated_data["project_id"])
        except ValueError as e:
            raise serializers.ValidationError(str(e))


class RedoSerializer(serializers.Serializer):
    project_id = serializers.IntegerField(required=True)

    def validate_project_id(self, value):
        if not Project.objects.filter(project_id=value).exists():
            raise serializers.ValidationError("Invalid project_id")
        return value

    def execute(self):
        try:
            return UndoRedoService.redo(self.validated_data["project_id"])
        except ValueError as e:
            raise serializers.ValidationError(str(e))


##########################################
# frame_object_range_no_fallback
#########################################


class FrameObjectRangeNoFallbackSerializer(serializers.Serializer):
    """
    Serializer to handle fetching object data for a range of frames without fallback.
    """

    start = serializers.IntegerField(required=True, help_text="Start frame id (inclusive)")
    end = serializers.IntegerField(required=True, help_text="End frame id (inclusive, max span 900)")
    video_id = serializers.IntegerField(required=True, help_text="Video ID (passed from view)")

    def validate(self, attrs):
        start = attrs.get("start")
        end = attrs.get("end")
        video_id = attrs.get("video_id")

        if start < 0 or end < 0:
            raise serializers.ValidationError({"start": "Start frame number must be non-negative"})

        if start > end:
            raise serializers.ValidationError({"start": "start must be <= end"})

        if end - start + 1 > 900:
            raise serializers.ValidationError({"end": "range cannot exceed 900 frames"})

        if not Project.objects.filter(project_id=video_id).exists():
            raise serializers.ValidationError({"video_id": "Invalid video_id"})

        return attrs

    def get_data(self):
        data = self.validated_data

        objects = FrameObjectRangeNoFallbackService.fetch(
            project_id=data["video_id"], start_frame=data["start"], end_frame=data["end"]
        )

        return {
            "video_id": data["video_id"],
            "start_frame": data["start"],
            "end_frame": data["end"],
            "objects": objects,
        }


class TrkExportSerializer(serializers.Serializer):
    project_id = serializers.IntegerField(required=True)

    def validate_project_id(self, value):
        if not Project.objects.filter(project_id=value).exists():
            raise serializers.ValidationError("Invalid project_id")
        return value

    def export(self):
        return TrkBuilderExportService.export(project_id=self.validated_data["project_id"])



# ==========================================
# CONFUSION / UNCERTAINTY SERIALIZER
# ==========================================
class FrameConfusionRowSerializer(serializers.ModelSerializer):

    class Meta:

        model = FrameConfusion

        fields = [
            "id",
            "frame_no",
            "next_frame_no",
            "current_object_id",
            "best_match_object_id",
            "second_match_object_id",
            "uncertainty",
            "is_forward",
            "best_match_cost",
            "second_match_cost",
            "nearby_object_count",
            "confusion_score",
            "is_crowded",
            "nearby_object_ids",
            "event_type",
            "created_at",
        ]
class FrameTimelineSerializer(serializers.Serializer):

    project_id = serializers.IntegerField(
        required=True,
        help_text="Project ID",
    )

    start = serializers.IntegerField(
        required=True,
    )

    end = serializers.IntegerField(
        required=True,
    )

    object_ids = serializers.CharField(
        required=False,
        allow_blank=True,
    )

    def validate(self, attrs):

        start = attrs["start"]
        end = attrs["end"]
        project_id = attrs["project_id"]

        # =====================================
        # FRAME VALIDATION
        # =====================================

        if start < 0:

            raise serializers.ValidationError(
                {
                    "start":
                        "start frame cannot be negative"
                }
            )

        if end < 0:

            raise serializers.ValidationError(
                {
                    "end":
                        "end frame cannot be negative"
                }
            )

        if start > end:

            raise serializers.ValidationError(
                {
                    "frame_range":
                        (
                            "start frame cannot "
                            "be greater than end frame"
                        )
                }
            )

        # =====================================
        # PROJECT VALIDATION
        # =====================================

        project = Project.objects.filter(
            project_id=project_id
        ).first()

        if not project:

            raise serializers.ValidationError(
                {
                    "project_id":
                        "Invalid project_id"
                }
            )

        if (
            project.total_frames is not None
            and end > project.total_frames
        ):

            raise serializers.ValidationError(
                {
                    "end":
                        (
                            f"end frame exceeds "
                            f"total frames "
                            f"({project.total_frames})"
                        )
                }
            )

        # =====================================
        # OBJECT IDS PARSING
        # =====================================

        raw_object_ids = attrs.get(
            "object_ids",
            ""
        )

        parsed_object_ids = []

        if raw_object_ids:

            try:

                parsed_object_ids = [
                    int(obj.strip())
                    for obj in raw_object_ids.split(",")
                    if obj.strip()
                ]

            except ValueError:

                raise serializers.ValidationError(
                    {
                        "object_ids":
                            (
                                "object_ids must contain "
                                "integers only"
                            )
                    }
                )

            # REMOVE DUPLICATES
            parsed_object_ids = list(
                dict.fromkeys(
                    parsed_object_ids
                )
            )

            # =====================================
            # VALIDATE OBJECT IDS
            # =====================================

            existing_ids = set(
                FrameObject.objects.filter(
                    frame__project_id=project_id,
                    frame__frame_no__gte=start,
                    frame__frame_no__lte=end,
                    object_id__in=parsed_object_ids,
                    is_active=True,
                ).values_list(
                    "object_id",
                    flat=True,
                )
            )

            # ALL INVALID

            if len(existing_ids) == 0:

                raise serializers.ValidationError(
                    {
                        "object_ids":
                            (
                                "Provided object_ids "
                                "do not exist "
                                "in this project"
                            )
                    }
                )

            # PARTIAL INVALID

            invalid_ids = [
                obj_id
                for obj_id in parsed_object_ids
                if obj_id not in existing_ids
            ]

            if invalid_ids:

                raise serializers.ValidationError(
                    {
                        "object_ids":
                            (
                                f"Invalid object_ids: "
                                f"{invalid_ids}"
                            )
                    }
                )

        # STORE PARSED IDS

        attrs["object_ids"] = (
            parsed_object_ids
        )

        return attrs

    def get_data(self):

        data = self.validated_data

        return FrameTimelineService.fetch(
            project_id=data["project_id"],
            start=data["start"],
            end=data["end"],
            object_ids=data.get("object_ids"),
        )



class InterpolateTrajectorySerializer(serializers.Serializer):

    source_object_id = serializers.IntegerField(required=False)
    source_end_frame = serializers.IntegerField(required=False)
    target_object_id = serializers.IntegerField(required=False)
    target_start_frame = serializers.IntegerField(required=False)
    object_id = serializers.IntegerField(required=False)
    start_frame = serializers.IntegerField(required=False)
    end_frame = serializers.IntegerField(required=False)

    def validate(self, data):

        project_id = self.context["project_id"]

        if not Project.objects.filter(
            project_id=project_id
        ).exists():
            raise serializers.ValidationError(
                "Invalid project"
            )
        if (
            data.get("object_id") is not None
            and data.get("start_frame") is not None
            and data.get("end_frame") is not None
        ):

            object_exists = ObjectTrack.objects.filter(
                project_id_id=project_id,
                object_id=data["object_id"],
            ).exists()

            if not object_exists:
                raise serializers.ValidationError(
                    {
                        "object_id": "Object does not exist"
                    }
                )

            if data["end_frame"] <= data["start_frame"]:
                raise serializers.ValidationError(
                    "end_frame must be greater than start_frame"
                )

            return data

        if (
            data["target_start_frame"]
            <=
            data["source_end_frame"]
        ):
            raise serializers.ValidationError(
                "target_start_frame must be greater than source_end_frame"
            )

        source_exists = FrameObject.objects.filter(
            frame__project_id_id=project_id,
            frame__frame_no=data["source_end_frame"],
            object_id=data["source_object_id"],
            is_active=True,
        ).exists()

        if not source_exists:
            raise serializers.ValidationError(
                {
                    "source_object_id":
                    "Object not found in source frame"
                }
            )

        target_exists = FrameObject.objects.filter(
            frame__project_id_id=project_id,
            frame__frame_no=data["target_start_frame"],
            object_id=data["target_object_id"],
            is_active=True,
        ).exists()

        if not target_exists:
            raise serializers.ValidationError(
                {
                    "target_object_id":
                    "Object not found in target frame"
                }
            )

        return data

    def execute(self):

        return (
            TrajectoryInterpolationService.interpolate(
                project_id=self.context["project_id"],
                **self.validated_data,
            )
        )


class NextBreakRequestSerializer(serializers.Serializer):
    object_id = serializers.IntegerField(min_value=0)
    current_frame = serializers.IntegerField(min_value=0)

    def validate(self, data):
        project_id = self.context["project_id"]
        if not Project.objects.filter(project_id=project_id).exists():
            raise serializers.ValidationError({"project_id": "Invalid project_id."})

        if not VideoFrame.objects.filter(
            project_id_id=project_id,
            frame_no=data["current_frame"],
        ).exists():
            raise serializers.ValidationError(
                {"current_frame": "Frame does not exist in this project."}
            )

        object_filter = {
            "project_id_id": project_id,
            "object_id": data["object_id"],
            "object_status": 1,
        }
        if not ObjectTrack.objects.filter(**object_filter).exists():
            raise serializers.ValidationError(
                {"object_id": "Active object does not exist in this project."}
            )

        if not FrameObject.objects.filter(
            frame__project_id_id=project_id,
            object_id=data["object_id"],
            is_active=True,
        ).exists():
            raise serializers.ValidationError(
                {"object_id": "Object has no active trajectory data."}
            )
        return data

    def get_data(self):
        return NextBreakService.find(
            project_id=self.context["project_id"],
            **self.validated_data,
        )


class NextBreakResponseSerializer(serializers.Serializer):
    object_id = serializers.IntegerField()
    break_start = serializers.IntegerField(allow_null=True)
    break_end = serializers.IntegerField(allow_null=True)


class TrajectorySuggestionSerializer(serializers.Serializer):
    object_id = serializers.IntegerField()
    score = serializers.FloatField(min_value=0.0, max_value=1.0)


class TrajectoryMatchingRequestSerializer(serializers.Serializer):
    object_id = serializers.IntegerField(min_value=0)
    break_start = serializers.IntegerField(min_value=0)
    break_end = serializers.IntegerField(min_value=0)
    limit = serializers.IntegerField(
        required=False,
        default=TrajectoryMatchingService.DEFAULT_LIMIT,
        min_value=1,
        max_value=20,
        write_only=True,
    )

    def validate(self, data):
        project_id = self.context["project_id"]
        break_start = data["break_start"]
        break_end = data["break_end"]

        if break_start > break_end:
            raise serializers.ValidationError(
                {"break_end": "break_end must be greater than or equal to break_start."}
            )

        if not Project.objects.filter(project_id=project_id).exists():
            raise serializers.ValidationError({"project_id": "Invalid project_id."})

        if not ObjectTrack.objects.filter(
            project_id_id=project_id,
            object_id=data["object_id"],
            object_status=1,
        ).exists():
            raise serializers.ValidationError(
                {"object_id": "Active object does not exist in this project."}
            )

        source_rows = FrameObject.objects.filter(
            frame__project_id_id=project_id,
            object_id=data["object_id"],
            is_active=True,
        )
        if source_rows.filter(
            frame__frame_no__gte=break_start,
            frame__frame_no__lte=break_end,
        ).exists():
            raise serializers.ValidationError(
                {"break_range": "Selected object has active data inside this range."}
            )

        boundary_frames = {
            "before": source_rows.filter(frame__frame_no=break_start - 1).exists(),
            "after": source_rows.filter(frame__frame_no=break_end + 1).exists(),
        }
        if not all(boundary_frames.values()):
            raise serializers.ValidationError(
                {
                    "break_range": (
                        "The range must be a continuous internal break with active "
                        "object data immediately before and after it."
                    )
                }
            )
        return data

    def get_data(self):
        return TrajectoryMatchingService().suggest(
            project_id=self.context["project_id"],
            **self.validated_data,
        )


class TrajectoryMatchingResponseSerializer(serializers.Serializer):
    object_id = serializers.IntegerField()
    break_start = serializers.IntegerField()
    break_end = serializers.IntegerField()
    suggestions = TrajectorySuggestionSerializer(many=True)


class TrajectoryClipSuggestionRequestSerializer(serializers.Serializer):
    object_id = serializers.IntegerField(min_value=0)
    start_frame = serializers.IntegerField(required=False, min_value=0)
    end_frame = serializers.IntegerField(required=False, min_value=0)
    limit = serializers.IntegerField(
        required=False,
        default=TrajectoryClipSuggestionService.DEFAULT_LIMIT,
        min_value=1,
        max_value=20,
        write_only=True,
    )

    def validate(self, data):
        project_id = self.context["project_id"]
        if not Project.objects.filter(project_id=project_id).exists():
            raise serializers.ValidationError({"project_id": "Invalid project_id."})

        track = ObjectTrack.objects.filter(
            project_id_id=project_id,
            object_id=data["object_id"],
            object_status=1,
        ).first()
        if track is None:
            raise serializers.ValidationError(
                {"object_id": "Active object does not exist in this project."}
            )

        start_frame = data.get("start_frame", track.start_frame)
        end_frame = data.get("end_frame", track.end_frame)
        if start_frame > end_frame:
            raise serializers.ValidationError(
                {"end_frame": "end_frame must be greater than or equal to start_frame."}
            )
        if start_frame < track.start_frame or end_frame > track.end_frame:
            raise serializers.ValidationError(
                {
                    "frame_range": (
                        f"Range must be between {track.start_frame} "
                        f"and {track.end_frame}."
                    )
                }
            )

        data["start_frame"] = start_frame
        data["end_frame"] = end_frame
        return data

    def get_data(self):
        return TrajectoryClipSuggestionService().suggest(
            project_id=self.context["project_id"],
            **self.validated_data,
        )


class ClipIntervalSuggestionSerializer(serializers.Serializer):
    start_frame = serializers.IntegerField()
    end_frame = serializers.IntegerField()
    peak_frame = serializers.IntegerField()
    score = serializers.FloatField(min_value=0.0, max_value=1.0)
    peak_movement = serializers.FloatField(min_value=0.0)
    reason = serializers.ChoiceField(choices=("movement_spike",))


class TrajectoryClipSuggestionResponseSerializer(serializers.Serializer):
    project_id = serializers.IntegerField()
    object_id = serializers.IntegerField()
    analyzed_range = serializers.DictField(
        child=serializers.IntegerField(min_value=0)
    )
    baseline_movement = serializers.FloatField(allow_null=True, min_value=0.0)
    suggestions = ClipIntervalSuggestionSerializer(many=True)


class TrajectoryGapRequestSerializer(serializers.Serializer):
    object_id = serializers.IntegerField(min_value=0)
    min_gap = serializers.IntegerField(
        required=False,
        default=TrajectoryGapService.DEFAULT_MIN_GAP,
        min_value=TrajectoryGapService.DEFAULT_MIN_GAP,
    )
    limit = serializers.IntegerField(
        required=False,
        default=TrajectoryGapService.DEFAULT_LIMIT,
        min_value=1,
        max_value=100,
    )

    def validate(self, data):
        project_id = self.context["project_id"]
        if not Project.objects.filter(project_id=project_id).exists():
            raise serializers.ValidationError({"project_id": "Invalid project_id."})

        if not FrameObject.objects.filter(
            frame__project_id_id=project_id,
            object_id=data["object_id"],
            is_active=True,
        ).exists():
            raise serializers.ValidationError(
                {"object_id": "Object has no active trajectory data."}
            )
        return data

    def get_data(self):
        return TrajectoryGapService.find(
            project_id=self.context["project_id"],
            **self.validated_data,
        )


class TrajectoryGapSerializer(serializers.Serializer):
    start_frame = serializers.IntegerField()
    end_frame = serializers.IntegerField()
    gap = serializers.IntegerField(min_value=2)


class TrajectoryGapResponseSerializer(serializers.Serializer):
    project_id = serializers.IntegerField()
    object_id = serializers.IntegerField()
    largest_gap = TrajectoryGapSerializer(allow_null=True)
    gaps = TrajectoryGapSerializer(many=True)


class TrajectoryLengthRequestSerializer(serializers.Serializer):
    ordering = serializers.ChoiceField(
        required=False,
        default=TrajectoryLengthService.ORDER_LONGEST_FIRST,
        choices=(
            TrajectoryLengthService.ORDER_LONGEST_FIRST,
            TrajectoryLengthService.ORDER_SHORTEST_FIRST,
        ),
    )
    min_length = serializers.IntegerField(required=False, min_value=1)
    max_length = serializers.IntegerField(required=False, min_value=1)

    def validate(self, data):
        project_id = self.context["project_id"]
        if not Project.objects.filter(project_id=project_id).exists():
            raise serializers.ValidationError({"project_id": "Invalid project_id."})

        min_length = data.get("min_length")
        max_length = data.get("max_length")
        if (
            min_length is not None
            and max_length is not None
            and min_length > max_length
        ):
            raise serializers.ValidationError(
                {"max_length": "max_length must be greater than or equal to min_length."}
            )
        return data

    def get_data(self):
        return TrajectoryLengthService.list(
            project_id=self.context["project_id"],
            **self.validated_data,
        )


class TrajectoryLengthSerializer(serializers.Serializer):
    object_id = serializers.IntegerField()
    first_frame = serializers.IntegerField()
    last_frame = serializers.IntegerField()
    length = serializers.IntegerField(min_value=1)


class TrajectoryLengthResponseSerializer(serializers.Serializer):
    project_id = serializers.IntegerField()
    ordering = serializers.ChoiceField(
        choices=(
            TrajectoryLengthService.ORDER_LONGEST_FIRST,
            TrajectoryLengthService.ORDER_SHORTEST_FIRST,
        )
    )
    trajectories = TrajectoryLengthSerializer(many=True)


###########################################
## ACTIVITY LOG EXPORT SERIALIZER
###########################################
class ActivityLogExportSerializer(serializers.Serializer):
    """
    Serializer to validate audit trail export request.
    """

    project_id = serializers.IntegerField(
        required=True,
        help_text="Project ID",
    )

    def validate_project_id(self, value):
        """
        Validate that the project exists.
        """

        if not Project.objects.filter(project_id=value).exists():
            raise serializers.ValidationError(
                f"Project with ID {value} does not exist."
            )

        return value
