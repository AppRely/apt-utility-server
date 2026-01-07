import os
import subprocess
import json
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
        
        def is_browser_compatible(video_path):
            try:
                result = subprocess.run(
                    [
                        "ffprobe", "-v", "error",
                        "-select_streams", "v:0",
                        "-show_entries", "stream=codec_name,pix_fmt",
                        "-of", "json",
                        video_path
                    ],
                    capture_output=True,
                    text=True,
                    check=True
                )
                data = json.loads(result.stdout)
                if "streams" in data and len(data["streams"]) > 0:
                    stream = data["streams"][0]
                    codec = stream.get("codec_name")
                    pix_fmt = stream.get("pix_fmt")
                    return codec == "h264" and pix_fmt == "yuv420p"
                return False
            except Exception:
                return False

        if not original_video_name.lower().endswith(".mp4") or not is_browser_compatible(video_path):
            base_name = os.path.splitext(original_video_name)[0]
            final_video_name = f"{base_name}_converted.mp4"
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
                    [
                        "ffmpeg", "-y", "-i", video_path,
                        "-c:v", "libx264",
                        "-pix_fmt", "yuv420p",
                        "-preset", "fast",
                        "-crf", "23",
                        "-movflags", "+faststart",
                        final_video_path
                    ],
                    check=True
                )
                
                # Delete original file only if we successfully created a new one and it's different
                if final_video_path != video_path and os.path.exists(final_video_path):
                    if os.path.exists(video_path):
                        os.remove(video_path)
            except subprocess.CalledProcessError as e:
                print(f"FFmpeg conversion failed: {e}")
                final_video_path = video_path
        else:
            final_video_path = video_path

        # write trk
        with open(trk_path, "wb") as f:
            for chunk in tracking_file.chunks():
                f.write(chunk)

        # Extract Metadata
        metadata = {}
        try:
            cmd = [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height,r_frame_rate,duration,nb_frames",
                "-of", "json", final_video_path
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            data = json.loads(result.stdout)
            
            if data.get('streams'):
                stream = data['streams'][0]
                
                fps_str = stream.get('r_frame_rate', '30/1')
                if '/' in fps_str:
                    num, den = map(int, fps_str.split('/'))
                    fps = num / den if den != 0 else 30.0
                else:
                    fps = float(fps_str)
                
                duration = float(stream.get('duration', 0))
                total_frames = int(stream.get('nb_frames', 0))
                
                if duration == 0 and total_frames > 0 and fps > 0:
                    duration = total_frames / fps
                elif total_frames == 0 and duration > 0 and fps > 0:
                    total_frames = int(duration * fps)

                metadata = {
                    "fps": round(fps, 2),
                    "width": stream.get('width'),
                    "height": stream.get('height'),
                    "duration": round(duration, 2),
                    "total_frames": total_frames
                }
        except Exception as e:
            print(f"Metadata extraction failed: {e}")

        return final_video_path, trk_path, metadata


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
