from django.db import transaction
from rest_framework import serializers

from ..models import (
    FrameObject,
    ObjectTrack,
)

from .object_lifecycle_service import ObjectLifecycleService
from .snapshot_builder import SnapshotBuilder
from .snapshot_logger import SnapshotLogger


class LinkObjectService:
    """
    Handles all link related operations.

    Currently supports:
        - Normal Link

    Future:
        - Overlap Link
    """

    @classmethod
    def execute(cls, **data):

        operation = data.get("operation", "link")

        if operation == "link":
            return cls._normal_link(**data)

        overlap_info = cls._detect_overlap(
            data["object_1_track"],
            data["object_2_track"],
        )

        data["overlap_info"] = overlap_info

        if operation == "overlap":

            if not overlap_info["has_overlap"]:
                raise serializers.ValidationError(
                    "Objects do not overlap."
                )

            return cls._overlap_link(**data)

        raise serializers.ValidationError(
            "Invalid operation."
        )

    @classmethod
    def _normal_link(cls, **data):

        project_id = data["project_id"]

        obj1 = data["object_1_id"]
        obj2 = data["object_2_id"]

        start2 = data["object_2_start"]
        end2 = data["object_2_end"]

        obj1_row = data["object_1_track"]
        obj2_row = data["object_2_track"]

        with transaction.atomic():

            # ============================================
            # BEFORE SNAPSHOT
            # ============================================

            before_state = SnapshotBuilder.build(
                before_qs_map={
                    "FrameObject": FrameObject.objects.filter(
                        frame__project_id_id=project_id,
                        frame__frame_no__gte=start2,
                        frame__frame_no__lte=end2,
                        object_id=obj2,
                    ),
                    "ObjectTrack": ObjectTrack.objects.filter(
                        track_id__in=[
                            obj1_row.track_id,
                            obj2_row.track_id,
                        ]
                    ),
                },
                after_qs_map={},
            )

            # ============================================
            # UPDATE FRAME OBJECTS
            # ============================================

            rows_updated = FrameObject.objects.filter(
                frame__project_id_id=project_id,
                object_id=obj2,
                frame__frame_no__gte=start2,
                frame__frame_no__lte=end2,
            ).update(
                object_id=obj1,
            )

            if rows_updated == 0:

                raise serializers.ValidationError(
                    "No frames found for object_2 in given range."
                )

            # ============================================
            # UPDATE OBJECT TRACK
            # ============================================

            obj1_row.start_frame = min(
                obj1_row.start_frame,
                obj2_row.start_frame,
            )

            obj1_row.end_frame = max(
                obj1_row.end_frame,
                obj2_row.end_frame,
            )

            obj1_row.object_status = 1

            obj1_row.operation_note = "link_target"

            obj1_row.save(
                update_fields=[
                    "start_frame",
                    "end_frame",
                    "object_status",
                    "operation_note",
                ]
            )

            # ============================================
            # DEACTIVATE OBJECT 2
            # ============================================

            ObjectLifecycleService.deactivate_object(
                obj2_row,
                note=f"linked_into_object_{obj1}",
            )


            # ============================================
            # AFTER SNAPSHOT
            # ============================================

            after_state = SnapshotBuilder.build(
                before_qs_map={},
                after_qs_map={
                    "FrameObject": FrameObject.objects.filter(
                        frame__project_id_id=project_id,
                        frame__frame_no__gte=start2,
                        frame__frame_no__lte=end2,
                        object_id=obj1,
                    ),
                    "ObjectTrack": ObjectTrack.objects.filter(
                        track_id__in=[
                            obj1_row.track_id,
                            obj2_row.track_id,
                        ]
                    ),
                },
            )

            # ============================================
            # SNAPSHOT LOG
            # ============================================

            SnapshotLogger.log(
                project_id=project_id,
                operation="link",
                before_state=before_state,
                after_state=after_state,
                objects_data={
                    "object_1_id": obj1,
                    "object_1_start": obj1_row.start_frame,
                    "object_1_end": obj1_row.end_frame,
                    "object_2_id": obj2,
                    "object_2_start": start2,
                    "object_2_end": end2,
                },
            )

            return {
                "status": "success",
                "message": "Objects merged successfully",
                "video_id": project_id,
                "rows_updated_main_table": rows_updated,
                "object_track_object_1": {
                    "object_id": obj1,
                    "start_frame": obj1_row.start_frame,
                    "end_frame": obj1_row.end_frame,
                    "object_status": obj1_row.object_status,
                    "operation_note": obj1_row.operation_note,
                },
                "object_track_object_2": {
                    "object_id": obj2,
                    "start_frame": obj2_row.start_frame,
                    "end_frame": obj2_row.end_frame,
                    "object_status": obj2_row.object_status,
                    "operation_note": obj2_row.operation_note,
                },
            }

    # ==========================================================
    # OVERLAP LINK (Coming Next)
    # ==========================================================

    @classmethod
    def _overlap_link(cls, **data):
        """
        Overlap link operation.

        Currently this method:
        1. Validates overlap.
        2. Determines winner and loser.
        3. Returns overlap details.

        FrameObject update logic will be added next.
        """

        overlap = data["overlap_info"]

        if not overlap["has_overlap"]:
            raise serializers.ValidationError(
                "Selected objects do not overlap."
            )

        link_info = cls._get_winner_loser(data)

        winner = link_info["winner"]
        loser = link_info["loser"]

        winner_track = link_info["winner_track"]
        loser_track = link_info["loser_track"]
        cls._validate_overlap_case(winner_track, loser_track,)

        before_state = SnapshotBuilder.build(
            before_qs_map={
                "FrameObject": FrameObject.objects.filter(
                    frame__project_id_id=data["project_id"],
                    object_id__in=[
                        winner,
                        loser,
                    ],
                ),
                "ObjectTrack": ObjectTrack.objects.filter(
                    track_id__in=[
                        winner_track.track_id,
                        loser_track.track_id,
                    ]
                ),
            },
            after_qs_map={},
        )
        overlap_start = overlap["start"]
        overlap_end = overlap["end"]

        # Find loser-only frame ranges
        with transaction.atomic():
            rows_updated = cls._move_non_overlap_frames(
                project_id=data["project_id"],
                winner=winner,
                loser=loser,
                loser_track=loser_track,
                overlap_start=overlap_start,
                overlap_end=overlap_end,
            )
            if rows_updated == 0:
                raise serializers.ValidationError(
                    "No non-overlapping frames found to move."
                )

            cls._update_overlap_tracks(
                winner_track=winner_track,
                loser_track=loser_track,
                overlap_start=overlap_start,
                overlap_end=overlap_end,
            )

            after_state = SnapshotBuilder.build(
                before_qs_map={},
                after_qs_map={
                    "FrameObject": FrameObject.objects.filter(
                        frame__project_id_id=data["project_id"],
                        object_id__in=[
                            winner,
                            loser,
                        ],
                    ),
                    "ObjectTrack": ObjectTrack.objects.filter(
                        track_id__in=[
                            winner_track.track_id,
                            loser_track.track_id,
                        ]
                    ),
                },
            )
            SnapshotLogger.log(
                project_id=data["project_id"],
                operation="overlap",
                before_state=before_state,
                after_state=after_state,
                objects_data={
                    "winner_object": winner,
                    "loser_object": loser,
                    "preferred_object": data["preferred_object"],
                    "overlap_start": overlap_start,
                    "overlap_end": overlap_end,
                },
            )


        return {
            "status": "success",
            "message": "Objects overlapped successfully.",
            "video_id": data["project_id"],
            "rows_updated_main_table": rows_updated,
            "winner_object": {
                "object_id": winner,
                "start_frame": winner_track.start_frame,
                "end_frame": winner_track.end_frame,
                "operation_note": winner_track.operation_note,
            },
            "loser_object": {
                "object_id": loser,
                "start_frame": loser_track.start_frame,
                "end_frame": loser_track.end_frame,
                "operation_note": loser_track.operation_note,
            },
        }
    # ==========================================================
    # HELPER METHODS (Coming Next)
    # ==========================================================

    @staticmethod
    def _detect_overlap(
        object_1_track,
        object_2_track,
    ):
        """
        Detect overlap between two object tracks.

        Returns:
            {
                "has_overlap": bool,
                "start": int | None,
                "end": int | None,
            }
        """

        overlap_start = max(
            object_1_track.start_frame,
            object_2_track.start_frame,
        )

        overlap_end = min(
            object_1_track.end_frame,
            object_2_track.end_frame,
        )

        if overlap_start <= overlap_end:

            return {
                "has_overlap": True,
                "start": overlap_start,
                "end": overlap_end,
            }

        return {
            "has_overlap": False,
            "start": None,
            "end": None,
        }
    

    @staticmethod
    def _get_winner_loser(data):
        """
        Determine winner and loser based on the preferred object.
        """

        preferred_object = data["preferred_object"]

        object_1_id = data["object_1_id"]
        object_2_id = data["object_2_id"]

        if preferred_object == object_1_id:
            return {
                "winner": object_1_id,
                "loser": object_2_id,
                "winner_track": data["object_1_track"],
                "loser_track": data["object_2_track"],
            }

        return {
            "winner": object_2_id,
            "loser": object_1_id,
            "winner_track": data["object_2_track"],
            "loser_track": data["object_1_track"],
        }
    
    @classmethod
    def _move_non_overlap_frames(
        cls,
        *,
        project_id,
        winner,
        loser,
        loser_track,
        overlap_start,
        overlap_end,
    ):
        """
        Move the loser's non-overlapping frames to the winner.
        """

        rows_updated = 0

        # Before overlap
        if loser_track.start_frame < overlap_start:

            rows_updated += FrameObject.objects.filter(
                frame__project_id_id=project_id,
                object_id=loser,
                frame__frame_no__gte=loser_track.start_frame,
                frame__frame_no__lt=overlap_start,
                is_active=True,
            ).update(
                object_id=winner,
            )

        # After overlap
        if loser_track.end_frame > overlap_end:

            rows_updated += FrameObject.objects.filter(
                frame__project_id_id=project_id,
                object_id=loser,
                frame__frame_no__gt=overlap_end,
                frame__frame_no__lte=loser_track.end_frame,
                is_active=True,
            ).update(
                object_id=winner,
            )

        return rows_updated
    
    @classmethod
    def _update_overlap_tracks(
        cls,
        *,
        winner_track,
        loser_track,
        overlap_start,
        overlap_end,
    ):

        winner_track.start_frame = min(
            winner_track.start_frame,
            loser_track.start_frame,
        )

        winner_track.end_frame = max(
            winner_track.end_frame,
            loser_track.end_frame,
        )

        winner_track.operation_note = "overlap_target"

        winner_track.save(
            update_fields=[
                "start_frame",
                "end_frame",
                "operation_note",
            ]
        )

        if loser_track.start_frame < overlap_start:
            loser_track.start_frame = overlap_start

        if loser_track.end_frame > overlap_end:
            loser_track.end_frame = overlap_end

        loser_track.operation_note = "overlap_remaining"

        loser_track.save(
            update_fields=[
                "start_frame",
                "end_frame",
                "operation_note",
            ]
        )


    @staticmethod
    def _validate_overlap_case(
        winner_track,
        loser_track,
    ):
        """
        Allow only partial overlap.

        Reject complete containment.
        """

        winner_inside_loser = (
            loser_track.start_frame < winner_track.start_frame
            and loser_track.end_frame > winner_track.end_frame
        )

        loser_inside_winner = (
            winner_track.start_frame < loser_track.start_frame
            and winner_track.end_frame > loser_track.end_frame
        )

        if winner_inside_loser or loser_inside_winner:
            raise serializers.ValidationError(
                "Complete containment overlap is not supported."
            )