from django.db import transaction
from django.db.models import Q, Min, Max, Count
from rest_framework import serializers

from ..models import (
    Project,
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


class LinkTrackState:
    @staticmethod
    def refresh(track, note):
        bounds = FrameObject.objects.filter(
            frame__project_id_id=track.project_id_id, object_id=track.object_id,
            is_active=True,
        ).aggregate(start=Min("frame__frame_no"), end=Max("frame__frame_no"))
        if bounds["start"] is None:
            ObjectLifecycleService.deactivate_object(track, note=note)
            return
        track.start_frame = bounds["start"]
        track.end_frame = bounds["end"]
        track.object_status = 1
        track.operation_note = note
        track.save(update_fields=["start_frame", "end_frame", "object_status", "operation_note"])


class LinkSelectionValidator:
    @staticmethod
    def validate_unique_frames(project_id, object_id, start=None, end=None):
        rows = FrameObject.objects.filter(
            frame__project_id_id=project_id, object_id=object_id, is_active=True,
        )
        if start is not None and end is not None:
            rows = rows.filter(frame__frame_no__range=(start, end))
        duplicate = (rows.values("frame__frame_no").annotate(row_count=Count("pk"))
                     .filter(row_count__gt=1).order_by("frame__frame_no").first())
        if duplicate:
            raise serializers.ValidationError({
                "objects": (
                    f"Object {object_id} has duplicate active data at frame "
                    f"{duplicate['frame__frame_no']}. Correct this trajectory before linking."
                )
            })

    @staticmethod
    def collisions(project_id, winner, source, start, end):
        winner_frames = FrameObject.objects.filter(
            frame__project_id_id=project_id, object_id=winner, is_active=True,
        ).values("frame_id")
        return list(FrameObject.objects.filter(
            frame__project_id_id=project_id, object_id=source, is_active=True,
            frame__frame_no__range=(start, end), frame_id__in=winner_frames,
        ).values_list("frame__frame_no", flat=True).distinct().order_by("frame__frame_no"))

    @staticmethod
    def pair(data):
        project_id = data["project_id"]
        for index in (1, 2):
            obj = data[f"object_{index}_id"]
            tracks = list(ObjectTrack.objects.select_for_update().filter(
                project_id_id=project_id, object_id=obj, object_status=1,
            ))
            if len(tracks) != 1:
                raise serializers.ValidationError("Each object must have exactly one active track.")
            track = tracks[0]
            start, end = data[f"object_{index}_start"], data[f"object_{index}_end"]
            if not track.start_frame <= start <= end <= track.end_frame:
                raise serializers.ValidationError("Selected range is outside the active lifecycle.")
            if not FrameObject.objects.filter(frame__project_id_id=project_id, object_id=obj,
                    is_active=True, frame__frame_no__range=(start, end)).exists():
                raise serializers.ValidationError("Selected range has no active frames.")
            data[f"object_{index}_track"] = track
        if data["object_1_id"] == data["object_2_id"]:
            raise serializers.ValidationError("Object IDs must be different.")
        winner = (data.get("preferred_object") if data.get("operation") == "overlap"
                  else data["object_1_id"])
        for index in (1, 2):
            obj = data[f"object_{index}_id"]
            LinkSelectionValidator.validate_unique_frames(
                project_id, obj,
                None if obj == winner else data[f"object_{index}_start"],
                None if obj == winner else data[f"object_{index}_end"],
            )
        start = max(data["object_1_start"], data["object_2_start"])
        end = min(data["object_1_end"], data["object_2_end"])
        if data.get("operation", "link") == "link":
            if start <= end or LinkSelectionValidator.collisions(project_id,
                    data["object_1_id"], data["object_2_id"], data["object_2_start"], data["object_2_end"]):
                raise serializers.ValidationError("Selected trajectories overlap. Use the overlap operation and choose a preferred object.")
        else:
            if data.get("preferred_object") not in (data["object_1_id"], data["object_2_id"]):
                raise serializers.ValidationError("Choose one selected object as preferred_object.")
            if start > end:
                raise serializers.ValidationError("Selected ranges do not overlap.")
            winner_index = 1 if data["preferred_object"] == data["object_1_id"] else 2
            loser_index = 3 - winner_index
            collisions = LinkSelectionValidator.collisions(project_id, data["preferred_object"],
                data[f"object_{loser_index}_id"], data[f"object_{loser_index}_start"], data[f"object_{loser_index}_end"])
            if any(frame < start or frame > end for frame in collisions):
                raise serializers.ValidationError("Source frames overlap destination data outside the selected overlap. Adjust the selected ranges.")
            data["overlap_info"] = {"has_overlap": True, "start": start, "end": end}


class NormalLinkMutator:
    """
    Executes database mutations for the normal link operation.
    """
    @staticmethod
    def execute_mutations(project_id, obj1, obj2, frame_ids, obj1_row, obj2_row):
        rows_updated = FrameObject.objects.filter(
            frame_id__in=frame_ids,
            object_id=obj2,
            is_active=True,
        ).update(
            object_id=obj1,
        )

        if rows_updated == 0:
            raise serializers.ValidationError(
                "No frames found for object_2 in given range."
            )

        LinkTrackState.refresh(obj1_row, "link_target")
        LinkTrackState.refresh(obj2_row, f"linked_into_object_{obj1}")

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
        LinkTrackState.refresh(winner_track, "overlap_target")
        LinkTrackState.refresh(loser_track, "overlap_completed")


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
                        is_active=True,
                    ), ("id", "object_id")),
                    "ObjectTrack": (ObjectTrack.objects.filter(
                        track_id__in=[
                            obj1_row.track_id,
                            obj2_row.track_id,
                        ]
                    ), ("track_id", "project_id_id", "object_id", "start_frame", "end_frame", "object_status", "operation_note")),
                },
                after_qs_map={},
            )

            captured_ids = [row["id"] for row in before_state["FrameObject"]["deleted"]]

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
                        id__in=captured_ids,
                    ), ("id", "object_id")),
                    "ObjectTrack": (ObjectTrack.objects.filter(
                        track_id__in=[
                            obj1_row.track_id,
                            obj2_row.track_id,
                        ]
                    ), ("track_id", "project_id_id", "object_id", "start_frame", "end_frame", "object_status", "operation_note")),
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

        # Fetch only the submitted source range; unselected frames remain untouched.
        # This allows us to perform all calculations and operations in memory or using direct frame_ids,
        # completely avoiding slow subquery JOINs on VideoFrame.
        loser_index = 1 if loser == data["object_1_id"] else 2
        frames = VideoFrame.objects.filter(
            project_id_id=project_id,
            frame_no__gte=data[f"object_{loser_index}_start"],
            frame_no__lte=data[f"object_{loser_index}_end"]
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
                    ), ("track_id", "project_id_id", "object_id", "start_frame", "end_frame", "object_status", "operation_note")),
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

            overlap_counts = OverlapLinkMutator.merge_overlap_frames(
                project_id=project_id,
                winner=winner,
                loser=loser,
                overlap_frame_ids=overlap_frame_ids,
            )

            rows_updated += overlap_counts["updated"]

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
                    ), ("track_id", "project_id_id", "object_id", "start_frame", "end_frame", "object_status", "operation_note")),
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
            "rows_deleted_main_table": overlap_counts["deleted"],
            "winner_object": {
                "object_id": winner,
                "start_frame": winner_track.start_frame,
                "end_frame": winner_track.end_frame,
                "operation_note": winner_track.operation_note,
                "object_status": winner_track.object_status,
            },
            "loser_object": {
                "object_id": loser,
                "start_frame": loser_track.start_frame,
                "end_frame": loser_track.end_frame,
                "operation_note": loser_track.operation_note,
                "object_status": loser_track.object_status,
            },
        }


