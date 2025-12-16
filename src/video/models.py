from django.db import models

# Create your models here.
# from src.common.models import BaseModel

# models.py
class Project(models.Model):
    project_id = models.AutoField(primary_key=True)

    # Basic project information
    project_name = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True)

    # Video metadata
    video_id = models.IntegerField(null=True, blank=True)
    video_name = models.CharField(max_length=255, null=True, blank=True)
    video_path = models.TextField(null=True, blank=True)
    width = models.IntegerField(null=True, blank=True)
    height = models.IntegerField(null=True, blank=True)
    fps = models.FloatField(null=True, blank=True)
    total_frames = models.IntegerField(null=True, blank=True)

    # Status of the project--database status data insert
    status = models.CharField(max_length=50, null=True, blank=True)

    trk_file_name = models.CharField(max_length=255, null=True, blank=True)
    trk_file_path = models.TextField(null=True, blank=True)

    # SIMPLE FIELD — default = inprogress-->project status
    project_status = models.CharField(max_length=20, default="inprogress")

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "project"

    def __str__(self):
        return self.project_name


class VideoData(models.Model):
    id = models.AutoField(primary_key=True)
    video_id = models.IntegerField()
    frame_no = models.IntegerField()
    frame_timestamp = models.FloatField()

    # JSON fields for storing arrays from TRK file
    confidence = models.JSONField(null=True, blank=True)
    tag = models.JSONField(null=True, blank=True)
    timestamp = models.JSONField(null=True, blank=True)

    # 10 object slots
    object_1_id = models.IntegerField(null=True, blank=True)
    object_1_coordinates = models.JSONField(null=True, blank=True)
    object_2_id = models.IntegerField(null=True, blank=True)
    object_2_coordinates = models.JSONField(null=True, blank=True)
    object_3_id = models.IntegerField(null=True, blank=True)
    object_3_coordinates = models.JSONField(null=True, blank=True)
    object_4_id = models.IntegerField(null=True, blank=True)
    object_4_coordinates = models.JSONField(null=True, blank=True)
    object_5_id = models.IntegerField(null=True, blank=True)
    object_5_coordinates = models.JSONField(null=True, blank=True)
    object_6_id = models.IntegerField(null=True, blank=True)
    object_6_coordinates = models.JSONField(null=True, blank=True)
    object_7_id = models.IntegerField(null=True, blank=True)
    object_7_coordinates = models.JSONField(null=True, blank=True)
    object_8_id = models.IntegerField(null=True, blank=True)
    object_8_coordinates = models.JSONField(null=True, blank=True)
    object_9_id = models.IntegerField(null=True, blank=True)
    object_9_coordinates = models.JSONField(null=True, blank=True)
    object_10_id = models.IntegerField(null=True, blank=True)
    object_10_coordinates = models.JSONField(null=True, blank=True)

    trk_timestamp = models.FloatField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "video_data"
        indexes = [
            models.Index(fields=["video_id", "frame_no"]),
        ]

    def __str__(self):
        return f"Video {self.video_id} | Frame {self.frame_no}"

class ObjectTrack(models.Model):
    track_id = models.AutoField(primary_key=True)

    project_id = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="object_tracks")
    object_id = models.IntegerField()

    start_frame = models.IntegerField()
    end_frame = models.IntegerField()

    # 1 = active, 0 = inactive
    object_status = models.IntegerField(default=1)
    # e.g. "link", "swap", etc.
    operation_note = models.CharField(max_length=255, null=True, blank=True)

    class Meta:
        db_table = "object_track"
        indexes = [
            models.Index(fields=["project_id", "object_id"]),
        ]
    def __str__(self):
        return f"Object {self.object_id} | ProjectID {self.project_id_id}"


class ActivityLog(models.Model):
    activity_id = models.AutoField(primary_key=True)
    project_id = models.IntegerField()
    objects_data = models.JSONField()  
    operation = models.CharField(max_length=255)
    activity_updated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "activity_log"


class Video(models.Model):
    id = models.AutoField(primary_key=True)
    project_name = models.CharField(max_length=255)
    video_file_title = models.CharField(max_length=255, blank=True, null=True)
    video_file = models.FileField(upload_to="videos/")
    trk_file_title = models.CharField(max_length=255, blank=True, null=True)
    trk_file = models.FileField(upload_to="trks/")
    description = models.TextField(blank=True, null=True)  # Add this
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title or f"Video {self.id}"

        