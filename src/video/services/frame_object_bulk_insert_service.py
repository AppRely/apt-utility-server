# src/video/services/frame_object_bulk_insert_service.py

# import numpy as np
# from ..models import VideoFrame, FrameObject


# class FrameObjectBulkInsertService:

#     BULK_SIZE = 5000

#     @staticmethod
#     def _sanitize(data):
#         """
#         Replace NaN / Inf with None (Postgres JSON-safe).
#         """
#         if data is None:
#             return None

#         if isinstance(data, (float, np.floating)):
#             return None if np.isnan(data) or np.isinf(data) else data

#         if isinstance(data, (list, tuple, np.ndarray)):
#             return [FrameObjectBulkInsertService._sanitize(x) for x in data]

#         return data

#     @classmethod
#     def insert(cls, *, project_id, trk):
#         bulk = []
#         total = 0

#         frames = dict(
#             VideoFrame.objects
#             .filter(project_id_id=project_id)
#             .values_list("frame_no", "id")
#         )

#         trk_object_ids = np.array(trk.pTrkiTgt).flatten()
#         start = int(trk.T0)
#         end = int(trk.T1)

#         for frame_no in range(start, end + 1):
#             if frame_no not in frames:
#                 continue

#             frame_array = trk.getframe(frame_no)
#             if frame_array is None:
#                 continue

#             arr = np.asarray(frame_array)
#             if arr.shape[-2] == 1:
#                 arr = arr.squeeze(axis=-2)
#             if arr.ndim != 3:
#                 continue

#             conf_frame = cls._extract_frame(trk, "pTrkConf", frame_no)
#             tag_frame = cls._extract_frame(trk, "pTrkTag", frame_no)
#             ts_frame = cls._extract_frame(trk, "pTrkTS", frame_no)

#             for obj_idx in range(arr.shape[-1]):
#                 coords = arr[..., obj_idx]

#                 if np.any(np.isnan(coords)):
#                     continue

#                 bulk.append(
#                     FrameObject(
#                         frame_id=frames[frame_no],           # ✅ FK ID ONLY
#                         object_id=int(trk_object_ids[obj_idx]),
#                         coordinates=cls._sanitize(coords.tolist()),
#                         confidence=cls._sanitize(cls._extract_val(conf_frame, obj_idx)),
#                         tag=cls._sanitize(cls._extract_val(tag_frame, obj_idx)),
#                         timestamp=cls._sanitize(cls._extract_val(ts_frame, obj_idx)),
#                     )
#                 )

#                 if len(bulk) >= cls.BULK_SIZE:
#                     FrameObject.objects.bulk_create(bulk, batch_size=cls.BULK_SIZE)
#                     total += len(bulk)
#                     bulk.clear()

#         if bulk:
#             FrameObject.objects.bulk_create(bulk, batch_size=cls.BULK_SIZE)
#             total += len(bulk)

#         return total

#     @staticmethod
#     def _extract_frame(trk, attr, frame_no):
#         if not hasattr(trk, attr):
#             return None

#         data = getattr(trk, attr)
#         if data is None:
#             return None

#         val = data.getframe(frame_no)
#         if val is None:
#             return None

#         val = np.asarray(val)
#         if val.shape[-2] == 1:
#             val = val.squeeze(axis=-2)
#         return val

#     @staticmethod
#     def _extract_val(frame_data, obj_idx):
#         if frame_data is None:
#             return None
#         return frame_data[..., obj_idx].tolist()



import numpy as np
from ..models import VideoFrame, FrameObject


