# ==========================================
# CONFUSION TABLE SERVICE
# ==========================================

from ..models import FrameConfusion


class ConfusionTableService:

    ALLOWED_FILTERS = [
        "event_type",
        "is_crowded",
        "is_forward",
        "current_object_id",
    ]

    ALLOWED_ORDERING = [
        "frame_no",
        "-frame_no",
        "uncertainty",
        "-uncertainty",
        "confusion_score",
        "-confusion_score",
        "created_at",
        "-created_at",
    ]

    @staticmethod
    def fetch(
        *,
        project_id,
        start_frame=None,
        end_frame=None,
        query_params=None,
    ):

        qs = (
            FrameConfusion.objects.filter(
                project_id=project_id
            )
            .only(
                "id",
                "frame_no",
                "next_frame_no",
                "current_object_id",
                "best_match_object_id",
                "second_match_object_id",
                "uncertainty",
                "is_forward",
                "best_match_cost",
                "second_match_cost",
                "nearby_object_count",
                "confusion_score",
                "is_crowded",
                "event_type",
                "created_at",
            )
        )

        # =====================================
        # FRAME FILTERS
        # =====================================

        if start_frame is not None:

            qs = qs.filter(
                frame_no__gte=start_frame
            )

        if end_frame is not None:

            qs = qs.filter(
                frame_no__lte=end_frame
            )

        # =====================================
        # DYNAMIC FILTERS
        # =====================================

        filters = {}

        for field in (
            ConfusionTableService.ALLOWED_FILTERS
        ):

            value = query_params.get(field)

            if value is None:

                continue

            if isinstance(value, str):

                if value.lower() == "true":

                    value = True

                elif value.lower() == "false":

                    value = False

            filters[field] = value

        if filters:

            qs = qs.filter(**filters)

        # =====================================
        # MIN SCORE
        # =====================================

        min_score = query_params.get(
            "min_score"
        )

        if min_score is not None:

            qs = qs.filter(
                confusion_score__gte=min_score
            )

        # =====================================
        # ORDERING
        # =====================================

        ordering = query_params.get(
            "ordering",
            "-confusion_score",
        )

        if ordering not in (
            ConfusionTableService.ALLOWED_ORDERING
        ):

            ordering = "-confusion_score"

        qs = qs.order_by(ordering)

        # =====================================
        # LIMIT
        # =====================================

        limit = query_params.get(
            "limit",
            200,
        )

        limit = min(
            max(int(limit), 1),
            1000,
        )

        return qs[:limit]