class BulkLinkOverlapError(serializers.ValidationError):
    """Keep the structured overlap response's numeric and boolean types."""
    def __init__(self, overlaps):
        self.payload = {
            "success": False,
            "code": "TRAJECTORY_OVERLAP",
            "message": "Bulk linking cannot be performed because some selected trajectories have overlapping frames.",
            "overlaps": overlaps,
        }
        super().__init__(self.payload)


class BulkLinkOverlapValidator:
    @staticmethod
    def validate(objects):
        overlaps = []
        for index, first in enumerate(objects):
            for second in objects[index + 1:]:
                start = max(first["start_frame"], second["start_frame"])
                end = min(first["end_frame"], second["end_frame"])
                if start <= end:
                    overlaps.append({
                        "object_1_id": first["object_id"],
                        "object_2_id": second["object_id"],
                        "overlap_start": start,
                        "overlap_end": end,
                    })
        if overlaps:
            raise BulkLinkOverlapError(overlaps)


class BulkLinkStrategy(LinkStrategy):
    """Validate the complete selection, then reuse normal-link mutations once per source."""
    @classmethod
    @transaction.atomic
    def execute(cls, **data):
        project_id = data["project_id"]
        objects = sorted(data["objects"], key=lambda item: (item["start_frame"], item["object_id"]))
        ids = [item["object_id"] for item in objects]
        if len(ids) < 2 or len(set(ids)) != len(ids):
            raise serializers.ValidationError({"objects": "Provide at least two unique objects."})

        # Match the lock order used by track rebuilding/export.
        if not Project.objects.select_for_update().filter(pk=project_id).exists():
            raise serializers.ValidationError("Invalid project")
        tracks = list(ObjectTrack.objects.select_for_update().filter(
            project_id_id=project_id, object_id__in=ids, object_status=1,
        ).order_by("track_id"))
        by_id = {track.object_id: track for track in tracks}
        if len(tracks) != len(ids) or set(by_id) != set(ids):
            raise serializers.ValidationError({"objects": "Every object must have exactly one active track in this project."})

        frame_ids_by_object = {}
        affected = Q(pk__in=[])
        for item in objects:
            obj = item["object_id"]
            track = by_id[obj]
            if not track.start_frame <= item["start_frame"] <= item["end_frame"] <= track.end_frame:
                raise serializers.ValidationError({"objects": f"Range outside object {obj} lifecycle."})
            frame_ids = list(VideoFrame.objects.filter(
                project_id_id=project_id,
                frame_no__range=(item["start_frame"], item["end_frame"]),
            ).values_list("id", flat=True))
            frame_ids_by_object[obj] = frame_ids
            if not FrameObject.objects.filter(frame_id__in=frame_ids, object_id=obj, is_active=True).exists():
                raise serializers.ValidationError({"objects": f"No active frames found for object {obj} in given range."})
            affected |= Q(frame_id__in=frame_ids, object_id=obj, is_active=True)

        for item in objects:
            LinkSelectionValidator.validate_unique_frames(
                project_id, item["object_id"],
                None if item["object_id"] == ids[0] else item["start_frame"],
                None if item["object_id"] == ids[0] else item["end_frame"],
            )
        BulkLinkOverlapValidator.validate(objects)
        collisions = []
        for item in objects[1:]:
            frames = LinkSelectionValidator.collisions(project_id, ids[0], item["object_id"],
                                                       item["start_frame"], item["end_frame"])
            for frame in frames:
                collisions.append({"object_1_id": ids[0], "object_2_id": item["object_id"],
                                   "overlap_start": frame, "overlap_end": frame})
        if collisions:
            raise BulkLinkOverlapError(collisions)
        # Freeze the affected row identities before changing ownership.
        frame_pks = list(FrameObject.objects.select_for_update().filter(affected).values_list("pk", flat=True))
        snapshot_map = {
            "FrameObject": (FrameObject.objects.filter(pk__in=frame_pks), ("id", "object_id")),
            # Full track fields permit restoration if export's rebuild removes an inactive track.
            "ObjectTrack": (ObjectTrack.objects.filter(pk__in=[track.pk for track in tracks]),
                            ("track_id", "project_id_id", "object_id", "start_frame", "end_frame", "object_status", "operation_note")),
        }
        before = SnapshotBuilder.build(before_qs_map=snapshot_map, after_qs_map={})
        master = by_id[ids[0]]
        rows_updated = 0
        for obj in ids[1:]:
            rows_updated += NormalLinkMutator.execute_mutations(
                project_id, master.object_id, obj, frame_ids_by_object[obj], master, by_id[obj],
            )
        after = SnapshotBuilder.build(before_qs_map={}, after_qs_map=snapshot_map)
        SnapshotLogger.log(
            project_id=project_id, operation="bulk_link", before_state=before, after_state=after,
            objects_data={"master_object_id": master.object_id, "objects": objects},
        )
        return {
            "status": "success", "message": "Objects bulk linked successfully",
            "video_id": project_id, "master_object_id": master.object_id,
            "merged_object_ids": ids[1:], "rows_updated_main_table": rows_updated,
            "deactivated_object_ids": [obj for obj in ids[1:] if by_id[obj].object_status == 0],
            "start_frame": master.start_frame, "end_frame": master.end_frame,
        }


class LinkObjectService:
    """
    Handles all link related operations by routing to strategies that implement SOLID principles.
    """
    @classmethod
    @transaction.atomic
    def execute(cls, **data):
        operation = data.get("operation", "link")
        if not Project.objects.select_for_update().filter(pk=data["project_id"]).exists():
            raise serializers.ValidationError("Invalid project")
        if operation == "bulk_link":
            return BulkLinkStrategy.execute(**data)
        if operation not in ("link", "overlap"):
            raise serializers.ValidationError("Invalid operation.")
        LinkSelectionValidator.pair(data)
        if operation == "link":
            return NormalLinkStrategy.execute(**data)
        return OverlapLinkStrategy.execute(**data)
