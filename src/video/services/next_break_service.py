from django.db.models import Exists, Max, Min, OuterRef, Value

from ..models import FrameObject


class NextBreakService:
    """Find only the next runtime gap in an object's active frame data."""

    @classmethod
    def find(cls, *, project_id, object_id, current_frame):
        active_rows = FrameObject.objects.filter(
            frame__project_id_id=project_id,
            object_id=object_id,
            is_active=True,
        )
        active_range = active_rows.aggregate(
            first_frame=Min("frame__frame_no"),
            last_frame=Max("frame__frame_no"),
        )

        first_frame = active_range["first_frame"]
        last_frame = active_range["last_frame"]
        empty_result = {
            "object_id": object_id,
            "break_start": None,
            "break_end": None,
        }

        if first_frame is None or current_frame >= last_frame:
            return empty_result

        search_from = max(current_frame, first_frame)

        next_frame_exists = FrameObject.objects.filter(
            frame__project_id_id=project_id,
            object_id=object_id,
            is_active=True,
            frame__frame_no=OuterRef("frame__frame_no") + Value(1),
        )

        # Find the first active frame after the navigation point that does not
        # have an active row in the immediately following frame. Starting the
        # search at current_frame keeps the existing "skip current break" rule.
        break_boundary = (
            active_rows.filter(
                frame__frame_no__gte=search_from,
                frame__frame_no__lt=last_frame,
            )
            .annotate(has_next_frame=Exists(next_frame_exists))
            .filter(has_next_frame=False)
            .order_by("frame__frame_no")
            .values_list("frame__frame_no", flat=True)
            .first()
        )

        if break_boundary is None:
            return empty_result

        resume_frame = (
            active_rows.filter(frame__frame_no__gt=break_boundary)
            .order_by("frame__frame_no")
            .values_list("frame__frame_no", flat=True)
            .first()
        )

        if resume_frame is None:
            return empty_result

        return {
            "object_id": object_id,
            "break_start": break_boundary + 1,
            "break_end": resume_frame - 1,
        }
