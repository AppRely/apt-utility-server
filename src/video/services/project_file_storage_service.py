import os
from django.conf import settings


class ProjectFileStorageService:

    @staticmethod
    def save(project_name, video_file, tracking_file):
        media_root = settings.MEDIA_ROOT

        video_dir = os.path.join(media_root, "video_folder")
        trk_dir = os.path.join(media_root, "track_folder")

        os.makedirs(video_dir, exist_ok=True)
        os.makedirs(trk_dir, exist_ok=True)

        video_path = os.path.join(video_dir, video_file.name)
        trk_path = os.path.join(trk_dir, tracking_file.name)

        # write video
        with open(video_path, "wb") as f:
            for chunk in video_file.chunks():
                f.write(chunk)

        # write trk
        with open(trk_path, "wb") as f:
            for chunk in tracking_file.chunks():
                f.write(chunk)

        return video_path, trk_path

    @staticmethod
    def build_stream_urls(project_id, request):
        if not request:
            return None, None

        video_url = request.build_absolute_uri(
            f"/api/v1/videos/{project_id}/project-stream/"
        )
        trk_url = request.build_absolute_uri(
            f"/api/v1/videos/{project_id}/project-stream-trk/"
        )

        return video_url, trk_url
