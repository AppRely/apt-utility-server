import os
import subprocess
from django.conf import settings


class ProjectFileStorageService:

    @staticmethod
    def save(project_name, video_file, tracking_file):
        media_root = settings.MEDIA_ROOT

        video_dir = os.path.join(media_root, "video_folder")
        trk_dir = os.path.join(media_root, "track_folder")

        os.makedirs(video_dir, exist_ok=True)
        os.makedirs(trk_dir, exist_ok=True)

        original_video_name = video_file.name
        video_path = os.path.join(video_dir, original_video_name)
        trk_path = os.path.join(trk_dir, tracking_file.name)

        # write video
        with open(video_path, "wb") as f:
            for chunk in video_file.chunks():
                f.write(chunk)

        # Convert to MP4 if not already MP4
        final_video_path = video_path
        if not original_video_name.lower().endswith(".mp4"):
            base_name = os.path.splitext(original_video_name)[0]
            final_video_name = f"{base_name}.mp4"
            final_video_path = os.path.join(video_dir, final_video_name)

            try:
                # FFmpeg command to convert to mp4
                # -y: overwrite output files
                # -i: input file
                # -c:v libx264: use x264 codec
                # -crf 23: constant rate factor (quality)
                # -preset medium: encoding speed/quality tradeoff
                # -c:a aac: use aac audio codec
                subprocess.run(
                    ["ffmpeg", "-y", "-i", video_path, "-c:v", "libx264", "-crf", "23", "-preset", "medium", "-c:a", "aac", final_video_path],
                    check=True,
                    capture_output=True
                )
                # Delete original file after successful conversion
                if os.path.exists(video_path):
                    os.remove(video_path)
            except subprocess.CalledProcessError as e:
                # If conversion fails, we keep the original and log the error
                # In a real app, you might want to raise an exception here
                print(f"FFmpeg conversion failed: {e.stderr.decode()}")
                final_video_path = video_path

        # write trk
        with open(trk_path, "wb") as f:
            for chunk in tracking_file.chunks():
                f.write(chunk)

        return final_video_path, trk_path

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
