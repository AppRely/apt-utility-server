# confusion_store_service.py

import heapq
import logging
import traceback
from collections import defaultdict

import numpy as np
from django.conf import settings

from ..models import FrameObject, FrameConfusion, Project

logger = logging.getLogger(__name__)


class ConfusionStoreService:

    TOP_K = getattr(settings, "CONFUSION_TOP_K", 1000)
    MIN_UNCERTAINTY = getattr(settings, "CONFUSION_MIN_UNCERTAINTY", 0.80)
    SAMPLE_FRAMES = getattr(settings, "CONFUSION_SAMPLE_FRAMES", 300)
    CROWD_THRESHOLD = 3  # lowered from 5 to work better with 10 objects/frame

    @staticmethod
    def _fetch_frame_objects(*, project_id, frame_no):
        rows = (
            FrameObject.objects.filter(
                frame__project_id_id=project_id,
                frame__frame_no=frame_no,
                is_active=True,
            )
            .only("object_id", "coordinates")
        )

        objects = []

        for row in rows:
            coords = row.coordinates
            if not coords:
                continue
            try:
                if isinstance(coords, dict):
                    x = coords.get("x")
                    y = coords.get("y")
                    if x is None or y is None:
                        continue
                    points = np.array([[x, y]])
                elif isinstance(coords, list):
                    if len(coords) > 0 and isinstance(coords[0], list):
                        points = np.array(coords)
                    elif len(coords) >= 2:
                        points = np.array([[coords[0], coords[1]]])
                    else:
                        continue
                else:
                    continue
                objects.append({
                    "object_id": row.object_id,
                    "points": points,
                })
            except Exception as e:
                logger.error(f"Coordinate parse error: {e}")

        return objects

    @staticmethod
    def _load_project_objects(project_id):
        frame_map = defaultdict(list)

        rows = (
            FrameObject.objects
            .filter(
                frame__project_id_id=project_id,
                is_active=True,
            )
            .values(
                "frame__frame_no",
                "object_id",
                "coordinates",
            )
            .iterator(chunk_size=5000)
        )

        for row in rows:
            coords = row["coordinates"]
            if not coords:
                continue
            try:
                if isinstance(coords, dict):
                    x = coords.get("x")
                    y = coords.get("y")
                    if x is None or y is None:
                        continue
                    points = np.array([[x, y]])
                elif isinstance(coords, list):
                    if len(coords) > 0 and isinstance(coords[0], list):
                        points = np.array(coords)
                    elif len(coords) >= 2:
                        points = np.array([[coords[0], coords[1]]])
                    else:
                        continue
                else:
                    continue
                frame_map[row["frame__frame_no"]].append({
                    "object_id": row["object_id"],
                    "points": points,
                })
            except Exception:
                continue

        return frame_map

    @classmethod
    def _build_motion_statistics(cls, *, project_id):
        """
        Compute adaptive thresholds from a sample of consecutive frame pairs.
        Returns a dict with p75, p95, and std of the distances.
        """
        frame_map = cls._load_project_objects(project_id)
        frame_numbers = sorted(frame_map.keys())
        if len(frame_numbers) < 2:
            return {"p75": 5.0, "p95": 10.0, "std": 3.0}

        total_frames = len(frame_numbers)
        sample_step = max(1, total_frames // cls.SAMPLE_FRAMES)
        sampled_indices = range(0, total_frames, sample_step)

        distances = []

        for idx in sampled_indices:
            frame_no = frame_numbers[idx]
            next_frame_no = frame_no + 1
            if next_frame_no not in frame_map:
                continue

            current_objects = frame_map[frame_no]
            next_objects = frame_map[next_frame_no]

            if not current_objects or not next_objects:
                continue

            for curr_obj in current_objects:
                curr_pts = curr_obj["points"]
                for next_obj in next_objects:
                    next_pts = next_obj["points"]
                    try:
                        min_pts = min(len(curr_pts), len(next_pts))
                        curr_use = curr_pts[:min_pts]
                        next_use = next_pts[:min_pts]
                        distance = float(np.nanmean(np.abs(curr_use - next_use)) * 2)
                        if distance >= 0:
                            distances.append(distance)
                    except Exception:
                        continue

        if not distances:
            return {"p75": 5.0, "p95": 10.0, "std": 3.0}

        distances = np.array(distances)
        p75 = np.percentile(distances, 75)
        p95 = np.percentile(distances, 95)
        std = np.std(distances)

        p75 = max(p75, 0.1)
        p95 = max(p95, p75 + 0.1)
        std = max(std, 0.5)

        return {"p75": float(p75), "p95": float(p95), "std": float(std)}

    @classmethod
    def generate(cls, *, project_id):
        project = Project.objects.get(
        project_id=project_id
        )

        project.confusion_status = "PROCESSING"
        project.save(update_fields=["confusion_status"])

        try:
            print(f"CONFUSION STARTED | project_id={project_id}", flush=True)

            frame_map = cls._load_project_objects(project_id)
            frame_numbers = sorted(frame_map.keys())

            print(f"TOTAL FRAMES={len(frame_numbers)}", flush=True)

            # Dynamic thresholds per project
            stats = cls._build_motion_statistics(project_id=project_id)
            max_good_match_cost = stats["p75"]
            distance_threshold = stats["p95"]
            max_cost_gap = max(stats["std"], 3.0)

            print(
                f"THRESHOLDS | "
                f"match={max_good_match_cost} | "
                f"distance={distance_threshold} | "
                f"gap={max_cost_gap}",
                flush=True,
            )

            # Clear old confusion data
            FrameConfusion.objects.filter(project_id=project_id).delete()

            top_events = []  # heap of (severity, frame_no, object_id, instance)

            for idx, frame_no in enumerate(frame_numbers):
                if idx % 1000 == 0:
                    print(f"PROCESSED {idx}/{len(frame_numbers)}", flush=True)

                next_frame_no = frame_no + 1
                current_objects = frame_map.get(frame_no, [])
                next_objects = frame_map.get(next_frame_no, [])

                if not current_objects or not next_objects:
                    continue

                for curr_obj in current_objects:
                    curr_pts = curr_obj["points"]
                    candidates = []

                    for next_obj in next_objects:
                        next_pts = next_obj["points"]
                        try:
                            min_pts = min(len(curr_pts), len(next_pts))
                            curr_use = curr_pts[:min_pts]
                            next_use = next_pts[:min_pts]
                            distance = float(np.nanmean(np.abs(curr_use - next_use)) * 2)

                            if distance > distance_threshold:
                                continue

                            candidates.append({
                                "object_id": next_obj["object_id"],
                                "distance": distance,
                            })
                        except Exception:
                            continue

                    nearby_count = len(candidates)
                    if nearby_count < 2:
                        continue

                    candidates.sort(key=lambda x: x["distance"])
                    best = candidates[0]
                    second = candidates[1]

                    best_cost = best["distance"]
                    second_cost = second["distance"]

                    if second_cost <= 0:
                        continue
                    if best_cost > max_good_match_cost:
                        continue

                    cost_gap = second_cost - best_cost
                    if cost_gap > max_cost_gap:
                        continue

                    uncertainty = best_cost / second_cost
                    if uncertainty < cls.MIN_UNCERTAINTY:
                        continue

                    # --- Event type classification (restored original logic) ---
                    if uncertainty >= 0.90 and cost_gap <= (max_cost_gap * 0.5):
                        event_type = "ID_SWITCH_RISK"
                    elif nearby_count >= cls.CROWD_THRESHOLD:
                        event_type = "CROWD_OVERLAP"
                    else:
                        event_type = "HIGH_UNCERTAINTY"

                    # Debug log for crowd detection (optional)
                    if nearby_count >= 3:
                        print(
                            f"CROWD DETECTED | frame={frame_no} | count={nearby_count}",
                            flush=True,
                        )

                    # --- Severity score with crowd bonus ---
                    crowd_bonus = min(nearby_count / 10.0, 1.0)
                    severity_score = (
                        uncertainty * 0.6
                        +
                        (1 - min(cost_gap / (max_cost_gap * 2), 1.0)) * 0.3
                        +
                        crowd_bonus * 0.1
                    )

                    event = (
                        severity_score,
                        frame_no,
                        curr_obj["object_id"],
                        FrameConfusion(
                            project_id=project_id,
                            frame_no=frame_no,
                            next_frame_no=next_frame_no,
                            current_object_id=curr_obj["object_id"],
                            best_match_object_id=best["object_id"],
                            second_match_object_id=second["object_id"],
                            best_match_cost=best_cost,
                            second_match_cost=second_cost,
                            uncertainty=uncertainty,
                            nearby_object_count=nearby_count,
                            confusion_score=severity_score,
                            is_crowded=(nearby_count >= cls.CROWD_THRESHOLD),
                            event_type=event_type,
                            is_forward=True,
                        )
                    )

                    # Maintain top-K heap
                    if len(top_events) < cls.TOP_K:
                        heapq.heappush(top_events, event)
                    else:
                        heapq.heappushpop(top_events, event)

            # Build final batch sorted by severity descending
            final_batch = [
                event[3]
                for event in sorted(top_events, key=lambda x: x[0], reverse=True)
            ]

            FrameConfusion.objects.bulk_create(final_batch, batch_size=1000)

            print(f"INSERTED ROWS={len(final_batch)}", flush=True)
            project.confusion_status = "COMPLETED"
            project.save(update_fields=["confusion_status"])
            print(f"CONFUSION COMPLETED | project_id={project_id}", flush=True)

        except Exception as e:
            project.confusion_status = "FAILED"
            project.save(update_fields=["confusion_status"])
            print(f"CONFUSION FAILED | project_id={project_id} | {e}", flush=True)
            traceback.print_exc()