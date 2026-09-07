from django.db import transaction
from django.db.models import Q

from ..models import FrameObject, ObjectTrack
from .object_lifecycle_service import ObjectLifecycleService
from .snapshot_builder import SnapshotBuilder
from .snapshot_logger import SnapshotLogger


class ObjectDeletionService:
    """Handle transactional single and bulk object soft deletion."""

    SINGLE_OPERATION = "delete"
    BULK_OPERATION = "BULK_DELETE"

    @classmethod
    def delete_single_object(
        cls,
        *,
        project_id: int,
        object_id: int,
        start_frame: int,
        end_frame: int,
        object_track: ObjectTrack,
    ) -> dict:
        deletion_items = [
            {
                "object_id": object_id,
                "start_frame": start_frame,
                "end_frame": end_frame,
                "object_track": object_track,
            }
        ]

        result = cls._delete_objects(
            project_id=project_id,
            deletion_items=deletion_items,
            operation=cls.SINGLE_OPERATION,
            operation_type="single",
            objects_data={
                "object_id": object_id,
                "object_start": start_frame,
                "object_end": end_frame,
            },
        )

        return {
            "object_id": object_id,
            "deleted_range": f"{start_frame}-{end_frame}",
            "frames_affected": result["frames_affected"],
            "object_status": 0,
        }

    @classmethod
    def delete_bulk_objects(
        cls,
        *,
        project_id: int,
        object_ids: list[int],
        object_tracks: list[ObjectTrack],
    ) -> dict:
        tracks_by_object_id = {track.object_id: track for track in object_tracks}
        deletion_items = [
            {
                "object_id": object_id,
                "start_frame": tracks_by_object_id[object_id].start_frame,
                "end_frame": tracks_by_object_id[object_id].end_frame,
                "object_track": tracks_by_object_id[object_id],
            }
            for object_id in object_ids
        ]

        cls._delete_objects(
            project_id=project_id,
            deletion_items=deletion_items,
            operation=cls.BULK_OPERATION,
            operation_type="bulk",
            objects_data={
                "operation_type": "bulk",
                "object_ids": object_ids,
                "deleted_object_count": len(object_ids),
                "objects": [
                    {
                        "object_id": item["object_id"],
                        "start_frame": item["start_frame"],
                        "end_frame": item["end_frame"],
                    }
                    for item in deletion_items
                ],
            },
        )

        return {
            "operation_type": "bulk",
            "deleted_object_ids": object_ids,
            "deleted_count": len(object_ids),
        }

    @classmethod
    def _delete_objects(
        cls,
        *,
        project_id: int,
        deletion_items: list[dict],
        operation: str,
        operation_type: str,
        objects_data: dict,
    ) -> dict:
        with transaction.atomic():
            before_state = SnapshotBuilder.build(
                before_qs_map={
                    "FrameObject": (
                        cls._frame_objects_queryset(
                            project_id=project_id,
                            deletion_items=deletion_items,
                        ),
                        ("id", "is_active"),
                    ),
                    "ObjectTrack": (
                        cls._object_tracks_queryset(
                            project_id=project_id,
                            deletion_items=deletion_items,
                        ),
                        ("track_id", "object_status", "operation_note"),
                    ),
                },
                after_qs_map={},
            )

            total_frames_affected = 0
            for item in deletion_items:
                affected_frames = FrameObject.objects.filter(
                    frame__project_id_id=project_id,
                    object_id=item["object_id"],
                    frame__frame_no__gte=item["start_frame"],
                    frame__frame_no__lte=item["end_frame"],
                ).update(is_active=False)
                total_frames_affected += affected_frames

                note_prefix = "bulk_deleted_frames" if operation_type == "bulk" else "deleted_frames"
                ObjectLifecycleService.deactivate_object(
                    item["object_track"],
                    note=f"{note_prefix}_{item['start_frame']}_to_{item['end_frame']}",
                )

            after_state = SnapshotBuilder.build(
                before_qs_map={},
                after_qs_map={
                    "FrameObject": (
                        cls._frame_objects_queryset(
                            project_id=project_id,
                            deletion_items=deletion_items,
                        ),
                        ("id", "is_active"),
                    ),
                    "ObjectTrack": (
                        cls._object_tracks_queryset(
                            project_id=project_id,
                            deletion_items=deletion_items,
                        ),
                        ("track_id", "object_status", "operation_note"),
                    ),
                },
            )

            SnapshotLogger.log(
                project_id=project_id,
                operation=operation,
                before_state=before_state,
                after_state=after_state,
                objects_data=objects_data,
            )

        return {"frames_affected": total_frames_affected}

    @staticmethod
    def _frame_objects_queryset(*, project_id: int, deletion_items: list[dict]):
        object_range_filter = Q(pk__in=[])
        for item in deletion_items:
            object_range_filter |= Q(
                object_id=item["object_id"],
                frame__frame_no__gte=item["start_frame"],
                frame__frame_no__lte=item["end_frame"],
            )

        return FrameObject.objects.filter(
            object_range_filter,
            frame__project_id_id=project_id,
        )

    @staticmethod
    def _object_tracks_queryset(*, project_id: int, deletion_items: list[dict]):
        track_ids = [item["object_track"].track_id for item in deletion_items]
        return ObjectTrack.objects.filter(
            project_id_id=project_id,
            track_id__in=track_ids,
        )
