from django.db.models import Prefetch
from ..models import VideoFrame, FrameObject

class FrameObjectRangeNoFallbackService:

    @staticmethod
    def fetch(project_id: int, start_frame: int, end_frame: int):
        # Fetch relevant VideoFrames in the range [start, end]
        frames_qs = (
            VideoFrame.objects
            .filter(project_id=project_id, frame_no__range=(start_frame, end_frame))
            .prefetch_related(
                Prefetch("frame_objects", queryset=FrameObject.objects.filter(is_active=True))
            )
            .order_by("frame_no")
        )

        results = {}

        for frame in frames_qs:
            FrameObjectRangeNoFallbackService._collect_objects(results, frame, frame.frame_no)

        return list(results.values())

    @staticmethod
    def _collect_objects(results: dict, frame: VideoFrame, target_frame_no: int):
        """
        Collect object data from a VideoFrame and add it to results
        mapped to the target_frame_no.
        """
        for obj in frame.frame_objects.all():
            obj_id = obj.object_id
            
            if obj_id not in results:
                results[obj_id] = {
                    "object_id": obj_id,
                    "frames": []
                }
            
            results[obj_id]["frames"].append({
                "frame_id": target_frame_no,
                "coordinates": obj.coordinates,
            })
