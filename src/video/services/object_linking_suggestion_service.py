import math
import logging
from django.db import transaction
from ..models import Project, ObjectTrack, VideoFrame, FrameObject, ObjectLinkingSuggestion

from django.db import close_old_connections


logger = logging.getLogger(__name__)

def get_object_coordinates(project_id, object_id, frame_no):

    try:

        frame_obj = VideoFrame.objects.get(
            project_id=project_id,
            frame_no=frame_no
        )

        fo = FrameObject.objects.filter(
            frame=frame_obj,
            object_id=object_id,
            is_active=True
        ).first()

        if fo and fo.coordinates:

            c = fo.coordinates

            # bbox
            if len(c) == 4 and isinstance(c[0], (int, float)):
                return (
                    (c[0] + c[2]) / 2.0,
                    (c[1] + c[3]) / 2.0,
                )

            # keypoints
            if isinstance(c[0], list):

                x = sum(point[0] for point in c) / len(c)
                y = sum(point[1] for point in c) / len(c)

                return (x, y)
            
    except VideoFrame.DoesNotExist:
        pass

    return None

def euclidean_distance(p1, p2):
    if p1 is None or p2 is None:
        return float('inf')
    return math.hypot(p1[0] - p2[0], p1[1] - p2[1])

@transaction.atomic
def compute_object_linking_suggestions(project_id, gap_max=10, distance_threshold=None):
    """
    Compute and store linking suggestions for a project.
    Returns number of suggestions created.
    """
    logger.info(f"[LINKING] Starting for project {project_id}")
    try:
        # Use the same per-project lock as ObjectTrackRebuildService. This keeps
        # track IDs valid until all suggestion foreign keys have been committed.
        Project.objects.select_for_update().get(project_id=project_id)

        # Delete old suggestions
        deleted, _ = ObjectLinkingSuggestion.objects.filter(project_id=project_id).delete()
        logger.info(f"[LINKING] Deleted {deleted} old suggestions for project {project_id}")
        tracks = list(
            ObjectTrack.objects.filter(
                project_id=project_id,
                object_status=1
            ).select_related('project_id')
        )

        logger.info(
            f"[LINKING] Found {len(tracks)} active tracks"
        )
        suggestions = []
        for source in tracks:
            src_coords = get_object_coordinates(project_id, source.object_id, source.end_frame)
            if src_coords is None:
                continue

            candidates = ObjectTrack.objects.filter(
                project_id=project_id,
                object_status=1,
                start_frame__gte=source.end_frame + 1,
                start_frame__lte=source.end_frame + gap_max
            ).exclude(object_id=source.object_id)

            cand_data = []
            for target in candidates:
                tgt_coords = get_object_coordinates(project_id, target.object_id, target.start_frame)
                if tgt_coords is None:
                    continue
                dist = euclidean_distance(src_coords, tgt_coords)
                if math.isfinite(dist) and (distance_threshold is None or dist <= distance_threshold):
                    cand_data.append({
                        'target_track': target,
                        'distance': dist,
                        'match_score': 1.0 / (1.0 + dist),
                        'target_start_frame': target.start_frame,
                    })

            if not cand_data:
                continue

            cand_data.sort(key=lambda x: x['distance'])
            for rank, cd in enumerate(cand_data, start=1):
                suggestions.append(ObjectLinkingSuggestion(
                    project_id=project_id,
                    source_track=source,
                    source_end_frame=source.end_frame,
                    target_track=cd['target_track'],
                    target_start_frame=cd['target_start_frame'],
                    distance=cd['distance'],
                    match_score=cd['match_score'],
                    is_best_match=(rank == 1),
                    rank=rank,
                ))

            best = cand_data[0]
            second = cand_data[1] if len(cand_data) > 1 else None
            if second:
                ratio = best['distance'] / max(second['distance'], 1e-6)
                uncertainty = 1.0 - min(ratio, 1.0)
            else:
                uncertainty = 0.0
            close_count = sum(1 for c in cand_data if c['distance'] < best['distance'] * 1.5)
            confusion_score = close_count / len(cand_data)

            for s in suggestions:
                if s.source_track == source and s.rank == 1:
                    s.uncertainty = uncertainty
                    s.confusion_score = confusion_score
                    break

        if suggestions:
            ObjectLinkingSuggestion.objects.bulk_create(suggestions)
            logger.info(f"[LINKING] Created {len(suggestions)} suggestions for project {project_id}")
        else:
            logger.info(f"[LINKING] No suggestions generated for project {project_id}")

        return len(suggestions)

    except Exception as e:
        logger.exception(f"[LINKING] FATAL ERROR for project {project_id}: {e}")
        raise



class ObjectLinkingService:

    @classmethod
    def generate(
        cls,
        *,
        project_id,
    ):

        close_old_connections()

        try:

            print(
                f"LINKING STARTED | project_id={project_id}",
                flush=True,
            )

            total = compute_object_linking_suggestions(
                project_id=project_id,
            )

            print(
                f"LINKING COMPLETED | project_id={project_id} | total={total}",
                flush=True,
            )

            return total

        except Exception as e:

            print(
                f"LINKING FAILED | project_id={project_id} | error={str(e)}",
                flush=True,
            )

            raise
