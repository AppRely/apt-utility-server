# src/video/services/project_upload_service.py

from django.db import transaction
from ..models import Project
from .project_file_storage_service import ProjectFileStorageService
from .trk_validation_service import TrkValidationService
from .video_frame_bulk_insert_service import VideoFrameBulkInsertService
from .frame_object_bulk_insert_service import FrameObjectBulkInsertService
from .object_track_bulk_insert_service import ObjectTrackBulkInsertService


class ProjectUploadService:

    @classmethod
    def create(cls, *, project_name, video_file, tracking_file, request):

        with transaction.atomic():

            video_path, trk_path = ProjectFileStorageService.save(
                project_name, video_file, tracking_file
            )

            trk = TrkValidationService.load_and_validate(trk_path)

            project = Project.objects.create(
                project_name=project_name,
                video_name=video_file.name,
                video_path=video_path,
                trk_file_name=tracking_file.name,
                trk_file_path=trk_path,
                project_status="inprogress",
                status="Completed",
            )

            # REMOVED INVALID video_id LOGIC

            frame_count = VideoFrameBulkInsertService.insert(
                project_id=project.project_id,
                trk=trk
            )

            rows = FrameObjectBulkInsertService.insert(
                project_id=project.project_id,
                trk=trk
            )

            if rows == 0:
                raise ValueError("Upload failed — no TRK frames inserted")

            ObjectTrackBulkInsertService.insert(
                project_id=project.project_id,
                trk=trk
            )

            video_url, trk_url = ProjectFileStorageService.build_stream_urls(
                project.project_id, request
            )

            project.video_path = video_url
            project.trk_file_path = trk_url
            project.save(update_fields=["video_path", "trk_file_path"])

            return {
                "project_id": project.project_id,
                "rows_inserted": rows,
                "video_stream_url": video_url,
                "trk_stream_url": trk_url,
            }
