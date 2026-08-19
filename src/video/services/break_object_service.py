from django.db import transaction
from django.db.models import Max

from ..models import FrameObject, ObjectTrack, VideoFrame

from .snapshot_builder import SnapshotBuilder
from .snapshot_logger import SnapshotLogger


class BreakConfigCalculator:
    """
    Calculates the configuration required for Break Before / Break After operations.
    """
    @staticmethod
    def calculate(*, start_frame: int, end_frame: int, break_frame: int, break_type: str) -> dict:
        if break_type not in ("before", "after"):
            raise ValueError(
                f"Unsupported break_type: {break_type}"
            )

        if break_type == "before":
            return {
                "operation": "break_before",
                "break_frame": break_frame,

                # Existing ObjectTrack
                "old_track_start": break_frame + 1,
                "old_track_end": end_frame,

                # New ObjectTrack
                "new_track_start": start_frame,
                "new_track_end": break_frame,

                # FrameObject rows to move
                "frame_move_start": start_frame,
                "frame_move_end": break_frame,
            }

        # -----------------------------
        # Break After
        # -----------------------------
        return {
            "operation": "break_after",
            "break_frame": break_frame,

            # Existing ObjectTrack
            "old_track_start": start_frame,
            "old_track_end": break_frame - 1,

            # New ObjectTrack
            "new_track_start": break_frame,
            "new_track_end": end_frame,

            # FrameObject rows to move
            "frame_move_start": break_frame,
            "frame_move_end": end_frame,
        }


class BreakMutator:
    """
    Responsible for executing database mutations for the break operations.
    """
    @staticmethod
    def generate_new_object_id(project_id: int) -> int:
        return (
            ObjectTrack.objects.filter(
                project_id_id=project_id
            ).aggregate(
                m=Max("object_id")
            )["m"]
            or 0
        ) + 1

    @staticmethod
    def update_frame_objects(*, project_id: int, object_id: int, new_object_id: int, config: dict, frame_ids: list) -> int:
        return FrameObject.objects.filter(
            frame_id__in=frame_ids,
            object_id=object_id,
        ).update(
            object_id=new_object_id
        )

    @staticmethod
    def update_object_tracks(*, project_id: int, obj_track: ObjectTrack, new_object_id: int, config: dict) -> ObjectTrack:
        operation = config["operation"]

        # ------------------------------------------
        # Update Existing ObjectTrack
        # ------------------------------------------
        obj_track.start_frame = config["old_track_start"]
        obj_track.end_frame = config["old_track_end"]
        obj_track.operation_note = (
            f"{operation}_from_"
            f"{config['old_track_start']}_"
            f"to_{config['old_track_end']}"
        )

        obj_track.save(
            update_fields=[
                "start_frame",
                "end_frame",
                "operation_note",
            ]
        )

        # ------------------------------------------
        # Create New ObjectTrack
        # ------------------------------------------
        return ObjectTrack.objects.create(
            project_id_id=project_id,
            object_id=new_object_id,
            start_frame=config["new_track_start"],
            end_frame=config["new_track_end"],
            object_status=1,
            operation_note=(
                f"{operation}_from_"
                f"{config['new_track_start']}_"
                f"to_{config['new_track_end']}"
            ),
        )


