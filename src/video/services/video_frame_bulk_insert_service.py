# src/video/services/video_frame_bulk_insert_service.py

from ..models import VideoFrame

class VideoFrameBulkInsertService:

    @staticmethod
    def insert(*, project_id, trk):
        frames = []
        append = frames.append

        start = int(trk.T0)
        end = int(trk.T1)

        for frame_no in range(start, end + 1):
            append(
                VideoFrame(
                    project_id_id=project_id,
                    frame_no=frame_no,
                )
            )

        VideoFrame.objects.bulk_create(frames, batch_size=5000)
        return len(frames)
