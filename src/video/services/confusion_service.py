# video/services/confusion_service.py

import logging
import numpy as np

from ..models import FrameObject

# =====================================
# LOGGER
# =====================================

logger = logging.getLogger(__name__)


class ConfusionService:

    @staticmethod
    def fetch(
        project_id,
        start_frame,
        end_frame,
    ):

        logger.info(
            f"Starting confusion calculation | "
            f"project_id={project_id} | "
            f"start={start_frame} | "
            f"end={end_frame}"
        )

        # =====================================
        # FETCH FRAME OBJECTS
        # =====================================

        qs = (
            FrameObject.objects.filter(
                frame__project_id_id=project_id,
                frame__frame_no__gte=start_frame,
                frame__frame_no__lte=end_frame + 1,
                is_active=True,
            )
            .select_related("frame")
            .order_by(
                "frame__frame_no",
                "object_id",
            )
        )

        logger.info(
            f"Total DB rows fetched={qs.count()}"
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
                # HANDLE DIFFERENT COORD FORMATS
                # ---------------------------------

                if isinstance(coords, dict):

                    # SINGLE POINT
                    # {x:100,y:200}

                    x = coords.get("x")
                    y = coords.get("y")

                    if x is None or y is None:
                        continue

                    points = np.array([[x, y]])

                elif isinstance(coords, list):

                    # MULTI KEYPOINTS
                    # [[x1,y1],[x2,y2]]

                    if (
                        len(coords) > 0
                        and isinstance(coords[0], list)
                    ):

                        points = np.array(coords)

                    # SINGLE POINT
                    # [x,y]

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
                    f"Coordinate parsing error: {e}"
                )

                continue

        logger.info(
            f"Total frames prepared="
            f"{len(frame_map)}"
        )

        # =====================================
        # CALCULATE UNCERTAINTY
        # =====================================

        rows = []

        frame_numbers = sorted(
            frame_map.keys()
        )

        for frame_no in frame_numbers:

            next_frame = frame_no + 1

            if next_frame not in frame_map:
                continue

            curr_objects = frame_map[frame_no]

            next_objects = frame_map[next_frame]

            if (
                len(curr_objects) == 0
                or len(next_objects) == 0
            ):
                continue

            logger.info(
                f"Processing frame "
                f"{frame_no} -> {next_frame} | "
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

                        # ---------------------------------
                        # MATCH SHAPES
                        # ---------------------------------

                        min_pts = min(
                            len(curr_pts),
                            len(next_pts),
                        )

                        curr_use = curr_pts[:min_pts]
                        next_use = next_pts[:min_pts]

                        # ---------------------------------
                        # SAME FORMULA AS EXTERNAL FILE
                        # ---------------------------------

                        distance = np.nanmean(
                            np.abs(
                                curr_use - next_use
                            )
                        ) * 2

                        cost_matrix[i, j] = distance

                    except Exception:
                        continue

            # =====================================
            # COMPUTE UNCERTAINTY
            # =====================================

            for i, curr_obj in enumerate(curr_objects):

                row_costs = cost_matrix[i]

                valid_idx = np.where(
                    ~np.isnan(row_costs)
                )[0]

                if len(valid_idx) < 2:
                    continue

                sorted_idx = valid_idx[
                    np.argsort(
                        row_costs[valid_idx]
                    )
                ]

                best_idx = sorted_idx[0]

                second_idx = sorted_idx[1]

                best_cost = row_costs[best_idx]

                second_cost = row_costs[second_idx]

                if second_cost <= 0:
                    continue

                uncertainty = (
                    best_cost / second_cost
                )

                rows.append({

                    "frame":
                        frame_no,

                    "object_id":
                        curr_obj["object_id"],

                    "best_match":
                        next_objects[
                            best_idx
                        ]["object_id"],

                    "second_match":
                        next_objects[
                            second_idx
                        ]["object_id"],

                    "uncertainty":
                        round(
                            float(
                                uncertainty
                            ),
                            4,
                        ),

                    "is_forward":
                        True,
                })

        # =====================================
        # SORT HIGH CONFUSION FIRST
        # =====================================

        rows.sort(
            key=lambda x: x["uncertainty"],
            reverse=True,
        )

        logger.info(
            f"Final confusion rows="
            f"{len(rows)}"
        )

        return rows