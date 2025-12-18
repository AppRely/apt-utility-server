from ..models import VideoData, ObjectTrack
from .object_slot_adapter import ObjectSlotAdapter

class FrameInfoService:

    @staticmethod
    def fetch(video_id: int, frame_no: int):
        frame = VideoData.objects.get(
            video_id=video_id,
            frame_no=frame_no
        )

        object_fields = ObjectSlotAdapter.get_object_id_fields()

        # Collect object IDs present in this frame
        object_ids = {
            getattr(frame, field)
            for field in object_fields
            if getattr(frame, field) is not None
        }

        # SINGLE QUERY for all tracks
        tracks = {
            t.object_id: t
            for t in ObjectTrack.objects.filter(
                project_id_id=video_id,
                object_id__in=object_ids
            )
        }

        objects = []
        for field in object_fields:
            obj_id = getattr(frame, field)
            if obj_id is None:
                continue

            coord_field = field.replace("_id", "_coordinates")

            track = tracks.get(obj_id)
            coords = getattr(frame, coord_field)
            objects.append({
                "object_id": obj_id,
                "coordinates": coords,
                "start_frame": track.start_frame if track else None,
                "end_frame": track.end_frame if track else None,
            })

        return {
            "video_id": video_id,
            "frame_number": frame_no,
            "objects": objects,
        }