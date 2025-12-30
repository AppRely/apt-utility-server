

from collections import defaultdict
from django.db.models import Q
from ..models import VideoFrame, FrameObject, Project

class FrameObjectRangeService:

    @staticmethod
    def fetch(project_id: int, start_frame: int, end_frame: int, extra_frames=None):
        if extra_frames is None:
            extra_frames = []

        # 1. Fetch relevant VideoFrames
        # We need frames in the range [start, end] AND any extra fallback frames
        frame_filter = Q(project_id=project_id) & (
            Q(frame_no__range=(start_frame, end_frame)) |
            Q(frame_no__in=extra_frames)
        )

        frames_qs = (
            VideoFrame.objects
            .filter(frame_filter)
            .prefetch_related("frame_objects")
            .order_by("frame_no")
        )

        frames_map = {f.frame_no: f for f in frames_qs}
        available_frames = sorted(frames_map.keys())

        # 2. Initialize results container
        # Key: object_id, Value: dict with object info and frames list
        results = {}

        # 3. Determine initial active frame (for start_frame)
        # If start_frame exists, it's the active one.
        # If not, find the nearest previous frame from available_frames.
        active_frame = None
        if start_frame in frames_map:
            active_frame = frames_map[start_frame]
        else:
            # Find max frame < start_frame
            prev_frames = [f for f in available_frames if f < start_frame]
            if prev_frames:
                active_frame = frames_map[prev_frames[-1]]

        # 4. Iterate through the requested range
        for f_no in range(start_frame, end_frame + 1):
            # Update active_frame if we hit a new valid frame
            if f_no in frames_map:
                active_frame = frames_map[f_no]
            
            # If we have a valid frame (either current or carried over), collect its objects
            if active_frame:
                FrameObjectRangeService._collect_objects(results, active_frame, f_no)

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
                "confidence": obj.confidence,
                "tag": obj.tag,
                "timestamp": obj.timestamp
            })