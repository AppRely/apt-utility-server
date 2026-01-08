# src/video/services/object_track_rebuild_service.py

from django.db import transaction
from django.db.models import Min, Max
from ..models import FrameObject, ObjectTrack


class ObjectTrackRebuildService:

    @staticmethod
    @transaction.atomic
    def rebuild(*, project_id: int):
        """
        Rebuild ObjectTrack from FrameObject (authoritative).
        """

        # 1️⃣ Clear old derived data
        ObjectTrack.objects.filter(project_id_id=project_id).delete()

        # 2️⃣ Aggregate ranges from FrameObject
        qs = (
            FrameObject.objects
            .filter(frame__project_id=project_id, is_active=True)
            .values("object_id")
            .annotate(
                start_frame=Min("frame__frame_no"),
                end_frame=Max("frame__frame_no"),
            )
        )

        bulk = [
            ObjectTrack(
                project_id_id=project_id,
                object_id=row["object_id"],
                start_frame=row["start_frame"],
                end_frame=row["end_frame"],
                object_status=1,
                operation_note=None,
            )
            for row in qs
        ]

        ObjectTrack.objects.bulk_create(bulk, batch_size=1000)
