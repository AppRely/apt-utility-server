from ..models import FrameObject


class TrajectoryGapService:
    """Find non-consecutive frame transitions in a trajectory."""

    DEFAULT_MIN_GAP = 2
    DEFAULT_LIMIT = 20

    @classmethod
    def find(
        cls,
        *,
        project_id,
        object_id,
        min_gap=DEFAULT_MIN_GAP,
        limit=DEFAULT_LIMIT,
    ):
        frame_numbers = list(
            FrameObject.objects.filter(
                frame__project_id_id=project_id,
                object_id=object_id,
                is_active=True,
            )
            .order_by("frame__frame_no")
            .values_list("frame__frame_no", flat=True)
            .distinct()
        )
        gaps = [
            {
                "start_frame": start_frame,
                "end_frame": end_frame,
                "gap": end_frame - start_frame,
            }
            for start_frame, end_frame in zip(frame_numbers, frame_numbers[1:])
            if end_frame - start_frame >= min_gap
        ]
        gaps.sort(key=lambda item: (-item["gap"], item["start_frame"]))

        return {
            "project_id": project_id,
            "object_id": object_id,
            "largest_gap": gaps[0] if gaps else None,
            "gaps": gaps[:limit],
        }
