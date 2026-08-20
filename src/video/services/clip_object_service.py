from django.db import transaction
from django.db.models import Max
from rest_framework import serializers

from ..models import FrameObject, ObjectTrack, VideoFrame
from .snapshot_builder import SnapshotBuilder
from .snapshot_logger import SnapshotLogger


class ClipConfigCalculator:
    """Build and validate the immutable configuration for a clip."""

    @staticmethod
    def calculate(*, obj_track: ObjectTrack, start_frame: int, end_frame: int) -> dict:
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

        return {
            "clip_start_frame": start_frame,
            "clip_end_frame": end_frame,
        }


class ClipMutator:
    """Perform only the database mutations required by a clip."""

    @staticmethod
    def generate_new_object_id(project_id: int) -> int:
        return (
            ObjectTrack.objects.filter(project_id_id=project_id).aggregate(
                maximum=Max("object_id")
            )["maximum"]
            or 0
        ) + 1

    @staticmethod
    def update_frame_objects(
        *, frame_ids: list, old_object_id: int, new_object_id: int
    ) -> int:
        return FrameObject.objects.filter(
            frame_id__in=frame_ids,
            object_id=old_object_id,
            is_active=True,
        ).update(object_id=new_object_id)

    @staticmethod
    def create_object_track(
        *, project_id: int, new_object_id: int, config: dict
    ) -> ObjectTrack:
        clip_start = config["clip_start_frame"]
        clip_end = config["clip_end_frame"]
        return ObjectTrack.objects.create(
            project_id_id=project_id,
            object_id=new_object_id,
            start_frame=clip_start,
            end_frame=clip_end,
            object_status=1,
            operation_note=f"clip_from_{clip_start}_to_{clip_end}",
        )


class ClipSnapshotManager:
    """Capture clip state using the existing undo/redo snapshot schema."""

    @staticmethod
    def build_before_snapshot(
        *, frame_ids: list, old_object_id: int, obj_track: ObjectTrack
    ) -> dict:
        return SnapshotBuilder.build(
            before_qs_map={
                "FrameObject": (
                    FrameObject.objects.filter(
                        frame_id__in=frame_ids,
                        object_id=old_object_id,
                        is_active=True,
                    ),
                    ("id", "object_id"),
                ),
                # Capture the relevant original track for the audit snapshot.
                # It is never mutated or replayed by clip undo/redo.
                "ObjectTrack": (
                    ObjectTrack.objects.filter(track_id=obj_track.track_id),
                    (
                        "track_id",
                        "project_id_id",
                        "object_id",
                        "start_frame",
                        "end_frame",
                        "object_status",
                        "operation_note",
                    ),
                ),
            },
            after_qs_map={},
        )

    @staticmethod
    def build_after_snapshot(
        *, frame_ids: list, new_object_id: int, new_track: ObjectTrack
    ) -> dict:
        return SnapshotBuilder.build(
            before_qs_map={},
            after_qs_map={
                "FrameObject": (
                    FrameObject.objects.filter(
                        frame_id__in=frame_ids,
                        object_id=new_object_id,
                        is_active=True,
                    ),
                    ("id", "object_id"),
                ),
                "ObjectTrack": ObjectTrack.objects.filter(
                    track_id=new_track.track_id
                ),
            },
        )


class ClipResponseBuilder:
    @staticmethod
    def build(
        *, obj_track: ObjectTrack, new_track: ObjectTrack, rows_updated: int
    ) -> dict:
        return {
            "old_object_id": obj_track.object_id,
            "new_object_id": new_track.object_id,
            "clip_range": f"{new_track.start_frame}-{new_track.end_frame}",
            "rows_updated_in_frame_object": rows_updated,
            "original_object_track": {
                "object_id": obj_track.object_id,
                "start_frame": obj_track.start_frame,
                "end_frame": obj_track.end_frame,
            },
            "clipped_object_track": {
                "object_id": new_track.object_id,
                "start_frame": new_track.start_frame,
                "end_frame": new_track.end_frame,
            },
        }


class ClipObjectService:
    """Orchestrate clipping without modifying the original ObjectTrack."""

    @classmethod
    def execute(
        cls,
        *,
        project_id: int,
        object_id: int,
        start_frame: int,
        end_frame: int,
        obj_track: ObjectTrack,
    ) -> dict:
        config = ClipConfigCalculator.calculate(
            obj_track=obj_track,
            start_frame=start_frame,
            end_frame=end_frame,
        )

        with transaction.atomic():
            # Serialize object-id allocation within this project's current tracks.
            list(
                ObjectTrack.objects.select_for_update()
                .filter(project_id_id=project_id)
                .values_list("track_id", flat=True)
            )

            # Re-read and lock the track so validation remains true through mutation.
            try:
                locked_track = ObjectTrack.objects.select_for_update().get(
                    track_id=obj_track.track_id,
                    project_id_id=project_id,
                    object_id=object_id,
                    object_status=1,
                )
            except ObjectTrack.DoesNotExist:
                raise serializers.ValidationError("Active object not found.")

            config = ClipConfigCalculator.calculate(
                obj_track=locked_track,
                start_frame=start_frame,
                end_frame=end_frame,
            )

            frame_ids = list(
                VideoFrame.objects.filter(
                    project_id_id=project_id,
                    frame_no__gte=config["clip_start_frame"],
                    frame_no__lte=config["clip_end_frame"],
                ).values_list("id", flat=True)
            )
            expected_frame_count = end_frame - start_frame + 1
            if len(frame_ids) != expected_frame_count:
                raise serializers.ValidationError(
                    {"clip_range": "The selected frame range does not belong to the project."}
                )

            before_state = ClipSnapshotManager.build_before_snapshot(
                frame_ids=frame_ids,
                old_object_id=object_id,
                obj_track=locked_track,
            )
            affected_rows = before_state["FrameObject"]["deleted"]
            if not affected_rows:
                raise serializers.ValidationError(
                    {"clip_range": "No active object rows found in the selected range."}
                )

            new_object_id = ClipMutator.generate_new_object_id(project_id)
            rows_updated = ClipMutator.update_frame_objects(
                frame_ids=frame_ids,
                old_object_id=object_id,
                new_object_id=new_object_id,
            )
            if rows_updated == 0:
                raise serializers.ValidationError(
                    {"clip_range": "No active object rows found in the selected range."}
                )
            if rows_updated != len(affected_rows):
                raise serializers.ValidationError(
                    {"clip_range": "Object rows changed while the clip was in progress."}
                )

            new_track = ClipMutator.create_object_track(
                project_id=project_id,
                new_object_id=new_object_id,
                config=config,
            )
            after_state = ClipSnapshotManager.build_after_snapshot(
                frame_ids=frame_ids,
                new_object_id=new_object_id,
                new_track=new_track,
            )

            SnapshotLogger.log(
                project_id=project_id,
                operation="clip",
                before_state=before_state,
                after_state=after_state,
                objects_data={
                    "old_object_id": object_id,
                    "new_object_id": new_object_id,
                    "clip_start_frame": config["clip_start_frame"],
                    "clip_end_frame": config["clip_end_frame"],
                },
            )

        return ClipResponseBuilder.build(
            obj_track=locked_track,
            new_track=new_track,
            rows_updated=rows_updated,
        )
