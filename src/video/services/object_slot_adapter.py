from django.db.models import Case, When, Value, IntegerField
from django.db.models import Q
from ..models import VideoData


class ObjectSlotAdapter:
    """
    Adapter that hides VideoData object slot implementation.
    Serializers must NEVER touch object_1_id, object_2_id, etc directly.
    """

    @staticmethod
    def get_object_id_fields():
        """
        Dynamically discover all object_*_id fields.
        """
        return [
            field.name
            for field in VideoData._meta.fields
            if field.name.startswith("object_") and field.name.endswith("_id")
        ]

    @classmethod
    def build_bulk_nullify_map(cls, object_id: int):
        """
        Build a mapping of fields to Django `Case`/`When` expressions to nullify the given object_id and its coordinates,
        suitable for use with Django's `update()` method for performing bulk updates in a single SQL statement.
        """
        update_map = {}

        for field in cls.get_object_id_fields():
            coord_field = field.replace("_id", "_coordinates")

            update_map[field] = Case(
                When(**{field: object_id}, then=Value(None)),
                default=field,
            )

            update_map[coord_field] = Case(
                When(**{field: object_id}, then=Value(None)),
                default=coord_field,
            )

        return update_map


    @classmethod
    def build_bulk_swap_map(
        cls,
        *,
        obj1: int,
        obj2: int,
        sentinel: int,
        s1: int,
        e1: int,
        s2: int,
        e2: int,
    ):
        """
        Build a CASE/WHEN update map to swap obj1 <-> obj2 safely using a sentinel.
        """
        update_map = {}

        for field in cls.get_object_id_fields():
            update_map[field] = Case(
                # obj1 → SENTINEL (only in obj1 range)
                When(
                    **{
                        field: obj1,
                        "frame_no__gte": s1,
                        "frame_no__lte": e1,
                    },
                    then=Value(sentinel),
                ),
                # obj2 → obj1 (only in obj2 range)
                When(
                    **{
                        field: obj2,
                        "frame_no__gte": s2,
                        "frame_no__lte": e2,
                    },
                    then=Value(obj1),
                ),
                # SENTINEL → obj2
                When(**{field: sentinel}, then=Value(obj2)),
                default=field,
                output_field=IntegerField(),
            )

        return update_map



    @classmethod
    def find_object_slot(cls, project_id: int, object_id: int) -> str | None:
        """
        Find which object_*_id column contains the given object_id.
        Executes exactly ONE DB query.
        """
        object_fields = cls.get_object_id_fields()

        q = Q()
        for field in object_fields:
            q |= Q(**{field: object_id})

        row = (
            VideoData.objects
            .filter(video_id=project_id)
            .filter(q)
            .only(*object_fields)
            .first()
        )

        if not row:
            return None

        for field in object_fields:
            if getattr(row, field) == object_id:
                return field

        return None


    @classmethod
    def build_bulk_replace_map(cls, old_object_id: int, new_object_id: int):
        """
        Replace old_object_id with new_object_id across all object slots
        (used by LINK operation).
        """
        update_map = {}

        for field in cls.get_object_id_fields():
            update_map[field] = Case(
                When(**{field: old_object_id}, then=Value(new_object_id)),
                default=field,
                output_field=IntegerField(),
            )

        return update_map