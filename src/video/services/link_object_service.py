from django.db import transaction
from django.db.models import Q, Min, Max
from rest_framework import serializers

from ..models import (
    FrameObject,
    ObjectTrack,
    VideoFrame,
)

from .object_lifecycle_service import ObjectLifecycleService
from .snapshot_builder import SnapshotBuilder
from .snapshot_logger import SnapshotLogger
from abc import ABC, abstractmethod


class TrackOverlapDetector:
    """
    Responsible for detecting overlap between two object tracks.
    """
    @staticmethod
    def detect(track_1, track_2):
        overlap_start = max(track_1.start_frame, track_2.start_frame)
        overlap_end = min(track_1.end_frame, track_2.end_frame)

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


class WinnerLoserSelector:
    """
    Responsible for determining the winner and loser track based on the preferred object.
    """
    @staticmethod
    def select(data):
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


class NormalLinkMutator:
    """
    Executes database mutations for the normal link operation.
    """
    @staticmethod
    def execute_mutations(project_id, obj1, obj2, frame_ids, obj1_row, obj2_row):
        rows_updated = FrameObject.objects.filter(
            frame_id__in=frame_ids,
            object_id=obj2,
        ).update(
            object_id=obj1,
        )

        if rows_updated == 0:
            raise serializers.ValidationError(
                "No frames found for object_2 in given range."
            )

        obj1_row.start_frame = min(obj1_row.start_frame, obj2_row.start_frame)
        obj1_row.end_frame = max(obj1_row.end_frame, obj2_row.end_frame)
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

        ObjectLifecycleService.deactivate_object(
            obj2_row,
            note=f"linked_into_object_{obj1}",
        )

        return rows_updated


class OverlapLinkMutator:
    """
    Executes database mutations for the overlap link operation.
    """
    @staticmethod
    def move_non_overlap_frames(winner, loser, before_overlap_frame_ids, after_overlap_frame_ids):
        rows_updated = 0

        # Before overlap
        if before_overlap_frame_ids:
            rows_updated += FrameObject.objects.filter(
                frame_id__in=before_overlap_frame_ids,
                object_id=loser,
                is_active=True,
            ).update(
                object_id=winner,
            )

        # After overlap
        if after_overlap_frame_ids:
            rows_updated += FrameObject.objects.filter(
                frame_id__in=after_overlap_frame_ids,
                object_id=loser,
                is_active=True,
            ).update(
                object_id=winner,
            )

        return rows_updated

    @staticmethod
    def merge_overlap_frames(project_id, winner, loser, overlap_frame_ids):
        if not overlap_frame_ids:
            return {"updated": 0, "deleted": 0}

        # Database optimization: fetch only frame_id values into a set
        winner_frame_ids = set(
            FrameObject.objects.filter(
                frame_id__in=overlap_frame_ids,
                object_id=winner,
                is_active=True,
            ).values_list("frame_id", flat=True)
        )

        # Bulk delete loser frame objects that overlap with existing winner frame objects
        deleted_count, _ = FrameObject.objects.filter(
            frame_id__in=winner_frame_ids,
            object_id=loser,
            is_active=True,
        ).delete()

        # Bulk update loser frame objects that do not overlap with winner frame objects
        updated_count = FrameObject.objects.filter(
            frame_id__in=overlap_frame_ids,
            object_id=loser,
            is_active=True,
        ).exclude(
            frame_id__in=winner_frame_ids
        ).update(
            object_id=winner
        )

        return {
            "updated": updated_count,
            "deleted": deleted_count,
        }

    @staticmethod
    def update_overlap_tracks(winner_track, loser_track, loser_frame_ids, frame_id_to_no):
        winner_track.start_frame = min(winner_track.start_frame, loser_track.start_frame)
        winner_track.end_frame = max(winner_track.end_frame, loser_track.end_frame)
        winner_track.operation_note = "overlap_target"
        winner_track.object_status = 1
        winner_track.save(
            update_fields=[
                "start_frame",
                "end_frame",
                "object_status",
                "operation_note",
            ]
        )

        # In-memory optimization: map remaining frames of loser back to their frame numbers using the dictionary
        remaining_frame_ids = list(
            FrameObject.objects.filter(
                frame_id__in=loser_frame_ids,
                object_id=loser_track.object_id,
                is_active=True,
            ).values_list("frame_id", flat=True)
        )

        remaining_frame_nos = [
            frame_id_to_no[fid] for fid in remaining_frame_ids if fid in frame_id_to_no
        ]

        if not remaining_frame_nos:
            ObjectLifecycleService.deactivate_object(
                loser_track,
                note="overlap_completed",
            )
            return

        loser_track.start_frame = min(remaining_frame_nos)
        loser_track.end_frame = max(remaining_frame_nos)
        loser_track.operation_note = "overlap_remaining"

        loser_track.save(
            update_fields=[
                "start_frame",
                "end_frame",
                "operation_note",
            ]
        )


