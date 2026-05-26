from django.db.models import Count
from django.db.models import Max
from django.db.models import Min
from django.db.models import Q

from ..models import FrameObject


class UniqueIdsService:

    @staticmethod
    def fetch(
        *,
        project_id,
        start_frame=None,
        end_frame=None,
    ):

        # =====================================
        # BASE QUERY
        # =====================================

        base_qs = FrameObject.objects.filter(
            frame__project_id_id=project_id,
            is_active=True,
        )

        is_range_filter = (
            start_frame is not None
            and end_frame is not None
        )

        # =====================================
        # RANGE FILTER
        # =====================================

        if is_range_filter:

            base_qs = base_qs.filter(
                frame__frame_no__gte=start_frame,
                frame__frame_no__lte=end_frame,
            )

        # =====================================
        # AGGREGATION
        # =====================================

        aggregated_rows = list(
            base_qs
            .values("object_id")
            .annotate(
                start_frame=Min(
                    "frame__frame_no"
                ),
                end_frame=Max(
                    "frame__frame_no"
                ),
                trk_len=Count("id"),
            )
            .order_by("object_id")
        )

        if not aggregated_rows:

            return {
                "project_id": project_id,
                "objects": [],
            }

        # =====================================
        # OLD BEHAVIOR
        # =====================================

        if not is_range_filter:

            return {
                "project_id": project_id,
                "objects": [
                    {
                        "id": row["object_id"],
                        "start_frame":
                            row["start_frame"],
                        "end_frame":
                            row["end_frame"],
                        "N_frame":
                            (
                                row["end_frame"]
                                - row["start_frame"]
                                + 1
                            ),
                        "trk_len":
                            row["trk_len"],
                    }
                    for row in aggregated_rows
                ],
            }

        # =====================================
        # BUILD EXACT PAIRS
        # =====================================

        coordinate_filters = Q()

        for row in aggregated_rows:

            coordinate_filters |= Q(
                object_id=row["object_id"],
                frame__frame_no=row["start_frame"],
            )

            coordinate_filters |= Q(
                object_id=row["object_id"],
                frame__frame_no=row["end_frame"],
            )

        # =====================================
        # FETCH ONLY REQUIRED ROWS
        # =====================================

        coordinate_rows = (
            FrameObject.objects.filter(
                coordinate_filters,
                frame__project_id_id=project_id,
                is_active=True,
            )
            .values(
                "object_id",
                "frame__frame_no",
                "coordinates",
            )
        )

        coordinate_map = {}

        for row in coordinate_rows:

            coordinates = row.get(
                "coordinates"
            )

            first_coordinate = None

            if (
                isinstance(coordinates, list)
                and len(coordinates) > 0
            ):

                first_coordinate = (
                    coordinates[0]
                )

            coordinate_map[
                (
                    row["object_id"],
                    row["frame__frame_no"],
                )
            ] = first_coordinate

        # =====================================
        # FINAL RESPONSE
        # =====================================

        result = []

        for row in aggregated_rows:

            object_id = row["object_id"]

            start_fr = row["start_frame"]
            end_fr = row["end_frame"]

            result.append(
                {
                    "id": object_id,

                    "start_frame":
                        start_fr,

                    "end_frame":
                        end_fr,

                    "N_frame":
                        (
                            end_fr
                            - start_fr
                            + 1
                        ),

                    "trk_len":
                        row["trk_len"],

                    "start_coordinate":
                        coordinate_map.get(
                            (
                                object_id,
                                start_fr,
                            )
                        ),

                    "end_coordinate":
                        coordinate_map.get(
                            (
                                object_id,
                                end_fr,
                            )
                        ),
                }
            )

        return {
            "project_id": project_id,
            "objects": result,
        }