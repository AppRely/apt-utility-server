from ..models import VideoData
from django.core.exceptions import ValidationError
from ..models import ObjectTrack


class ObjectLifecycleService:
    """
    Central place for ObjectTrack lifecycle rules.
    """

    @staticmethod
    def get_active_object(project_id: int, object_id: int) -> ObjectTrack:
        return ObjectTrack.objects.get(
            project_id_id=project_id,
            object_id=object_id,
            object_status=1
        )

    @staticmethod
    def deactivate_object(obj_track: ObjectTrack, note: str):
        obj_track.object_status = 0
        obj_track.operation_note = note
        obj_track.save(update_fields=["object_status", "operation_note"])
