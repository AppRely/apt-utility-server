# services/frame_info_service.py

from ..models import VideoFrame, FrameObject, ObjectTrack


class FrameInfoService:
    @staticmethod
    def fetch(video_id: int, frame_no: int) -> dict:
        """
        Fetch tracking data for a single frame.
        Uses nearest previous frame as fallback.
        """


        frame = (
            VideoFrame.objects
            .filter(project_id_id=video_id, frame_no__lte=frame_no)
            .order_by("-frame_no")
            .first()
        )

        if not frame:
            raise VideoFrame.DoesNotExist("No valid frame found")


        frame_objects = list(
            FrameObject.objects.filter(frame=frame)
        )

        if not frame_objects:
            return {
                "video_id": video_id,
                "frame_number": frame.frame_no,
                "objects": [],
            }


        object_ids = {obj.object_id for obj in frame_objects}

        tracks = {
            t.object_id: t
            for t in ObjectTrack.objects.filter(
                project_id_id=video_id,
                object_id__in=object_ids,
                object_status=1,
            )
        }


        objects = []
        for obj in frame_objects:
            track = tracks.get(obj.object_id)

            objects.append({
                "object_id": obj.object_id,
                "coordinates": obj.coordinates,
                "confidence": obj.confidence,
                "tag": obj.tag,
                "timestamp": obj.timestamp,
                "start_frame": track.start_frame if track else None,
                "end_frame": track.end_frame if track else None,
            })

        return {
            "video_id": video_id,
            "frame_number": frame.frame_no,
            "objects": objects,
        }
