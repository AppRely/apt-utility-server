import logging
import os
import shutil

import numpy as np
from django.conf import settings
from rest_framework.exceptions import ValidationError

from ..models import ActivityLog, Project
from ..TrkFile import Trk

logger = logging.getLogger(__name__)


class TrkExportService:
    """
    TRK EXPORT — FINAL, SAFE & CONSISTENT

    ✔ DELETE / BREAK / LINK supported
    ✔ Multiple BREAK supported
    ✔ NaN-heavy operations allowed
    ✔ consolidate() disabled safely
    ✔ Upload pipeline compatible
    ✔ All TRK invariants enforced
    """

    # =====================================================
    # PUBLIC API
    # =====================================================
    @classmethod
    def export(cls, *, project_id: int) -> dict:
        logger.info("[TRK-EXPORT] Start project_id=%s", project_id)

        project = Project.objects.get(project_id=project_id)
        if not project.trk_file_name:
            raise ValidationError("Project has no TRK file")

        src = os.path.join(settings.MEDIA_ROOT, "track_folder", project.trk_file_name)
        if not os.path.exists(src):
            raise ValidationError("Original TRK not found")

        export_dir = os.path.join(settings.MEDIA_ROOT, "trk_exports", str(project_id))
        os.makedirs(export_dir, exist_ok=True)

        version = cls._next_version(export_dir)
        dst = os.path.join(export_dir, f"project_{project_id}_v{version}.trk")

        shutil.copyfile(src, dst)
        trk = Trk(dst)

        # Disable consolidate (NaN-heavy operations)
        trk.pTrk.consolidate = lambda *a, **k: None

        # Apply operations
        ops = cls._build_operations(project_id)
        cls._apply_operations(trk, ops)

        # -------------------------------------------------
        # 🔑 FINAL AUTHORITATIVE SYNC (THIS IS CRITICAL)
        # -------------------------------------------------
        cls._finalize_targets(trk)

        # Optional invariant logging (safe to keep)
        cls._log_invariants(trk)

        trk.save(dst)

        logger.info("[TRK-EXPORT] Done project_id=%s version=%s", project_id, version)
        return {
            "project_id": project_id,
            "trk_version": version,
            "trk_path": dst,
        }

    # =====================================================
    # OPERATIONS
    # =====================================================
    @classmethod
    def _apply_operations(cls, trk, ops):
        for op in ops:
            if op["type"] == "delete":
                cls._apply_delete(trk, op)

        for op in ops:
            if op["type"] == "break":
                cls._apply_break(trk, op)

        for op in ops:
            if op["type"] == "link":
                cls._apply_link(trk, op)

        for op in ops:
            if op["type"] == "swap":
                cls._apply_swap(trk, op)

    # =====================================================
    # DELETE
    # =====================================================
    @staticmethod
    def _apply_delete(trk, op):
        idx = TrkExportService._target_index(trk, op["object_id"])
        if idx is None:
            return

        fs = np.arange(trk.T, dtype=np.int32)
        nan = TrkExportService._alloc_payload(trk, fs.size, 1, fill_nan=True)
        trk.settargetframe(nan, targets=[idx], fs=fs)

    # =====================================================
    # LINK
    # =====================================================
    @staticmethod
    def _apply_link(trk, op):
        src_idx = TrkExportService._target_index(trk, op["src_object_id"])
        dst_idx = TrkExportService._target_index(trk, op["dst_object_id"])
        if src_idx is None or dst_idx is None:
            return

        T0, T = trk.T0, trk.T
        start_rel = max(op["start_frame"] - T0, 0)
        fs = np.arange(start_rel, T, dtype=np.int32)

        p_dst = TrkExportService._alloc_payload(trk, fs.size, 1, fill_nan=True)
        for i, f_rel in enumerate(fs):
            frame = trk.getframe(T0 + f_rel)
            if frame is not None:
                p_dst[..., i, 0] = TrkExportService._normalize_coords(frame[..., src_idx])

        trk.settargetframe(p_dst, targets=[dst_idx], fs=fs)

        nan = TrkExportService._alloc_payload(trk, fs.size, 1, fill_nan=True)
        trk.settargetframe(nan, targets=[src_idx], fs=fs)

    # =====================================================
    # BREAK
    # =====================================================
    @staticmethod
    def _apply_break(trk, op):
        old_idx = TrkExportService._target_index(trk, op["old_object_id"])
        if old_idx is None:
            return

        new_idx = TrkExportService._ensure_target(trk, op["new_object_id"])

        T0, T = trk.T0, trk.T
        break_frame = op["break_frame"]

        break_rel = max(break_frame - T0, 0)
        fs = np.arange(break_rel + 1, T, dtype=np.int32)

        if fs.size == 0:  # 🔑 GUARD
            trk.pTrk.endframes[old_idx] = break_frame
            trk.pTrk.startframes[new_idx] = break_frame + 1
            return

        p = TrkExportService._alloc_payload(trk, fs.size, 1)

        frame = None  # 🔑 INIT
        for i, f_rel in enumerate(fs):
            frame = trk.getframe(T0 + f_rel)
            p[..., i, 0] = np.nan if frame is None else TrkExportService._normalize_coords(frame[..., old_idx])

        trk.settargetframe(p, targets=[new_idx], fs=fs)

        nan = TrkExportService._alloc_payload(trk, fs.size, 1, fill_nan=True)
        trk.settargetframe(nan, targets=[old_idx], fs=fs)

        trk.pTrk.endframes[old_idx] = break_frame
        trk.pTrk.startframes[new_idx] = break_frame + 1

    @staticmethod
    def _apply_swap(trk, op):
        idx1 = TrkExportService._target_index(trk, op["object_1_id"])
        idx2 = TrkExportService._target_index(trk, op["object_2_id"])

        if idx1 is None or idx2 is None or idx1 == idx2:
            return

        T0, T = trk.T0, trk.T

        swap_start = max(op["object_1_start"], op["object_2_start"])
        swap_end = min(op["object_1_end"], op["object_2_end"])

        start_rel = max(swap_start - T0, 0)
        end_rel = min(swap_end - T0 + 1, T)

        if start_rel >= end_rel:  # GUARD
            return

        fs = np.arange(start_rel, end_rel, dtype=np.int32)

        p1 = TrkExportService._alloc_payload(trk, fs.size, 1)
        p2 = TrkExportService._alloc_payload(trk, fs.size, 1)

        frame = None  # INIT
        for i, f_rel in enumerate(fs):
            frame = trk.getframe(T0 + f_rel)
            if frame is None:
                p1[..., i, 0] = np.nan
                p2[..., i, 0] = np.nan
            else:
                p1[..., i, 0] = TrkExportService._normalize_coords(frame[..., idx2])
                p2[..., i, 0] = TrkExportService._normalize_coords(frame[..., idx1])

        trk.settargetframe(p1, targets=[idx1], fs=fs)
        trk.settargetframe(p2, targets=[idx2], fs=fs)

    # =====================================================
    # 🔑 FINAL INVARIANT ENFORCER (THE FIX)
    # =====================================================
    @staticmethod
    def _finalize_targets(trk):
        """
        Make ALL blocks agree on target count, frames and data.
        This replaces consolidate() safely.
        """

        nt = trk.pTrk.startframes.size

        # Sync main TRK
        trk.ntargets = nt
        trk.pTrk.ntargets = nt

        # Sync secondary blocks
        for name in ("pTrkConf", "pTrkTS", "pTrkTag"):
            block = getattr(trk, name, None)
            if block is None or not hasattr(block, "data"):
                continue

            block.ntargets = nt

            # Pad ONLY missing targets
            while len(block.data) < nt:
                ref = block.data[0]
                block.data.append(np.full_like(ref, np.nan))
                block.startframes = np.append(block.startframes, trk.T1)
                block.endframes = np.append(block.endframes, trk.T0)

    @staticmethod
    def _log_invariants(trk):
        for name in ("pTrk", "pTrkConf", "pTrkTS", "pTrkTag"):
            b = getattr(trk, name, None)
            if b:
                logger.info(
                    "[TRK-CHECK] %s nt=%d start=%d end=%d data=%d",
                    name,
                    b.ntargets,
                    b.startframes.size,
                    b.endframes.size,
                    len(getattr(b, "data", [])),
                )

    # =====================================================
    # HELPERS
    # =====================================================
    @staticmethod
    def _alloc_payload(trk, frames, targets, fill_nan=False):
        p = np.empty((trk.nlandmarks, trk.d, frames, targets), dtype=np.float32)
        if fill_nan:
            p[:] = np.nan
        return p

    @staticmethod
    def _normalize_coords(c):
        """
        Always return shape (nlandmarks, d)
        """
        if c is None:
            return np.nan
        return np.squeeze(c, axis=-1) if c.ndim > 2 else c

    @staticmethod
    def _target_index(trk, obj_id):
        try:
            return list(trk.pTrkiTgt).index(obj_id)
        except ValueError:
            return None

    @staticmethod
    def _ensure_target(trk, obj_id):
        try:
            return list(trk.pTrkiTgt).index(obj_id)
        except ValueError:
            pass

        idx = trk.ntargets
        trk.setntargets(idx + 1)
        trk.pTrkiTgt[idx] = obj_id

        # Pad PRIMARY track immediately
        trk.pTrk.startframes = np.append(trk.pTrk.startframes, trk.T1)
        trk.pTrk.endframes = np.append(trk.pTrk.endframes, trk.T0)

        logger.info("[TRK] New target created idx=%d id=%d", idx, obj_id)
        return idx

    @staticmethod
    def _next_version(path):
        versions = [int(f.split("_v")[-1].split(".trk")[0]) for f in os.listdir(path) if f.endswith(".trk")]
        return max(versions, default=0) + 1

    @staticmethod
    def _build_operations(project_id):
        ops = []
        qs = ActivityLog.objects.filter(project_id=project_id, is_applied=True).order_by("activity_created_at")

        for r in qs:
            d = r.objects_data
            if r.operation == "delete":
                ops.append({"type": "delete", "object_id": d["object_id"]})
            elif r.operation == "break_object":
                ops.append(
                    {
                        "type": "break",
                        "old_object_id": d["object_id"],
                        "new_object_id": d["new_object_id"],
                        "break_frame": d["break_frame"],
                    }
                )
            elif r.operation == "link":
                ops.append(
                    {
                        "type": "link",
                        "src_object_id": d["object_2_id"],
                        "dst_object_id": d["object_1_id"],
                        "start_frame": d["object_2_start"],
                    }
                )

            elif r.operation == "swap":
                ops.append(
                    {
                        "type": "swap",
                        "object_1_id": d["object_1_id"],
                        "object_2_id": d["object_2_id"],
                        "object_1_start": d["object_1_start"],
                        "object_1_end": d["object_1_end"],
                        "object_2_start": d["object_2_start"],
                        "object_2_end": d["object_2_end"],
                    }
                )

        return ops
