from django.db.models import Max, Min

from ..models import FrameObject


class TrajectoryLengthService:
    """Calculate, filter, and order active trajectories by their frame span."""

    ORDER_LONGEST_FIRST = "length_desc"
    ORDER_SHORTEST_FIRST = "length_asc"

    @classmethod
    def list(
        cls,
        *,
        project_id,
        ordering=ORDER_LONGEST_FIRST,
        min_length=None,
        max_length=None,
    ):
        boundary_rows = (
            FrameObject.objects.filter(
                frame__project_id_id=project_id,
                is_active=True,
            )
            .values("object_id")
            .annotate(
                first_frame=Min("frame__frame_no"),
                last_frame=Max("frame__frame_no"),
            )
        )

        trajectories = []
        for row in boundary_rows:
            length = row["last_frame"] - row["first_frame"] + 1
            if min_length is not None and length < min_length:
                continue
            if max_length is not None and length > max_length:
                continue
            trajectories.append(
                {
                    "object_id": row["object_id"],
                    "first_frame": row["first_frame"],
                    "last_frame": row["last_frame"],
                    "length": length,
                }
            )

        is_descending = ordering == cls.ORDER_LONGEST_FIRST
        trajectories.sort(
            key=lambda item: (
                item["length"] * (-1 if is_descending else 1),
                item["object_id"],
            )
        )

        return {
            "project_id": project_id,
            "ordering": ordering,
            "trajectories": trajectories,
        }
