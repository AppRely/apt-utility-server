# src/video/services/video_data_bulk_insert_service.py

import numpy as np
from ..models import VideoData


class VideoDataBulkInsertService:

    BULK_INSERT_CHUNK_SIZE = 5000
    MAX_OBJECT_SLOTS = 10

    @classmethod
    def insert(cls, *, project_id, trk):
        bulk = []
        total = 0

        trk_object_ids = np.array(trk.pTrkiTgt).flatten()
        start = int(getattr(trk, "T0", 0))
        end = int(getattr(trk, "T1", start))

        for frame in range(start, end + 1):
            frame_array = trk.getframe(frame)
            if frame_array is None:
                continue

            arr = np.asarray(frame_array)
            if arr.shape[-2] == 1:
                arr = arr.squeeze(axis=-2)
            if arr.ndim != 3:
                continue

            objects_present = []
            objects_data = {}

            for obj_idx in range(arr.shape[-1]):
                coords = arr[..., obj_idx]
                if np.any(np.isnan(coords)):
                    continue

                objects_present.append(obj_idx)
                objects_data[obj_idx] = coords.tolist()

            if not objects_present:
                continue

            slots = {}
            slot_num = 1

            for obj_idx in objects_present[:cls.MAX_OBJECT_SLOTS]:
                true_id = int(trk_object_ids[obj_idx])
                slots[f"object_{slot_num}_id"] = true_id
                slots[f"object_{slot_num}_coordinates"] = objects_data[obj_idx]
                slot_num += 1

            for s in range(slot_num, cls.MAX_OBJECT_SLOTS + 1):
                slots[f"object_{s}_id"] = None
                slots[f"object_{s}_coordinates"] = None

            bulk.append(
                VideoData(
                    video_id=project_id,
                    frame_no=frame,
                    frame_timestamp=float(frame),
                    trk_timestamp=float(frame),
                    **slots,
                )
            )

            if len(bulk) >= cls.BULK_INSERT_CHUNK_SIZE:
                VideoData.objects.bulk_create(bulk)
                total += len(bulk)
                bulk = []

        if bulk:
            VideoData.objects.bulk_create(bulk)
            total += len(bulk)

        return total
