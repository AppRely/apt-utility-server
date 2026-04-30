from django.contrib import admin

from src.common.admin import BaseAdmin

from .models import (
    ActivityLog,
    FrameObject,
    ObjectTrack,
    OperationSnapshot,
    Project,
    VideoFrame,
)


@admin.register(Project)
class ProjectAdmin(BaseAdmin):
    list_display = (
        "project_id",
        "project_name",
        "video_name",
        "video_path",
        "status",
        "trk_file_name",
        "trk_file_path",
        "project_status",
        "fps",
        "width",
        "height",
        "duration",
        "total_frames",
        "created_at",
        "updated_at",
    )

    search_fields = ("project_name", "video_name")
    list_filter = ("status", "project_status")
    readonly_fields = ("created_at", "updated_at")


@admin.register(VideoFrame)
class VideoFrameAdmin(BaseAdmin):
    list_display = (
        "id",
        "get_project_name",
        "frame_no",
        "frame_timestamp",
        "trk_timestamp",
    )

    search_fields = ("project_id__project_id", "frame_no")
    list_filter = ("project_id__project_id",)
    readonly_fields = ()

    @admin.display(
        description="Project Name",
        ordering="project_id__project_name",
    )
    def get_project_name(self, obj):
        return obj.project_id.project_name


@admin.register(FrameObject)
class FrameObjectAdmin(BaseAdmin):
    list_display = (
        "id",
        "get_project_name",
        "get_frame_number",
        "object_id",
        "coordinates",
        "confidence",
        "tag",
        "timestamp",
        "is_active",
    )

    search_fields = ("frame__id", "object_id")
    list_filter = ("frame__project_id__project_id", "object_id")
    readonly_fields = ()

    @admin.display(
        description="Project Name",
        ordering="frame__project_id__project_name",
    )
    def get_project_name(self, obj):
        return obj.frame.project_id.project_name

    @admin.display(
        description="Frame Number",
        ordering="frame__frame_no",
    )
    def get_frame_number(self, obj):
        return obj.frame.frame_no


@admin.register(ObjectTrack)
class ObjectTrackAdmin(BaseAdmin):
    list_display = (
        "track_id",
        "get_project_id",
        "object_id",
        "start_frame",
        "end_frame",
        "object_status",
        "operation_note",
    )

    search_fields = ("track_id", "project_id__project_id", "object_id")
    list_filter = ("project_id__project_id", "object_id")

    readonly_fields = ()

    # Custom column for raw integer project_id
    @admin.display(description="Project ID")
    def get_project_id(self, obj):
        return obj.project_id_id  # <-- REAL integer FK


@admin.register(ActivityLog)
class ActivityLogAdmin(BaseAdmin):
    list_display = (
        "activity_id",
        "project_id",
        "objects_data",
        "operation",
        "activity_created_at",
        "activity_updated_at",
        "is_applied",
    )

    search_fields = ("activity_id", "project_id__project_id", "operation", "is_applied")
    list_filter = ("project_id__project_id", "operation", "is_applied")
    readonly_fields = ("activity_created_at", "activity_updated_at", "is_applied")


@admin.register(OperationSnapshot)
class OperationSnapshotAdmin(BaseAdmin):
    list_display = (
        "id",
        "activity",
        "before_state",
        "after_state",
        "created_at",
    )

    search_fields = ("activity__activity_id",)
    list_filter = ("activity__activity_id",)
    readonly_fields = ("created_at", "activity", "before_state", "after_state")
