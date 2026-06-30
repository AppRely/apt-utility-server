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

    # SIMPLE FIELD — default = inprogress-->project status
    project_status = models.CharField(max_length=50, default="inprogress")
    # Confusion recalculation status: "FAILED", "PROCESSING", "COMPLETED"
    confusion_status = models.CharField(max_length=20, default="COMPLETED",)
    skeleton_graph = models.JSONField(null=True, blank=True, default=list )
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
    is_active = models.BooleanField(default=True)  # for soft delete
    is_interpolated = models.BooleanField(default=False)
    class Meta:
        db_table = "frame_object"
        indexes = [
            # models.Index(fields=["frame", "object_id", "is_active"]),
            models.Index(fields=["object_id"]),
            models.Index(fields=["frame", "is_active"]),

            # NEW OPTIMIZED INDEX
            models.Index(
                fields=[
                    "is_active",
                    "frame",
                    "object_id",
                ]
            ),
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

    project_id = models.ForeignKey(Project, on_delete=models.CASCADE)
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


class FrameConfusion(models.Model):

    id = models.BigAutoField(
        primary_key=True
    )

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="frame_confusions",
    )

    # current frame (t)
    frame_no = models.IntegerField()

    # next frame (t + 1)
    next_frame_no = models.IntegerField()

    # current object id
    current_object_id = models.IntegerField()

    # best matched object
    best_match_object_id = models.IntegerField(
        null=True,
        blank=True,
    )

    # second competing object
    second_match_object_id = models.IntegerField(
        null=True,
        blank=True,
    )
 
    # ambiguity score
    uncertainty = models.FloatField()

    # True  -> forward match won
    # False -> backward match won
    is_forward = models.BooleanField(
        default=True
    )

    # best matching distance/cost
    best_match_cost = models.FloatField(
        null=True,
        blank=True,
    )

    # second matching distance/cost
    second_match_cost = models.FloatField(
        null=True,
        blank=True,
    )


    # nearby competing objects
    nearby_object_count = models.IntegerField(
        default=0
    )

    # overall confusion score
    confusion_score = models.FloatField(
        null=True,
        blank=True,
    )

    # crowded region flag
    is_crowded = models.BooleanField(
        default=False
    )
    nearby_object_ids = models.JSONField(
        null=True,
        blank=True,
        default=list,
        help_text="List of object IDs that are within the crowd radius"
    )

    # event category
    # Examples:
    # CROWD
    # HIGH_UNCERTAINTY
    # NORMAL
    event_type = models.CharField(
        max_length=50,
        null=True,
        blank=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    updated_at = models.DateTimeField(
        auto_now=True
    )

    class Meta:

        db_table = "frame_confusion"

        indexes = [
            models.Index(fields=["project", "frame_no",] ),
            models.Index(fields=["project", "current_object_id",]),
            models.Index(fields=["project", "event_type",]),
            models.Index(fields=["project", "-confusion_score",] ),
        ]

    def __str__(self):

        return (
            f"Project={self.project_id} | "
            f"Frame={self.frame_no} | "
            f"Object={self.current_object_id} | "
            f"Event={self.event_type}"
        )



class ObjectLinkingSuggestion(models.Model):
    id = models.BigAutoField(primary_key=True)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="linking_suggestions")
    source_track = models.ForeignKey('ObjectTrack', on_delete=models.CASCADE, related_name="as_source")
    source_end_frame = models.IntegerField()
    target_track = models.ForeignKey('ObjectTrack', on_delete=models.CASCADE, related_name="as_target")
    target_start_frame = models.IntegerField()
    distance = models.FloatField()
    match_score = models.FloatField()
    is_best_match = models.BooleanField(default=False)
    rank = models.IntegerField(null=True, blank=True)
    uncertainty = models.FloatField(null=True, blank=True)
    confusion_score = models.FloatField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "object_linking_suggestion"
        indexes = [
            models.Index(fields=["project", "source_track"]),
            models.Index(fields=["project", "target_track"]),
            models.Index(fields=["source_track", "is_best_match"]),
        ]