from collections import defaultdict
from ..models import VideoData
from .object_slot_adapter import ObjectSlotAdapter

class FrameObjectRangeService:

    @staticmethod
    def fetch(video_id: int, start_frame: int, end_frame: int, extra_frames=None):
        qs = (
            VideoData.objects
            .filter(
                video_id=video_id,
                frame_no__gte=start_frame,
                frame_no__lte=end_frame
            )
            .order_by("frame_no")
        )

        frames_by_no = {row.frame_no: row for row in qs}

        results = {}
        last_valid_row = None

        for frame_no in range(start_frame, end_frame + 1):
            row = frames_by_no.get(frame_no)

            if row:
                last_valid_row = row
            else:
                row = last_valid_row 

            if not row:
                continue

            FrameObjectRangeService._collect_objects(results, row, frame_no)

        return list(results.values())


    @staticmethod
    def _collect_objects(results: dict, row, frame_no: int):
        """
        Collect object data from a single VideoData row.
        """
        for id_field in ObjectSlotAdapter.get_object_id_fields():
            obj_id = getattr(row, id_field)

            if obj_id is None:
                continue

            coord_field = id_field.replace("_id", "_coordinates")
            coords = getattr(row, coord_field)

            if obj_id not in results:
                results[obj_id] = {
                    "object_id": obj_id,
                    "frames": []
                }

            results[obj_id]["frames"].append({
                "frame_id": frame_no,
                "coordinates": coords,
            })