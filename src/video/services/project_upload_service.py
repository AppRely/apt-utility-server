# src/video/services/project_upload_service.py

import os
import numpy as np

from django.db import transaction

from ..models import Project
from .frame_object_bulk_insert_service import FrameObjectBulkInsertService
from .object_track_rebuild_service import ObjectTrackRebuildService
from .project_file_storage_service import ProjectFileStorageService
from .trk_validation_service import TrkValidationService
from .video_frame_bulk_insert_service import VideoFrameBulkInsertService

from .background_executor import executor
from .confusion_store_service import ConfusionStoreService
from .object_linking_suggestion_service import ObjectLinkingService


class ProjectUploadService:
    @staticmethod
    def make_json_serializable(obj):
        if isinstance(obj, np.ndarray):
            if obj.size == 1:
                return obj.item()
            return [ProjectUploadService.make_json_serializable(x) for x in obj.tolist()]

        elif isinstance(obj, list):
            return [ProjectUploadService.make_json_serializable(x) for x in obj]

        elif isinstance(obj, tuple):
            return [ProjectUploadService.make_json_serializable(x) for x in obj]

        elif isinstance(obj, dict):
            return {
                k: ProjectUploadService.make_json_serializable(v)
                for k, v in obj.items()
            }

        elif isinstance(obj, np.integer):
            return int(obj)

        elif isinstance(obj, np.floating):
            return float(obj)

        return obj

    @classmethod
    def create(cls, *, project_name, video_file, tracking_file, request):
        # OUTSIDE DB transaction
        video_path, trk_path, metadata = ProjectFileStorageService.save(project_name, video_file, tracking_file)

        trk = TrkValidationService.load_and_validate(trk_path)
        skeleton_graph = []
        try:
            params = trk.trkData["trkInfo"]["params"]

            op_graph = params.get("op_affinity_graph")
            dpk_graph = params.get("dpk_graph")

            if op_graph is not None and len(op_graph) > 0:
                skeleton_graph = op_graph

            elif dpk_graph is not None and len(dpk_graph) > 0:
                skeleton_graph = dpk_graph

            else:
                skeleton_graph = []

        except Exception as e:
            print(f"Skeleton extraction failed: {e}")
            skeleton_graph = []

        # Always convert to JSON-safe Python objects
        skeleton_graph = cls.make_json_serializable(skeleton_graph)
        with transaction.atomic():
            project = Project.objects.create(
                project_name=project_name,
                video_name=os.path.basename(video_path),
                video_path=video_path,  # temporary
                video_storage_path=video_path,  # 
                trk_file_name=os.path.basename(trk_path),
                trk_file_path=trk_path,
                trk_storage_path=trk_path,
                project_status="inprogress",
                status="Completed",
                confusion_status="COMPLETED",
                skeleton_graph=skeleton_graph,
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

        print(f"BEFORE THREAD SUBMIT | project_id={project.project_id}", flush=True)
        transaction.on_commit(lambda: executor.submit( ConfusionStoreService.generate, project_id=project.project_id, ))
        # transaction.on_commit(lambda: executor.submit(project.project_id,))

        transaction.on_commit(lambda: executor.submit(ObjectLinkingService.generate, project_id=project.project_id,))
        print(f"AFTER THREAD SUBMIT | project_id={project.project_id}", flush=True)
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
