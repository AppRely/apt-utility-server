from django.db import transaction
from django.db.models import Max

from ..models import FrameObject, ObjectTrack

from .snapshot_builder import SnapshotBuilder
from .snapshot_logger import SnapshotLogger


class BreakObjectService:
    """
    Service responsible for executing Break Object operation.

    Supports:
        - Break Before
        - Break After
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
        """
        Execute Break Object operation.
        """

        start_frame = obj_track.start_frame
        end_frame = obj_track.end_frame

        config = cls._calculate_break_config(
            start_frame=start_frame,
            end_frame=end_frame,
            break_frame=break_frame,
            break_type=break_type,
        )

        with transaction.atomic():

            # The snapshot and its corresponding mutation must be atomic.  A
            # snapshot captured before entering the transaction can otherwise
            # restore state that was already changed by another request.
            before_state = cls._build_before_snapshot(
                project_id=project_id,
                object_id=object_id,
                obj_track=obj_track,
                config=config,
            )

            # ------------------------------------------
            # Generate New Object ID
            # ------------------------------------------
            new_object_id = (
                ObjectTrack.objects.filter(
                    project_id_id=project_id
                ).aggregate(
                    m=Max("object_id")
                )["m"]
                or 0
            ) + 1

            # ------------------------------------------
            # Update FrameObject
            # ------------------------------------------
            rows_updated = cls._update_frame_objects(
                project_id=project_id,
                object_id=object_id,
                new_object_id=new_object_id,
                config=config,
            )

            # ------------------------------------------
            # Update ObjectTrack
            # ------------------------------------------
            new_track = cls._update_object_tracks(
                project_id=project_id,
                obj_track=obj_track,
                new_object_id=new_object_id,
                config=config,
            )

            # ------------------------------------------
            # Capture AFTER Snapshot
            # ------------------------------------------
            after_state = cls._build_after_snapshot(
                project_id=project_id,
                obj_track=obj_track,
                new_track=new_track,
                new_object_id=new_object_id,
                config=config,
            )

            # ------------------------------------------
            # Activity Log
            # ------------------------------------------
            cls._log_activity(
                project_id=project_id,
                object_id=object_id,
                new_object_id=new_object_id,
                before_state=before_state,
                after_state=after_state,
                config=config,
            )

        # ------------------------------------------
        # Response
        # ------------------------------------------
        return cls._build_response(
            object_id=object_id,
            new_object_id=new_object_id,
            rows_updated=rows_updated,
            config=config,
        )
    

    @staticmethod
    def _calculate_break_config(
        *,
        start_frame: int,
        end_frame: int,
        break_frame: int,
        break_type: str,
    ) -> dict:
        """
        Calculate the configuration required for Break Before / Break After.

        Break After:
            Original : start_frame -> break_frame - 1
            New      : break_frame -> end_frame

        Break Before:
            New      : start_frame -> break_frame
            Original : break_frame + 1 -> end_frame
        """

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
    
    @classmethod
    def _build_before_snapshot(
        cls,
        *,
        project_id: int,
        object_id: int,
        obj_track: ObjectTrack,
        config: dict,
    ):
        """
        Capture the database state before performing the break operation.
        """

        return SnapshotBuilder.build(
            before_qs_map={
                "FrameObject": FrameObject.objects.filter(
                    frame__project_id_id=project_id,
                    object_id=object_id,
                    frame__frame_no__gte=config["frame_move_start"],
                    frame__frame_no__lte=config["frame_move_end"],
                ),
                "ObjectTrack": ObjectTrack.objects.filter(
                    track_id=obj_track.track_id,
                ),
            },
            after_qs_map={},
        )
    
    @classmethod
    def _update_frame_objects(
        cls,
        *,
        project_id: int,
        object_id: int,
        new_object_id: int,
        config: dict,
    ) -> int:
        """
        Update FrameObject records by assigning them to the new object.

        Returns:
            Number of updated rows.
        """

        rows_updated = (
            FrameObject.objects.filter(
                frame__project_id_id=project_id,
                object_id=object_id,
                frame__frame_no__gte=config["frame_move_start"],
                frame__frame_no__lte=config["frame_move_end"],
            ).update(
                object_id=new_object_id
            )
        )

        return rows_updated
    

    @classmethod
    def _update_object_tracks(
        cls,
        *,
        project_id: int,
        obj_track: ObjectTrack,
        new_object_id: int,
        config: dict,
    ) -> ObjectTrack:
        """
        Update the existing ObjectTrack and create the new ObjectTrack.

        Existing ObjectTrack retains the original object_id.
        New ObjectTrack receives the newly generated object_id.
        """

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
        new_track = ObjectTrack.objects.create(
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

        return new_track
    
    @classmethod
    def _build_after_snapshot(
        cls,
        *,
        project_id: int,
        obj_track: ObjectTrack,
        new_track: ObjectTrack,
        new_object_id: int,
        config: dict,
    ):
        """
        Capture the database state after the break operation.
        """

        return SnapshotBuilder.build(
            before_qs_map={
                "ObjectTrack": ObjectTrack.objects.filter(
                    track_id=obj_track.track_id
                ),
            },
            after_qs_map={
                "FrameObject": FrameObject.objects.filter(
                    frame__project_id_id=project_id,
                    object_id=new_object_id,
                    frame__frame_no__gte=config["frame_move_start"],
                    frame__frame_no__lte=config["frame_move_end"],
                ),
                "ObjectTrack": ObjectTrack.objects.filter(
                    track_id__in=[
                        obj_track.track_id,
                        new_track.track_id,
                    ]
                ),
            },
        )
    
    @classmethod
    def _log_activity(
        cls,
        *,
        project_id: int,
        object_id: int,
        new_object_id: int,
        before_state: dict,
        after_state: dict,
        config: dict,
    ):
        """
        Log the break operation and persist the corresponding snapshots.
        """

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

    
    @staticmethod
    def _build_response(
        *,
        object_id: int,
        new_object_id: int,
        rows_updated: int,
        config: dict,
    ) -> dict:
        """
        Build API response.
        """

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
