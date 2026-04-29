from ..models import FrameObject, VideoFrame


class FrameObjectRangeNoFallbackService:
    @staticmethod
    def fetch(project_id: int, start_frame: int, end_frame: int):
        # Fetch relevant VideoFrames in the range [start, end]
        queryset = (
            FrameObject.objects.filter(
                is_active=True, frame__project_id=project_id, frame__frame_no__range=(start_frame, end_frame)
            )
            .values("object_id", "coordinates", "frame__frame_no")  # optimized
            .order_by("frame__frame_no")
        )

        results = {}

        for obj in queryset:
            obj_id = obj["object_id"]
            frame_no = obj["frame__frame_no"]
            coords = obj["coordinates"]

            if obj_id not in results:
                results[obj_id] = {"object_id": obj_id, "frames": []}

            frames = results[obj_id]["frames"]

            # remove duplicate frames
            if frames and frames[-1]["coordinates"] == coords:
                continue

            frames.append(
                {
                    "frame_id": frame_no,
                    "coordinates": coords,
                }
            )

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
                results[obj_id] = {"object_id": obj_id, "frames": []}

            results[obj_id]["frames"].append(
                {
                    "frame_id": target_frame_no,
                    "coordinates": obj.coordinates,
                }
            )
