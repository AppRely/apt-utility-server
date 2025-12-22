
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

    @staticmethod
    def swap_objects(obj1: ObjectTrack, obj2: ObjectTrack, sentinel: int):
        obj1_id = obj1.object_id
        obj2_id = obj2.object_id

        # Step 1: obj1 → sentinel
        obj1.object_id = sentinel
        obj1.save(update_fields=["object_id"])

        # Step 2: obj2 → obj1
        obj2.object_id = obj1_id
        obj2.operation_note = f"swap_with_object_{obj1_id}"
        obj2.save(update_fields=["object_id", "operation_note"])

        # Step 3: obj1 → obj2
        obj1.object_id = obj2_id
        obj1.operation_note = f"swap_with_object_{obj2_id}"
        obj1.save(update_fields=["object_id", "operation_note"])

    @staticmethod
    def fetch(project_id: int, object_id: int, frame: int):
        try:
            obj = ObjectTrack.objects.get(
                project_id_id=project_id,
                object_id=object_id
            )
        except ObjectTrack.DoesNotExist:
            return {
                "project_id": project_id,
                "object_id": object_id,
                "message": "Object ID not found",
                "is_active": False,
            }

        if obj.object_status == 0:
            return {
                "project_id": project_id,
                "object_id": object_id,
                "message": "Object is inactive",
                "is_active": False,
                "operation_note": obj.operation_note,
            }

        return {
            "project_id": project_id,
            "object_id": object_id,
            "start_frame": obj.start_frame,
            "end_frame": obj.end_frame,
            "is_inside": obj.start_frame <= frame <= obj.end_frame,
            "object_status": obj.object_status,
            "operation_note": obj.operation_note,
        }
