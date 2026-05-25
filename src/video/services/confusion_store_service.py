# src/video/services/confusion_store_service.py

import logging
import numpy as np

from ..models import (
    FrameObject,
    FrameConfusion,
)

logger = logging.getLogger(__name__)


class ConfusionStoreService:

    @staticmethod
    def generate(
        *,
        project_id,
    ):

        logger.info(
            f"Starting confusion generation | "
            f"project_id={project_id}"
        )

        # =====================================
        # FETCH FRAME OBJECTS
        # =====================================

        qs = (
            FrameObject.objects.filter(
                frame__project_id_id=project_id,
                is_active=True,
            )
            .select_related("frame")
            .order_by(
                "frame__frame_no",
                "object_id",
            )
        )

        logger.info(
            f"Fetched frame objects={qs.count()}"
        )

        # =====================================
        # GROUP BY FRAME
        # =====================================

        frame_map = {}

        for row in qs:

            frame_no = row.frame.frame_no

            if frame_no not in frame_map:
                frame_map[frame_no] = []

            coords = row.coordinates

            if not coords:
                continue

            try:

                # ---------------------------------
                # SINGLE POINT DICT
                # ---------------------------------

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

                frame_map[frame_no].append({

                    "object_id":
                        row.object_id,

                    "points":
                        points,
                })

            except Exception as e:

                logger.error(
                    f"Coordinate parse error: {e}"
                )

        logger.info(
            f"Prepared frames={len(frame_map)}"
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

        batch = []

        frame_numbers = sorted(
            frame_map.keys()
        )

        for frame_no in frame_numbers:

            next_frame_no = frame_no + 1

            if next_frame_no not in frame_map:
                continue

            curr_objects = frame_map[frame_no]
            next_objects = frame_map[next_frame_no]

            if (
                len(curr_objects) == 0
                or len(next_objects) == 0
            ):
                continue

            logger.info(
                f"Processing "
                f"{frame_no} -> {next_frame_no} | "
                f"curr={len(curr_objects)} | "
                f"next={len(next_objects)}"
            )

            # =====================================
            # BUILD COST MATRIX
            # =====================================

            cost_matrix = np.full(
                (
                    len(curr_objects),
                    len(next_objects),
                ),
                np.nan,
            )

            for i, curr_obj in enumerate(curr_objects):

                curr_pts = curr_obj["points"]

                for j, next_obj in enumerate(next_objects):

                    next_pts = next_obj["points"]

                    try:

                        min_pts = min(
                            len(curr_pts),
                            len(next_pts),
                        )

                        curr_use = curr_pts[:min_pts]
                        next_use = next_pts[:min_pts]

                        # SAME FORMULA AS TRK ENGINE

                        distance = np.nanmean(
                            np.abs(
                                curr_use - next_use
                            )
                        ) * 2

                        cost_matrix[i, j] = distance

                    except Exception:
                        continue

            # =====================================
            # COMPUTE MATCHES
            # =====================================

            for i, curr_obj in enumerate(curr_objects):

                row_costs = cost_matrix[i]

                valid_next = np.where(
                    ~np.isnan(row_costs)
                )[0]

                if len(valid_next) < 2:
                    continue

                order_next = valid_next[
                    np.argsort(
                        row_costs[valid_next]
                    )
                ]

                best_idx = order_next[0]
                second_idx = order_next[1]

                best_cost = row_costs[best_idx]
                second_cost = row_costs[second_idx]

                if second_cost <= 0:
                    continue

                # =====================================
                # FORWARD RATIO
                # =====================================

                fwd_ratio = (
                    best_cost / second_cost
                )

                # =====================================
                # BACKWARD RATIO
                # =====================================

                bwd_ratio = np.nan

                col_costs = cost_matrix[:, best_idx]

                valid_curr = np.where(
                    ~np.isnan(col_costs)
                )[0]

                if len(valid_curr) >= 2:

                    order_curr = valid_curr[
                        np.argsort(
                            col_costs[valid_curr]
                        )
                    ]

                    bwd_best_cost = col_costs[
                        order_curr[0]
                    ]

                    bwd_second_cost = col_costs[
                        order_curr[1]
                    ]

                    if bwd_second_cost > 0:

                        bwd_ratio = (
                            bwd_best_cost
                            / bwd_second_cost
                        )

                # =====================================
                # PICK BEST DIRECTION
                # =====================================

                use_forward = (
                    np.isnan(bwd_ratio)
                    or fwd_ratio <= bwd_ratio
                )

                uncertainty = (
                    fwd_ratio
                    if use_forward
                    else bwd_ratio
                )

                # =====================================
                # CROWD DETECTION
                # =====================================

                DISTANCE_THRESHOLD = 100

                nearby_mask = (
                    row_costs < DISTANCE_THRESHOLD
                )

                nearby_count = np.count_nonzero(
                    nearby_mask
                )

                confusion_score = (
                    uncertainty * nearby_count
                )

                is_crowded = nearby_count >= 3

                # =====================================
                # EVENT TYPE
                # =====================================

                event_type = "NORMAL"

                if nearby_count >= 3:

                    event_type = "CROWD"

                elif uncertainty >= 0.8:

                    event_type = "HIGH_UNCERTAINTY"

                # =====================================
                # STORE ONLY IMPORTANT EVENTS
                # =====================================

                if (
                    not is_crowded
                    and uncertainty < 0.7
                ):
                    continue

                batch.append(

                    FrameConfusion(

                        project_id=project_id,

                        frame_no=frame_no,

                        next_frame_no=next_frame_no,

                        current_object_id=(
                            curr_obj["object_id"]
                        ),

                        best_match_object_id=(
                            next_objects[
                                best_idx
                            ]["object_id"]
                        ),

                        second_match_object_id=(
                            next_objects[
                                second_idx
                            ]["object_id"]
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

                        is_forward=bool(
                            use_forward
                        ),

                        nearby_object_count=(
                            nearby_count
                        ),

                        confusion_score=float(
                            confusion_score
                        ),

                        is_crowded=is_crowded,

                        event_type=event_type,
                    )
                )

        # =====================================
        # BULK INSERT
        # =====================================

        FrameConfusion.objects.bulk_create(
            batch,
            batch_size=5000,
        )

        logger.info(
            f"Inserted confusion rows="
            f"{len(batch)}"
        )