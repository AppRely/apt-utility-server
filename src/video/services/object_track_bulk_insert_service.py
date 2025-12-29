# src/video/services/object_track_bulk_insert_service.py

import numpy as np
from ..models import ObjectTrack


class ObjectTrackBulkInsertService:

    @staticmethod
    def insert(*, project_id, trk):
        starts = np.array(trk.startframes).flatten()
        ends = np.array(trk.endframes).flatten()
        ids = np.array(trk.pTrkiTgt).flatten()

        bulk = []

        for i in range(len(ids)):
            bulk.append(
                ObjectTrack(
                    project_id_id=project_id,
                    object_id=int(ids[i]),
                    start_frame=int(starts[i]),
                    end_frame=int(ends[i]),
                    object_status=1,
                    operation_note=None,
                )
            )

        ObjectTrack.objects.bulk_create(bulk, batch_size=1000)
