from django.contrib import admin
from .models import (
    Project,
    VideoFrame,
    FrameObject,
    ObjectTrack,
    ActivityLog,
    OperationSnapshot,
)
from src.common.admin import BaseAdmin

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
        "project_id",
        "frame_no",
        "frame_timestamp",
        "trk_timestamp",
    )

    search_fields = ("project_id__project_id", "frame_no")
    list_filter = ("project_id__project_id",)
    readonly_fields = ()

@admin.register(FrameObject)
class FrameObjectAdmin(BaseAdmin):
    list_display = (
        "id",
        "frame",
        "object_id",
        "coordinates",
        "confidence",
        "tag",
        "timestamp",
    )

    search_fields = ("frame__id", "object_id")
    list_filter = ("object_id",)
    readonly_fields = ()

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
    def get_project_id(self, obj):
        return obj.project_id_id   # <-- REAL integer FK
    get_project_id.short_description = "Project ID"


@admin.register(ActivityLog)
class ActivityLogAdmin(BaseAdmin):
    list_display = (
        "activity_id",
        "project_id",
        "objects_data",
        "operation",
        "activity_created_at",
        "activity_updated_at",
    )

    search_fields = ("activity_id", "project_id__project_id", "operation")
    list_filter = ("project_id__project_id", "operation")
    readonly_fields = ("activity_created_at", "activity_updated_at")


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
    readonly_fields = ("created_at",)
