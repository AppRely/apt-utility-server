from django.db.models import Count
from django.db.models import Max
from django.db.models import Min
from django.db.models import Q

from ..models import (
    FrameObject,
    ObjectLinkingSuggestion,
    VideoFrame,
)


class UniqueIdsService:

    # Bound SQL expression size independently of the number of project tracks.
    COORDINATE_BATCH_SIZE = 200

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

        # =====================================
        # OPTIONAL RANGE FILTER
        # =====================================

        if (
            start_frame is not None
            and end_frame is not None
        ):

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
                start_frame=Min("frame__frame_no"),
                end_frame=Max("frame__frame_no"),
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
        # LINKING SUGGESTIONS
        # =====================================

        object_ids = [
            row["object_id"]
            for row in aggregated_rows
        ]

        linking_rows = (
            ObjectLinkingSuggestion.objects
            .filter(
                project_id=project_id,
                source_track__object_id__in=object_ids,
            )
            .values(
                "source_track__object_id",
                "target_track__object_id",
                "match_score",
                "rank",
                "uncertainty",
            )
            .order_by(
                "source_track__object_id",
                "rank",
            )
        )

        linking_map = {}

        for suggestion in linking_rows.iterator(chunk_size=2000):

            source_object_id = (
                suggestion["source_track__object_id"]
            )

            linking_map.setdefault(
                source_object_id,
                []
            ).append(
                suggestion
            )

        # Resolve project-scoped frame numbers once. The coordinate predicates
        # then use indexed columns on frame_object only, without an OR across
        # the frame_object/video_frame join for every endpoint.
        endpoint_frames = {
            frame_no
            for row in aggregated_rows
            for frame_no in (row["start_frame"], row["end_frame"])
        }
        frame_ids = dict(VideoFrame.objects.filter(
            project_id_id=project_id, frame_no__in=endpoint_frames,
        ).values_list("frame_no", "id"))
        frame_numbers = {frame_id: frame_no for frame_no, frame_id in frame_ids.items()}

        # Fetch the same endpoint coordinates in bounded batches. A single OR
        # expression for every project track becomes expensive to build/plan
        # and can exceed database expression limits on large projects.
        coordinate_map = {}
        for offset in range(0, len(aggregated_rows), UniqueIdsService.COORDINATE_BATCH_SIZE):
            batch = aggregated_rows[offset:offset + UniqueIdsService.COORDINATE_BATCH_SIZE]
            endpoint_pairs = {
                (row["object_id"], frame_no)
                for row in batch
                for frame_no in (row["start_frame"], row["end_frame"])
            }
            coordinate_filters = Q()
            for object_id, frame_no in sorted(endpoint_pairs):
                frame_id = frame_ids.get(frame_no)
                if frame_id is not None:
                    coordinate_filters |= Q(object_id=object_id, frame_id=frame_id)
            if not coordinate_filters:
                continue

            coordinate_rows = (
                FrameObject.objects.filter(
                    coordinate_filters,
                    is_active=True,
                )
                .values("object_id", "frame_id", "coordinates")
            )
            for row in coordinate_rows.iterator(chunk_size=2000):
                coordinates = row.get("coordinates")
                first_coordinate = (
                    coordinates[0]
                    if isinstance(coordinates, list) and coordinates
                    else None
                )
                coordinate_map[(row["object_id"], frame_numbers[row["frame_id"]])] = first_coordinate
                
        # =====================================
        # FINAL RESPONSE
        # =====================================

        result = []

        for row in aggregated_rows:

            object_id = row["object_id"]

            start_fr = row["start_frame"]
            end_fr = row["end_frame"]

            matches = linking_map.get(
                object_id,
                []
            )

            best_match = (
                matches[0]
                if len(matches) > 0
                else None
            )

            second_match = (
                matches[1]
                if len(matches) > 1
                else None
            )

            other_matches = []

            for match in matches[2:]:

                other_matches.append(
                    {
                        "object_id":
                            match["target_track__object_id"],

                        "match_score":
                            round(
                                match["match_score"],
                                4,
                            ),

                        "rank":
                            match["rank"],
                    }
                )

            result.append(
                {
                    "object_id":
                        object_id,

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

                    "best_match":
                        (
                            best_match["target_track__object_id"]
                            if best_match
                            else None
                        ),

                    "best_match_score":
                        (
                            round(
                                best_match["match_score"],
                                4,
                            )
                            if best_match
                            else None
                        ),

                    "best_match_uncertainty":
                        (
                            round(
                                best_match["uncertainty"],
                                4,
                            )
                            if (
                                best_match
                                and best_match["uncertainty"]
                                is not None
                            )
                            else None
                        ),

                    "second_match":
                        (
                            second_match["target_track__object_id"]
                            if second_match
                            else None
                        ),

                    "second_match_score":
                        (
                            round(
                                second_match["match_score"],
                                4,
                            )
                            if second_match
                            else None
                        ),

                    "other_matches":
                        other_matches,
                }
            )

        return {
            "project_id": project_id,
            "objects": result,
        }