from django.db import models

class Project(models.Model):
    project_id = models.AutoField(primary_key=True)

    # Basic project information
    project_name = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True)

    video_name = models.CharField(max_length=255, null=True, blank=True)
    video_path = models.TextField(null=True, blank=True)
    # Status of the project--database status data insert
    status = models.CharField(max_length=50, null=True, blank=True)

    trk_file_name = models.CharField(max_length=255, null=True, blank=True)
    trk_file_path = models.TextField(null=True, blank=True)

    # Video Metadata
    fps = models.FloatField(null=True, blank=True)
    width = models.IntegerField(null=True, blank=True)
    height = models.IntegerField(null=True, blank=True)
    duration = models.FloatField(null=True, blank=True)
    total_frames = models.IntegerField(null=True, blank=True)
    skeleton_points = models.JSONField(null=True, blank=True)
    # SIMPLE FIELD — default = inprogress-->project status
    project_status = models.CharField(max_length=50, default="inprogress")

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "project"

    def __str__(self):
        return self.project_name

class VideoFrame(models.Model):
    id = models.AutoField(primary_key=True)

    project_id = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="frames")
    frame_no = models.IntegerField()
    frame_timestamp = models.FloatField(null=True, blank=True)
    trk_timestamp = models.FloatField(null=True, blank=True)

    class Meta:
        db_table = "video_frame"
        unique_together = ("project_id", "frame_no")
        indexes = [
            models.Index(fields=["project_id", "frame_no"]),
        ]

    def __str__(self):
        return f"{self.project_id.project_name} | Frame {self.frame_no}"


class FrameObject(models.Model):
    id = models.AutoField(primary_key=True)

    frame = models.ForeignKey(VideoFrame, on_delete=models.CASCADE, related_name="frame_objects")

    object_id = models.IntegerField()

    coordinates = models.JSONField(null=True, blank=True)
    confidence = models.JSONField(null=True, blank=True)
    tag = models.JSONField(null=True, blank=True)
    timestamp = models.JSONField(null=True, blank=True)
    is_active = models.BooleanField(default=True) #for soft delete
    class Meta:
        db_table = "frame_object"
        indexes = [
            models.Index(fields=["frame", "object_id"]),
            models.Index(fields=["object_id"]),
        ]

    def __str__(self):
        return f"Frame {self.frame_id} | Object {self.object_id}"

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
            models.Index(fields=["project_id", "object_status"]),
        ]
        
    def __str__(self):
        return f"Object {self.object_id} | Project {self.project_id_id}"
    
class ActivityLog(models.Model):
    activity_id = models.AutoField(primary_key=True)

    project_id = models.ForeignKey(Project, on_delete=models.CASCADE )
    objects_data = models.JSONField()  
    operation = models.CharField(max_length=255)
    activity_created_at = models.DateTimeField(auto_now_add=True)
    activity_updated_at = models.DateTimeField(auto_now=True)
    
    #  REQUIRED FOR UNDO / REDO
    is_applied = models.BooleanField(default=True)
    
    class Meta:
        db_table = "activity_log"
        indexes = [
            models.Index(fields=["project_id", "is_applied"]),
        ]


class OperationSnapshot(models.Model):
    id = models.AutoField(primary_key=True)

    activity = models.ForeignKey(ActivityLog, on_delete=models.CASCADE, related_name="snapshots")

    before_state = models.JSONField()
    after_state = models.JSONField()

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "operation_snapshot"

