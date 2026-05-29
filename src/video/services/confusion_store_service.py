# src/video/services/confusion_store_service.py

import heapq
import logging
import numpy as np
from django.conf import settings

from ..models import (FrameObject,FrameConfusion,)

logger = logging.getLogger(__name__)


class ConfusionStoreService:

    # =====================================
    # CONFIG
    # =====================================

    TOP_K = getattr(
        settings,
        "CONFUSION_TOP_K",
        1000,
    )

    MIN_UNCERTAINTY = getattr(
        settings,
        "CONFUSION_MIN_UNCERTAINTY",
        0.80,
    )

    SAMPLE_FRAMES = getattr(
        settings,
        "CONFUSION_SAMPLE_FRAMES",
        300,
    )

    # =====================================
    # FETCH FRAME OBJECTS
    # =====================================

    @staticmethod
    def _fetch_frame_objects(
        *,
        project_id,
        frame_no,
    ):

        rows = (
            FrameObject.objects.filter(
                frame__project_id_id=project_id,
                frame__frame_no=frame_no,
                is_active=True,
            )
            .only(
                "object_id",
                "coordinates",
            )
        )

        objects = []

        for row in rows:

            coords = row.coordinates

            if not coords:
                continue

            try:

                # DICT FORMAT
                if isinstance(coords, dict):

                    x = coords.get("x")
                    y = coords.get("y")

                    if x is None or y is None:
                        continue

                    points = np.array([[x, y]])

                # ---------------------------------
                # LIST FORMAT
                # ---------------------------------

                elif isinstance(coords, list):

                    # MULTI KEYPOINTS

                    if (
                        len(coords) > 0
                        and isinstance(coords[0], list)
                    ):

                        points = np.array(coords)

                    # SINGLE POINT

                    elif len(coords) >= 2:

                        points = np.array([
                            [coords[0], coords[1]]
                        ])

                    else:
                        continue

                else:
                    continue

                objects.append({

                    "object_id":
                        row.object_id,

                    "points":
                        points,
                })

            except Exception as e:

                logger.error(
                    f"Coordinate parse error: {e}"
                )

        return objects

    # =====================================
    # BUILD MOTION STATISTICS
    # =====================================

    @classmethod
    def _build_motion_statistics(
        cls,
        *,
        project_id,
    ):

        print(
            f"BUILDING MOTION STATS | "
            f"project_id={project_id}",
            flush=True,
        )

        frame_numbers = list(

            FrameObject.objects.filter(
                frame__project_id_id=project_id,
                is_active=True,
            )
            .values_list(
                "frame__frame_no",
                flat=True,
            )
            .distinct()
            .order_by("frame__frame_no")[
                :cls.SAMPLE_FRAMES
            ]
        )

        all_distances = []

        for frame_no in frame_numbers:

            next_frame_no = frame_no + 1

            current_objects = (
                cls._fetch_frame_objects(
                    project_id=project_id,
                    frame_no=frame_no,
                )
            )

            next_objects = (
                cls._fetch_frame_objects(
                    project_id=project_id,
                    frame_no=next_frame_no,
                )
            )

            if (
                not current_objects
                or not next_objects
            ):
                continue

            for curr_obj in current_objects:

                curr_pts = curr_obj["points"]

                for next_obj in next_objects:

                    next_pts = next_obj["points"]

                    try:

                        min_pts = min(
                            len(curr_pts),
                            len(next_pts),
                        )

                        curr_use = curr_pts[:min_pts]

                        next_use = next_pts[:min_pts]

                        # ORIGINAL FORMULA
                        distance = (
                            np.nanmean(
                                np.abs(
                                    curr_use - next_use
                                )
                            ) * 2
                        )

                        if np.isnan(distance):
                            continue

                        all_distances.append(
                            float(distance)
                        )

                    except Exception:
                        continue

        # =====================================
        # FALLBACK
        # =====================================

        if not all_distances:

            print(
                "NO MOTION STATS FOUND",
                flush=True,
            )

            return {

                "p50": 25,
                "p75": 40,
                "p90": 80,
                "p95": 120,
                "std": 10,
            }

        stats = {

            "p50": float(
                np.percentile(
                    all_distances,
                    50,
                )
            ),

            "p75": float(
                np.percentile(
                    all_distances,
                    75,
                )
            ),

            "p90": float(
                np.percentile(
                    all_distances,
                    90,
                )
            ),

            "p95": float(
                np.percentile(
                    all_distances,
                    95,
                )
            ),

            "std": float(
                np.std(all_distances)
            ),
        }

        print(
            f"MOTION STATS={stats}",
            flush=True,
        )

        return stats

    # =====================================
    # MAIN GENERATOR
    # =====================================

    @classmethod
    def generate(
        cls,
        *,
        project_id,
    ):

        print(
            f"CONFUSION STARTED | "
            f"project_id={project_id}",
            flush=True,
        )

        logger.info(
            f"Starting confusion generation | "
            f"project_id={project_id}"
        )

        stats = (
            cls._build_motion_statistics(
                project_id=project_id
            )
        )

        # =====================================
        # DYNAMIC THRESHOLDS
        # =====================================

        max_good_match_cost = (
            stats["p75"]
        )

        distance_threshold = (
            stats["p95"]
        )

        max_cost_gap = max(
            stats["std"],
            3,
        )

        print(
            f"THRESHOLDS | "
            f"match={max_good_match_cost} | "
            f"distance={distance_threshold} | "
            f"gap={max_cost_gap}",
            flush=True,
        )

        # =====================================
        # DELETE OLD DATA
        # =====================================

        FrameConfusion.objects.filter(
            project_id=project_id
        ).delete()

        # =====================================
        # GENERATE CONFUSIONS
        # =====================================

        frame_numbers = list(

            FrameObject.objects.filter(
                frame__project_id_id=project_id
            )
            .values_list(
                "frame__frame_no",
                flat=True,
            )
            .distinct()
            .order_by("frame__frame_no")
        )

        print(
            f"TOTAL FRAMES={len(frame_numbers)}",
            flush=True,
        )

        top_events = []

        # =====================================
        # PROCESS FRAMES
        # =====================================

        for frame_no in frame_numbers:

            if frame_no % 1000 == 0:

                print(
                    f"PROCESSING FRAME={frame_no}",
                    flush=True,
                )

            next_frame_no = frame_no + 1

            current_objects = (
                cls._fetch_frame_objects(
                    project_id=project_id,
                    frame_no=frame_no,
                )
            )

            next_objects = (
                cls._fetch_frame_objects(
                    project_id=project_id,
                    frame_no=next_frame_no,
                )
            )

            if (
                not current_objects
                or not next_objects
            ):
                continue

            for curr_obj in current_objects:

                curr_pts = curr_obj["points"]

                candidates = []

                for next_obj in next_objects:

                    next_pts = next_obj["points"]

                    try:

                        min_pts = min(
                            len(curr_pts),
                            len(next_pts),
                        )

                        curr_use = curr_pts[:min_pts]
                        next_use = next_pts[:min_pts]

                        # ORIGINAL FORMULA
                        distance = (
                            np.nanmean(
                                np.abs(
                                    curr_use - next_use
                                )
                            ) * 2
                        )

                        # DISTANCE FILTER
                        if (
                            distance
                            > distance_threshold
                        ):
                            continue

                        candidates.append({

                            "object_id":
                                next_obj["object_id"],

                            "distance":
                                float(distance),
                        })

                    except Exception:
                        continue

                if len(candidates) < 2:
                    continue

                candidates.sort(
                    key=lambda x: x["distance"]
                )

                best = candidates[0]

                second = candidates[1]

                best_cost = best["distance"]

                second_cost = second["distance"]

                if second_cost <= 0:
                    continue

                # UNCERTAINTY
                uncertainty = (
                    best_cost / second_cost
                )

                # QUALITY FILTER
                if (
                    best_cost
                    > max_good_match_cost
                ):
                    continue

                # GAP FILTER
                cost_gap = (
                    second_cost - best_cost
                )

                if (
                    cost_gap
                    > max_cost_gap
                ):
                    continue

                # UNCERTAINTY FILTER
                if (
                    uncertainty
                    < cls.MIN_UNCERTAINTY
                ):
                    continue

                nearby_count = len(candidates)

                crowd_bonus = min(
                    nearby_count / 10,
                    1.0,
                )

                # FINAL SCORE
                severity_score = (

                    uncertainty * 0.6

                    + (
                        1
                        - min(
                            cost_gap / (
                                max_cost_gap * 2
                            ),
                            1.0,
                        )
                    ) * 0.3

                    + crowd_bonus * 0.1
                )

                # EVENT TYPE

                if (
                    uncertainty >= 0.90
                    and cost_gap <= (
                        max_cost_gap * 0.5
                    )
                ):

                    event_type = (
                        "ID_SWITCH_RISK"
                    )

                elif nearby_count >= 5:

                    event_type = (
                        "CROWD_OVERLAP"
                    )

                else:

                    event_type = (
                        "HIGH_UNCERTAINTY"
                    )

                event = (

                    severity_score,

                    FrameConfusion(

                        project_id=project_id,

                        frame_no=frame_no,

                        next_frame_no=next_frame_no,

                        current_object_id=(
                            curr_obj["object_id"]
                        ),

                        best_match_object_id=(
                            best["object_id"]
                        ),

                        second_match_object_id=(
                            second["object_id"]
                        ),

                        best_match_cost=float(
                            best_cost
                        ),

                        second_match_cost=float(
                            second_cost
                        ),

                        uncertainty=float(
                            uncertainty
                        ),

                        nearby_object_count=(
                            nearby_count
                        ),

                        confusion_score=float(
                            severity_score
                        ),

                        is_crowded=(
                            nearby_count >= 5
                        ),

                        event_type=event_type,

                        is_forward=True,
                    )
                )

                # TOP-K

                if (
                    len(top_events)
                    < cls.TOP_K
                ):

                    heapq.heappush(
                        top_events,
                        event,
                    )

                else:

                    heapq.heappushpop(
                        top_events,
                        event,
                    )

        print(
            f"FINAL EVENTS={len(top_events)}",
            flush=True,
        )

        final_batch = [

            event[1]

            for event in sorted(
                top_events,
                key=lambda x: x[0],
                reverse=True,
            )
        ]

        FrameConfusion.objects.bulk_create(
            final_batch,
            batch_size=1000,
        )

        print(
            f"INSERTED ROWS={len(final_batch)}",
            flush=True,
        )

        logger.info(
            f"Inserted rows="
            f"{len(final_batch)}"
        )

        print(
            f"CONFUSION COMPLETED | "
            f"project_id={project_id}",
            flush=True,
        )