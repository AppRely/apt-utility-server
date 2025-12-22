# src/video/services/frame_object_bulk_insert_service.py

import numpy as np
from ..models import VideoFrame, FrameObject


class FrameObjectBulkInsertService:

    BULK_SIZE = 5000

    @staticmethod
    def _sanitize(data):
        """
        Replace NaN / Inf with None (Postgres JSON-safe).
        """
        if data is None:
            return None

        if isinstance(data, (float, np.floating)):
            return None if np.isnan(data) or np.isinf(data) else data

        if isinstance(data, (list, tuple, np.ndarray)):
            return [FrameObjectBulkInsertService._sanitize(x) for x in data]

        return data

    @classmethod
    def insert(cls, *, project_id, trk):
        bulk = []
        total = 0

        frames = {
            vf.frame_no: vf
            for vf in VideoFrame.objects.filter(project_id_id=project_id)
        }

        trk_object_ids = np.array(trk.pTrkiTgt).flatten()
        start = int(trk.T0)
        end = int(trk.T1)

        for frame_no in range(start, end + 1):
            frame = frames.get(frame_no)
            if not frame:
                continue

            frame_array = trk.getframe(frame_no)
            if frame_array is None:
                continue

            arr = np.asarray(frame_array)
            if arr.shape[-2] == 1:
                arr = arr.squeeze(axis=-2)
            if arr.ndim != 3:
                continue

            for obj_idx in range(arr.shape[-1]):
                coords = arr[..., obj_idx]

                if np.any(np.isnan(coords)):
                    continue

                bulk.append(
                    FrameObject(
                        frame=frame,                         # ✅ FK ONLY
                        object_id=int(trk_object_ids[obj_idx]),
                        coordinates=cls._sanitize(coords.tolist()),
                        confidence=cls._sanitize(
                            cls._extract(trk, "pTrkConf", frame_no, obj_idx)
                        ),
                        tag=cls._sanitize(
                            cls._extract(trk, "pTrkTag", frame_no, obj_idx)
                        ),
                        timestamp=cls._sanitize(
                            cls._extract(trk, "pTrkTS", frame_no, obj_idx)
                        ),
                    )
                )

                if len(bulk) >= cls.BULK_SIZE:
                    FrameObject.objects.bulk_create(bulk)
                    total += len(bulk)
                    bulk.clear()

        if bulk:
            FrameObject.objects.bulk_create(bulk)
            total += len(bulk)

        return total

    @staticmethod
    def _extract(trk, attr, frame_no, obj_idx):
        if not hasattr(trk, attr):
            return None

        data = getattr(trk, attr)
        if data is None:
            return None

        val = data.getframe(frame_no)
        if val is None:
            return None

        val = np.asarray(val)
        if val.shape[-2] == 1:
            val = val.squeeze(axis=-2)

        return val[..., obj_idx].tolist()
