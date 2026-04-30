# src/video/services/project_upload_service.py

import os

from django.db import transaction

from ..models import Project
from .frame_object_bulk_insert_service import FrameObjectBulkInsertService
from .object_track_rebuild_service import ObjectTrackRebuildService
from .project_file_storage_service import ProjectFileStorageService
from .trk_validation_service import TrkValidationService
from .video_frame_bulk_insert_service import VideoFrameBulkInsertService


class ProjectUploadService:
    @classmethod
    def create(cls, *, project_name, video_file, tracking_file, request):
        # OUTSIDE DB transaction
        video_path, trk_path, metadata = ProjectFileStorageService.save(project_name, video_file, tracking_file)

        trk = TrkValidationService.load_and_validate(trk_path)

        with transaction.atomic():
            project = Project.objects.create(
                project_name=project_name,
                video_name=os.path.basename(video_path),
                video_path=video_path,  # temporary
                trk_file_name=os.path.basename(trk_path),
                trk_file_path=trk_path,
                project_status="inprogress",
                status="Completed",
                # Save Metadata
                fps=metadata.get("fps"),
                width=metadata.get("width"),
                height=metadata.get("height"),
                duration=metadata.get("duration"),
                total_frames=metadata.get("total_frames"),
            )

            # REMOVED INVALID video_id LOGIC

            frame_count = VideoFrameBulkInsertService.insert(project_id=project.project_id, trk=trk)

            rows = FrameObjectBulkInsertService.insert(project_id=project.project_id, trk=trk)

            if rows == 0:
                raise ValueError("Upload failed — no TRK frames inserted")

            # ObjectTrackBulkInsertService.insert(
            #     project_id=project.project_id,
            #     trk=trk
            # )

            ObjectTrackRebuildService.rebuild(project_id=project.project_id)

        # AFTER DB INSERT → store STREAM URLs
        try:
            video_url, trk_url = ProjectFileStorageService.build_stream_urls(project.project_id, request)

            project.video_path = video_url
            project.trk_file_path = trk_url
            project.save(update_fields=["video_path", "trk_file_path"])
        except Exception:
            # If URL generation fails, we still return the project but with local paths
            # This prevents the whole upload from failing due to a minor post-processing error
            video_url = project.video_path
            trk_url = project.trk_file_path

        return {
            "project_id": project.project_id,
            "rows_inserted": rows,
            "video_stream_url": video_url,
            "trk_stream_url": trk_url,
        }