class FrameObjectBulkInsertService:

    BULK_SIZE = 5000

    @classmethod
    def insert(cls, *, project_id, trk):
        bulk = []
        total = 0

        # ---------------------------------------------------------
        # 1. Cache VideoFrame IDs once
        # ---------------------------------------------------------
        frame_id_map = dict(
            VideoFrame.objects
            .filter(project_id_id=project_id)
            .values_list("frame_no", "id")
        )

        # TRK object IDs (may be missing or shorter than targets)
        trk_object_ids = (
            np.asarray(trk.pTrkiTgt).flatten()
            if hasattr(trk, "pTrkiTgt") and trk.pTrkiTgt is not None
            else None
        )

        start = int(trk.T0)
        end = int(trk.T1)

        # ---------------------------------------------------------
        # 2. Iterate frames (ONE pass)
        # ---------------------------------------------------------
        for frame_no in range(start, end + 1):
            frame_id = frame_id_map.get(frame_no)
            if not frame_id:
                continue

            # ---- Read ALL TRK data ONCE per frame
            frame_arr = trk.getframe(frame_no)
            if frame_arr is None:
                continue

            conf_arr = trk.pTrkConf.getframe(frame_no) if getattr(trk, "pTrkConf", None) else None
            tag_arr  = trk.pTrkTag.getframe(frame_no)  if getattr(trk, "pTrkTag", None) else None
            ts_arr   = trk.pTrkTS.getframe(frame_no)   if getattr(trk, "pTrkTS", None) else None

            arr = np.asarray(frame_arr)

            # normalize shape
            if arr.shape[-2] == 1:
                arr = arr.squeeze(axis=-2)
            if arr.ndim != 3:
                continue

            # -----------------------------------------------------
            # 3. Frame-safe object ID mapping
            # -----------------------------------------------------
            num_targets = arr.shape[-1]
            object_ids = trk_object_ids
            if object_ids is None or len(object_ids) < num_targets:
                object_ids = np.arange(num_targets)

            # -----------------------------------------------------
            # 4. Vectorized valid-object detection
            # -----------------------------------------------------
            valid_mask = ~np.isnan(arr).any(axis=(0, 1))
            valid_indices = np.where(valid_mask)[0]

            if valid_indices.size == 0:
                continue

            # -----------------------------------------------------
            # 5. Build ORM objects (safe + fast)
            # -----------------------------------------------------
            for obj_idx in valid_indices:
                coords = arr[..., obj_idx]

                # ---- SAFE sanitization (numeric + object dtype)
                if np.issubdtype(coords.dtype, np.number):
                    mask = np.isnan(coords) | np.isinf(coords)
                    if mask.any():
                        coords = coords.astype(object)
                        coords[mask] = None
                    coords = coords.tolist()
                else:
                    coords = [
                        [
                            None if (
                                x is None
                                or (isinstance(x, float) and np.isnan(x))
                                or (isinstance(x, float) and np.isinf(x))
                            ) else x
                            for x in row
                        ]
                        for row in coords
                    ]


                bulk.append(
                    FrameObject(
                        frame_id=frame_id,
                        object_id=int(object_ids[obj_idx]),
                        coordinates=coords,
                        confidence=cls._safe_scalar(conf_arr, obj_idx),
                        tag=cls._safe_scalar(tag_arr, obj_idx),
                        timestamp=cls._safe_scalar(ts_arr, obj_idx),
                    )
                )

            # -----------------------------------------------------
            # 6. Bulk flush
            # -----------------------------------------------------
            if len(bulk) >= cls.BULK_SIZE:
                FrameObject.objects.bulk_create(
                    bulk,
                    batch_size=cls.BULK_SIZE,
                )
                total += len(bulk)
                bulk.clear()

        # ---------------------------------------------------------
        # 7. Final flush
        # ---------------------------------------------------------
        if bulk:
            FrameObject.objects.bulk_create(
                bulk,
                batch_size=cls.BULK_SIZE,
            )
            total += len(bulk)

        return total

    # -------------------------------------------------------------
    # Helper: extract scalar safely (NO recursion)
    # -------------------------------------------------------------
    @staticmethod
    def _safe_scalar(arr, idx):
        if arr is None:
            return None

        try:
            val = arr[..., idx]
        except Exception:
            return None

        # Handle object / mixed dtype safely
        if not np.issubdtype(np.asarray(val).dtype, np.number):
            return val.tolist() if hasattr(val, "tolist") else val

        # Numeric path
        if np.isnan(val).any() or np.isinf(val).any():
            return None

        return val.tolist()