class BreakSnapshotManager:
    """
    Responsible for capturing before and after snapshots.
    """
    @staticmethod
    def build_before_snapshot(*, project_id: int, object_id: int, obj_track: ObjectTrack, config: dict, frame_ids: list) -> dict:
        return SnapshotBuilder.build(
            before_qs_map={
                "FrameObject": (FrameObject.objects.filter(
                    frame_id__in=frame_ids,
                    object_id=object_id,
                ), ("id", "object_id")),
                "ObjectTrack": (ObjectTrack.objects.filter(
                    track_id=obj_track.track_id,
                ), ("track_id", "start_frame", "end_frame", "operation_note")),
            },
            after_qs_map={},
        )

    @staticmethod
    def build_after_snapshot(*, project_id: int, obj_track: ObjectTrack, new_track: ObjectTrack, new_object_id: int, config: dict, frame_ids: list) -> dict:
        frame_state = SnapshotBuilder.build(
            before_qs_map={
                "ObjectTrack": (ObjectTrack.objects.filter(
                    track_id=obj_track.track_id
                ), ("track_id", "start_frame", "end_frame", "operation_note")),
            },
            after_qs_map={
                "FrameObject": (FrameObject.objects.filter(
                    frame_id__in=frame_ids,
                    object_id=new_object_id,
                ), ("id", "object_id")),
            },
        )
        # The old track is only updated, while the new track needs to be
        # recreated by redo. Keep the latter complete and the former narrow.
        frame_state["ObjectTrack"] = {
            "deleted": [],
            "updated": [],
            "created": [
                {
                    "track_id": obj_track.track_id,
                    "start_frame": obj_track.start_frame,
                    "end_frame": obj_track.end_frame,
                    "operation_note": obj_track.operation_note,
                },
                SnapshotBuilder.capture(ObjectTrack.objects.filter(track_id=new_track.track_id))[0],
            ],
        }
        return frame_state


class BreakResponseBuilder:
    """
    Responsible for building the service response payload.
    """
    @staticmethod
    def build(*, object_id: int, new_object_id: int, rows_updated: int, config: dict) -> dict:
        return {
            "old_object_id": object_id,
            "new_object_id": new_object_id,
            "break_type": (
                "before"
                if config["operation"] == "break_before"
                else "after"
            ),
            "old_range": (
                f"{config['old_track_start']}-"
                f"{config['old_track_end']}"
            ),
            "new_range": (
                f"{config['new_track_start']}-"
                f"{config['new_track_end']}"
            ),
            "rows_updated_in_frame_object": rows_updated,
        }


class BreakObjectService:
    """
    Service responsible for executing the Break Object operation.
    Delegates configuration, mutations, snapshots, and response mapping to subcomponents (SOLID).
    """
    @classmethod
    def execute(
        cls,
        *,
        project_id: int,
        object_id: int,
        break_frame: int,
        break_type: str,
        obj_track: ObjectTrack,
    ):
        config = BreakConfigCalculator.calculate(
            start_frame=obj_track.start_frame,
            end_frame=obj_track.end_frame,
            break_frame=break_frame,
            break_type=break_type,
        )

        # Pre-fetch frame_ids for the affected range to avoid slow JOIN queries
        frame_ids = list(
            VideoFrame.objects.filter(
                project_id_id=project_id,
                frame_no__gte=config["frame_move_start"],
                frame_no__lte=config["frame_move_end"]
            ).values_list("id", flat=True)
        )

        with transaction.atomic():
            before_state = BreakSnapshotManager.build_before_snapshot(
                project_id=project_id,
                object_id=object_id,
                obj_track=obj_track,
                config=config,
                frame_ids=frame_ids,
            )

            new_object_id = BreakMutator.generate_new_object_id(project_id)

            rows_updated = BreakMutator.update_frame_objects(
                project_id=project_id,
                object_id=object_id,
                new_object_id=new_object_id,
                config=config,
                frame_ids=frame_ids,
            )

            new_track = BreakMutator.update_object_tracks(
                project_id=project_id,
                obj_track=obj_track,
                new_object_id=new_object_id,
                config=config,
            )

            after_state = BreakSnapshotManager.build_after_snapshot(
                project_id=project_id,
                obj_track=obj_track,
                new_track=new_track,
                new_object_id=new_object_id,
                config=config,
                frame_ids=frame_ids,
            )

            SnapshotLogger.log(
                project_id=project_id,
                operation=config["operation"],
                before_state=before_state,
                after_state=after_state,
                objects_data={
                    "old_object_id": object_id,
                    "new_object_id": new_object_id,
                    "break_frame": config["break_frame"],
                    "old_start_frame": config["old_track_start"],
                    "old_end_frame": config["old_track_end"],
                    "new_start_frame": config["new_track_start"],
                    "new_end_frame": config["new_track_end"],
                    "break_type": (
                        "before"
                        if config["operation"] == "break_before"
                        else "after"
                    ),
                },
            )

        return BreakResponseBuilder.build(
            object_id=object_id,
            new_object_id=new_object_id,
            rows_updated=rows_updated,
            config=config,
        )
