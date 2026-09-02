# src/video/services/object_track_rebuild_service.py

from django.db import transaction
from django.db.models import Max, Min

from ..models import FrameObject, ObjectTrack, Project


class ObjectTrackRebuildService:
    @staticmethod
    @transaction.atomic
    def rebuild(*, project_id: int):
        """
        Reconcile ObjectTrack with FrameObject (authoritative).

        Existing rows are updated in place so their primary keys remain stable.
        ObjectLinkingSuggestion has foreign keys to these rows, so deleting and
        recreating every track can otherwise invalidate a concurrent linking
        calculation.
        """

        # Serialize track rebuilds and linking-suggestion generation per project.
        Project.objects.select_for_update().get(project_id=project_id)

        ranges = list(
            FrameObject.objects.filter(frame__project_id=project_id, is_active=True)
            .values("object_id")
            .annotate(
                start_frame=Min("frame__frame_no"),
                end_frame=Max("frame__frame_no"),
            )
        )

        existing = {
            track.object_id: track
            for track in ObjectTrack.objects.select_for_update().filter(
                project_id_id=project_id
            )
        }
        active_object_ids = {row["object_id"] for row in ranges}
        to_update = []
        to_create = []

        for row in ranges:
            track = existing.get(row["object_id"])
            if track is None:
                to_create.append(
                    ObjectTrack(
                        project_id_id=project_id,
                        object_id=row["object_id"],
                        start_frame=row["start_frame"],
                        end_frame=row["end_frame"],
                        object_status=1,
                        operation_note=None,
                    )
                )
                continue

            track.start_frame = row["start_frame"]
            track.end_frame = row["end_frame"]
            track.object_status = 1
            track.operation_note = None
            to_update.append(track)

        if to_update:
            ObjectTrack.objects.bulk_update(
                to_update,
                ["start_frame", "end_frame", "object_status", "operation_note"],
                batch_size=1000,
            )

        if to_create:
            ObjectTrack.objects.bulk_create(to_create, batch_size=1000)

        # Remove only tracks which no longer have any active frame data.
        ObjectTrack.objects.filter(project_id_id=project_id).exclude(
            object_id__in=active_object_ids
        ).delete()

        return len(ranges)