class LinkStrategy(ABC):
    """
    Abstract Base Class representing a linking strategy.
    """
    @classmethod
    @abstractmethod
    def execute(cls, **data):
        pass


class NormalLinkStrategy(LinkStrategy):
    """
    Implementation of the normal linking strategy.
    """
    @classmethod
    def execute(cls, **data):
        project_id = data["project_id"]

        obj1 = data["object_1_id"]
        obj2 = data["object_2_id"]

        start2 = data["object_2_start"]
        end2 = data["object_2_end"]

        obj1_row = data["object_1_track"]
        obj2_row = data["object_2_track"]

        # Pre-fetch frame_ids for the affected range to avoid slow JOIN queries
        frame_ids = list(
            VideoFrame.objects.filter(
                project_id_id=project_id,
                frame_no__gte=start2,
                frame_no__lte=end2
            ).values_list("id", flat=True)
        )

        with transaction.atomic():

            # ============================================
            # BEFORE SNAPSHOT
            # ============================================

            before_state = SnapshotBuilder.build(
                before_qs_map={
                    "FrameObject": (FrameObject.objects.filter(
                        frame_id__in=frame_ids,
                        object_id=obj2,
                    ), ("id", "object_id")),
                    "ObjectTrack": (ObjectTrack.objects.filter(
                        track_id__in=[
                            obj1_row.track_id,
                            obj2_row.track_id,
                        ]
                    ), ("track_id", "start_frame", "end_frame", "object_status", "operation_note")),
                },
                after_qs_map={},
            )

            rows_updated = NormalLinkMutator.execute_mutations(
                project_id=project_id,
                obj1=obj1,
                obj2=obj2,
                frame_ids=frame_ids,
                obj1_row=obj1_row,
                obj2_row=obj2_row,
            )

            after_state = SnapshotBuilder.build(
                before_qs_map={},
                after_qs_map={
                    "FrameObject": (FrameObject.objects.filter(
                        frame_id__in=frame_ids,
                        object_id=obj1,
                    ), ("id", "object_id")),
                    "ObjectTrack": (ObjectTrack.objects.filter(
                        track_id__in=[
                            obj1_row.track_id,
                            obj2_row.track_id,
                        ]
                    ), ("track_id", "start_frame", "end_frame", "object_status", "operation_note")),
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

class OverlapLinkStrategy(LinkStrategy):
    """
    Implementation of the overlap linking strategy.
    """
    @classmethod
    def execute(cls, **data):
        project_id = data["project_id"]
        overlap = data["overlap_info"]
        overlap_start = overlap["start"]
        overlap_end = overlap["end"]

        link_info = WinnerLoserSelector.select(data)
        winner = link_info["winner"]
        loser = link_info["loser"]

        winner_track = link_info["winner_track"]
        loser_track = link_info["loser_track"]

        # Fetch mapping of frame_id to frame_no for the loser track's entire range.
        # This allows us to perform all calculations and operations in memory or using direct frame_ids,
        # completely avoiding slow subquery JOINs on VideoFrame.
        frames = VideoFrame.objects.filter(
            project_id_id=project_id,
            frame_no__gte=loser_track.start_frame,
            frame_no__lte=loser_track.end_frame
        ).values("id", "frame_no")

        frame_id_to_no = {f["id"]: f["frame_no"] for f in frames}
        loser_frame_ids = list(frame_id_to_no.keys())

        # Sub-divide the frame_ids into before, overlap, and after regions
        before_overlap_frame_ids = [
            fid for fid, fno in frame_id_to_no.items() if fno < overlap_start
        ]
        overlap_frame_ids = [
            fid for fid, fno in frame_id_to_no.items() if overlap_start <= fno <= overlap_end
        ]
        after_overlap_frame_ids = [
            fid for fid, fno in frame_id_to_no.items() if fno > overlap_end
        ]

        # Query only the loser's active FrameObjects in the loser_frame_ids range.
        # We completely avoid snapshotting the winner's unchanged FrameObjects.
        loser_frame_qs = FrameObject.objects.filter(
            frame_id__in=loser_frame_ids,
            object_id=loser,
            is_active=True,
        )

        with transaction.atomic():
            before_state = SnapshotBuilder.build(
                before_qs_map={
                    "FrameObject": (loser_frame_qs, ("id", "frame_id", "object_id", "coordinates", "confidence", "tag", "timestamp", "is_active", "is_interpolated")),
                    "ObjectTrack": (ObjectTrack.objects.filter(
                        track_id__in=[
                            winner_track.track_id,
                            loser_track.track_id,
                        ]
                    ), ("track_id", "start_frame", "end_frame", "object_status", "operation_note")),
                },
                after_qs_map={},
            )

            # Keep track of which loser FrameObject IDs were captured so we only query those in the after snapshot
            captured_loser_fo_ids = [row["id"] for row in before_state["FrameObject"]["deleted"]]

            rows_updated = OverlapLinkMutator.move_non_overlap_frames(
                winner=winner,
                loser=loser,
                before_overlap_frame_ids=before_overlap_frame_ids,
                after_overlap_frame_ids=after_overlap_frame_ids,
            )

            OverlapLinkMutator.merge_overlap_frames(
                project_id=project_id,
                winner=winner,
                loser=loser,
                overlap_frame_ids=overlap_frame_ids,
            )

            OverlapLinkMutator.update_overlap_tracks(
                winner_track=winner_track,
                loser_track=loser_track,
                loser_frame_ids=loser_frame_ids,
                frame_id_to_no=frame_id_to_no,
            )

            after_state = SnapshotBuilder.build(
                before_qs_map={},
                after_qs_map={
                    # Simple PK lookup for the affected rows
                    "FrameObject": (FrameObject.objects.filter(
                        id__in=captured_loser_fo_ids
                    ), ("id", "frame_id", "object_id", "coordinates", "confidence", "tag", "timestamp", "is_active", "is_interpolated")),
                    "ObjectTrack": (ObjectTrack.objects.filter(
                        track_id__in=[
                            winner_track.track_id,
                            loser_track.track_id,
                        ]
                    ), ("track_id", "start_frame", "end_frame", "object_status", "operation_note")),
                },
            )

            SnapshotLogger.log(
                project_id=project_id,
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
            "video_id": project_id,
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


class LinkObjectService:
    """
    Handles all link related operations by routing to strategies that implement SOLID principles.
    """
    @classmethod
    def execute(cls, **data):
        operation = data.get("operation", "link")

        if operation == "link":
            return NormalLinkStrategy.execute(**data)

        if operation == "overlap":
            overlap_info = TrackOverlapDetector.detect(
                data["object_1_track"],
                data["object_2_track"],
            )
            data["overlap_info"] = overlap_info

            if not overlap_info["has_overlap"]:
                raise serializers.ValidationError(
                    "Objects do not overlap."
                )

            return OverlapLinkStrategy.execute(**data)

        raise serializers.ValidationError(
            "Invalid operation."
        )