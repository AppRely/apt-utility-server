from django.contrib import admin
from .models import VideoData, Project
from src.common.admin import BaseAdmin


@admin.register(VideoData)
class VideoDataAdmin(BaseAdmin):

    list_display = (
        "id",
        "video_id",
        "frame_no",
        "frame_timestamp",
        "trk_timestamp",
        "object_1_id",
        "object_1_coordinates",
        "object_2_id",
        "object_2_coordinates",
        "object_3_id",
        "object_3_coordinates",
        "object_4_id",
        "object_4_coordinates",
        "object_5_id",
        "object_5_coordinates",
        "object_6_id",
        "object_6_coordinates",
        "object_7_id",
        "object_7_coordinates",
        "object_8_id",
        "object_8_coordinates",
        "object_9_id",
        "object_9_coordinates",
        "object_10_id",
        "object_10_coordinates",
        "tag",
        "timestamp",
        "confidence",
        "created_at",
        "updated_at",
    )

    list_filter = ("video_id", "frame_no", "object_1_id", "object_2_id", "object_3_id", "object_4_id", "object_5_id", "object_6_id", "object_7_id", "object_8_id", "object_9_id", "object_10_id", "tag", "timestamp", "confidence")  
    search_fields = ("video_id", "frame_no", "object_1_id", "object_2_id", "object_3_id", "object_4_id", "object_5_id", "object_6_id", "object_7_id", "object_8_id", "object_9_id", "object_10_id", "tag", "timestamp", "confidence")

    readonly_fields = ("created_at", "updated_at")


@admin.register(Project)
class ProjectAdmin(BaseAdmin):
    list_display = (
        "project_id",
        "project_name",
        "video_id",
        "video_name",
        "status",
        "fps",
        "total_frames",
        "created_at",
        "updated_at",
    )

    search_fields = ("project_name", "video_name")
    list_filter = ("status",)
    readonly_fields = ("created_at", "updated_at")
