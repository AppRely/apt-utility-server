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
    CROWD_THRESHOLD = getattr(settings, "CONFUSION_CROWD_THRESHOLD", 3)
    FRAME_SAMPLE_STEP = getattr(settings, "CONFUSION_FRAME_SAMPLE_STEP", 30)
    CROWD_RADIUS = getattr(settings, "CONFUSION_CROWD_RADIUS", 80.0)

    # Unused but kept for compatibility
    MIN_UNCERTAINTY = getattr(settings, "CONFUSION_MIN_UNCERTAINTY", 0.80)
    SAMPLE_FRAMES = getattr(settings, "CONFUSION_SAMPLE_FRAMES", 300)

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
        """Stub for compatibility."""
        return {"p75": 5.0, "p95": 10.0, "std": 3.0}

    @staticmethod
    def _compute_centroid(points):
        if len(points) == 0:
            return None
        return np.mean(points, axis=0)

    @classmethod
    def generate(cls, *, project_id):
        project = Project.objects.get(
        project_id=project_id
        )

        project.confusion_status = "PROCESSING"
        project.save(update_fields=["confusion_status"])

        try:
            print(f"CROWD DETECTION STARTED | project_id={project_id}", flush=True)

            frame_map = cls._load_project_objects(project_id)
            frame_numbers = sorted(frame_map.keys())
            print(f"TOTAL FRAMES AVAILABLE={len(frame_numbers)}", flush=True)

            sampled_frames = frame_numbers[::cls.FRAME_SAMPLE_STEP]
            print(f"SAMPLED FRAMES={len(sampled_frames)} (every {cls.FRAME_SAMPLE_STEP} frames)", flush=True)

            # Clear old confusion data
            FrameConfusion.objects.filter(project_id=project_id).delete()

            top_events = []  # heap of (-crowd_score, frame_no, main_obj_id, instance)

            for frame_no in sampled_frames:
                objects = frame_map.get(frame_no, [])
                if len(objects) < cls.CROWD_THRESHOLD:
                    continue

                # Compute centroids
                obj_data = []
                for obj in objects:
                    pts = obj["points"]
                    if pts is None or len(pts) == 0:
                        continue
                    centroid = cls._compute_centroid(pts)
                    if centroid is not None:
                        obj_data.append((obj["object_id"], centroid))

                if len(obj_data) < cls.CROWD_THRESHOLD:
                    continue

                # Find neighbours within radius
                neighbour_lists = {}
                neighbour_counts = {}
                for i, (id_i, pos_i) in enumerate(obj_data):
                    neighbours = []
                    for j, (id_j, pos_j) in enumerate(obj_data):
                        if i == j:
                            continue
                        if np.linalg.norm(pos_i - pos_j) <= cls.CROWD_RADIUS:
                            neighbours.append(id_j)
                    neighbour_lists[id_i] = neighbours
                    neighbour_counts[id_i] = len(neighbours)

                if not neighbour_counts:
                    continue

                # Main object = one with most neighbours
                main_obj_id = max(neighbour_counts, key=neighbour_counts.get)
                nearby_count = neighbour_counts[main_obj_id]
                nearby_ids = neighbour_lists[main_obj_id]

                if nearby_count < cls.CROWD_THRESHOLD - 1:
                    continue

                # Compute crowd density score
                main_centroid = next(c for (oid, c) in obj_data if oid == main_obj_id)
                if nearby_count > 0:
                    distances = []
                    for nid in nearby_ids:
                        n_centroid = next(c for (oid, c) in obj_data if oid == nid)
                        distances.append(np.linalg.norm(main_centroid - n_centroid))
                    mean_dist = np.mean(distances)
                    neighbour_ratio = nearby_count / (cls.CROWD_THRESHOLD - 1)
                    proximity = max(0.0, 1.0 - (mean_dist / cls.CROWD_RADIUS))
                    crowd_score = min(neighbour_ratio * proximity, 1.0)
                else:
                    crowd_score = 0.0

                # Create FrameConfusion record (repurposed fields)
                event = FrameConfusion(
                    project_id=project_id,
                    frame_no=frame_no,
                    next_frame_no=frame_no,   # unused
                    current_object_id=main_obj_id,
                    best_match_object_id=None,
                    second_match_object_id=None,
                    best_match_cost=0.0,
                    second_match_cost=0.0,
                    uncertainty=0.0,
                    nearby_object_count=nearby_count,
                    nearby_object_ids=nearby_ids,          # <-- store the list
                    confusion_score=crowd_score,
                    is_crowded=True,
                    event_type="CROWD",
                    is_forward=True,
                )

                # Maintain top-K heap (by crowd_score)
                if len(top_events) < cls.TOP_K:
                    heapq.heappush(top_events, (-crowd_score, frame_no, main_obj_id, event))
                else:
                    heapq.heappushpop(top_events, (-crowd_score, frame_no, main_obj_id, event))

            # Bulk create
            final_batch = [ev[3] for ev in top_events]
            FrameConfusion.objects.bulk_create(final_batch, batch_size=1000)

            print(f"INSERTED CROWD EVENTS = {len(final_batch)}", flush=True)
            project.confusion_status = "COMPLETED"
            project.save(update_fields=["confusion_status"])
            print(f"CROWD DETECTION COMPLETED | project_id={project_id}", flush=True)

            return {
                "status": "success",
                "project_id": project_id,
                "crowd_events_stored": len(final_batch),
            }

        except Exception as e:
            project.confusion_status = "FAILED"
            project.save(update_fields=["confusion_status"])
            print(f"CONFUSION FAILED | project_id={project_id} | {e}", flush=True)
            traceback.print_exc()
            raise