import logging
import os
import shutil

import numpy as np

from django.conf import settings
from rest_framework.exceptions import ValidationError
from .object_track_rebuild_service import ObjectTrackRebuildService

from ..models import (
    Project,
    ObjectTrack,
    VideoFrame,
    FrameObject,
)

from ..TrkFile import Trk

logger = logging.getLogger(__name__)



class TrkBuilderExportService:
    """
    Database Driven TRK Export

    This exporter DOES NOT replay ActivityLog.

    It rebuilds the final TRK directly from the
    current database state.

    Source of truth:

        ObjectTrack
        FrameObject
        VideoFrame
    """

    def __init__(self, project_id):

        self.project_id = project_id

        self.project = None

        self.trk = None

        self.export_path = None
        self.original_trk_path = None   # <-- ADD THIS

        self.object_tracks = []

        self.video_frames = []

        self.frame_objects = []

        self.trk_version = None

        #
        # object_id -> target index
        #
        self.target_map = {}

        #
        # frame_no -> VideoFrame
        #
        self.frame_map = {}

        #
        # object_id -> {
        #       track
        #       frames
        # }
        #
        self.objects = {} 

    # =====================================================
    # PUBLIC API
    # =====================================================

    @classmethod
    def export(cls, project_id):
        # Rebuild ObjectTrack from authoritative FrameObject state before export
        
        ObjectTrackRebuildService.rebuild(project_id=project_id)

        builder = cls(project_id)

        return builder.build()

    # =====================================================
    # BUILD
    # =====================================================

    def build(self):

        self._load_project()

        self._prepare_export_file()

        self._load_trk()

        self._load_database()

        self._build_object_map()

        self._allocate_targets()

        self._write_all_track_data()

        self._install_tracklets()

        self._write_metadata()

        self._finalize_metadata()

        self._save_trk()


        return {
            "project_id": self.project_id,
            "trk_version": self.trk_version,
            "trk_path": self.export_path,
        }


    # =====================================================
    # LOAD PROJECT
    # =====================================================

    def _load_project(self):

        self.project = Project.objects.get(
            project_id=self.project_id
        )

        if not self.project.trk_file_name:
            raise ValidationError(
                "Project has no TRK file."
            )

    # =====================================================
    # CREATE EXPORT FILE
    # =====================================================

    def _prepare_export_file(self):

        src = os.path.join(
            settings.MEDIA_ROOT,
            "track_folder",
            self.project.trk_file_name,
        )

        # ADD THIS LINE
        self.original_trk_path = src

        if not os.path.exists(src):
            raise ValidationError(
                "Original TRK not found."
            )

        export_dir = os.path.join(
            settings.MEDIA_ROOT,
            "trk_exports",
            str(self.project_id),
        )

        os.makedirs(
            export_dir,
            exist_ok=True,
        )

        version = 1

        while True:

            dst = os.path.join(
                export_dir,
                f"project_{self.project_id}_v{version}.trk",
            )

            if not os.path.exists(dst):
                break

            version += 1

        shutil.copyfile(
            src,
            dst,
        )

        self.export_path = dst
        self.trk_version = version

        logger.info(
            "Export File : %s",
            dst,
        )

    # =====================================================
    # LOAD TRK
    # =====================================================
    def _load_trk(self):
        self.trk = Trk( self.export_path )

    # =====================================================
    # LOAD DATABASE
    # =====================================================

    def _load_database(self):

        self.object_tracks = list(
            ObjectTrack.objects.filter(
                project_id=self.project_id,
                object_status=1,
            )
            .only(
                "object_id",
                "start_frame",
                "end_frame",
            )
            .order_by("object_id")
        )

        self.video_frames = list(
            VideoFrame.objects.filter(
                project_id=self.project_id,
            )
            .only(
                "frame_no",
            )
            .order_by("frame_no")
        )

        self.frame_map = {

            x.frame_no: x

            for x in self.video_frames

        }

        self.frame_objects = list(
            FrameObject.objects.filter(
                frame__project_id=self.project_id,
                is_active=True,
            )
            .select_related("frame")
            .only(
                "object_id",
                "coordinates",
                "confidence",
                "timestamp",
                "tag",
                "frame__frame_no",
            )
            .order_by(
                "object_id",
                "frame__frame_no",
            )
        )

    # =====================================================
    # OBJECT MAP
    # =====================================================

    def _build_object_map(self):

        #
        # Create object dictionary
        #
        self.objects = {
            track.object_id: {
                "track": track,
                "frames": {},
            }
            for track in self.object_tracks
        }

        #
        # Attach frame objects
        #
        for row in self.frame_objects:

            obj = self.objects.get(row.object_id)

            if obj is not None:
                obj["frames"][row.frame.frame_no] = row

    def _allocate_targets(self):

        #
        # Get all object IDs currently present in the database
        #
        object_ids = sorted(self.objects.keys())

        if not object_ids:
            highest_id = -1
            total_targets = 0
        else:
            highest_id = max(object_ids)
            total_targets = highest_id + 1

        #
        # Reinitialize ALL tracklets
        #
        self.trk.setntargets(
            total_targets,
        )

        if self.trk.pTrkConf is not None:
            self.trk.pTrkConf.setntargets(total_targets)

        if hasattr(self.trk, "pTrkAnimalConf") and self.trk.pTrkAnimalConf is not None:
            self.trk.pTrkAnimalConf.setntargets(total_targets)

        #
        # Keep TRK target ids exactly equal to object ids
        self.trk.ntargets = total_targets

        self.trk.pTrkiTgt = np.arange(
            total_targets,
            dtype=np.int32,
        )

        #
        # Build lookup
        #
        self.target_map = {}

        for object_id in object_ids:
            self.target_map[object_id] = object_id


    def _write_all_track_data(self):
        total_targets = self.trk.ntargets

        # ------------------------------------------------------------
        #  FIX: Force explicit floating-point types for numeric data
        #  (coords, confidence, timestamp) to avoid OverflowError
        #  when to_py() subtracts 1 during loading.
        # ------------------------------------------------------------
        coord_dtype = np.float32          # coordinates are floating-point
        conf_dtype  = np.float32 if self.trk.pTrkConf else None
        ts_dtype    = np.float64 if self.trk.pTrkTS else None
        tag_dtype   = bool if self.trk.pTrkTag else None

        # If you have animal confidence, force it to float too
        has_animal_conf = hasattr(self.trk, "pTrkAnimalConf") and self.trk.pTrkAnimalConf is not None
        if has_animal_conf:
            animal_conf_dtype = np.float32   # or use self.trk.pTrkAnimalConf... but force float

        # Allocate container lists
        coord_data = [None] * total_targets
        conf_data = [None] * total_targets if self.trk.pTrkConf else None
        ts_data = [None] * total_targets if self.trk.pTrkTS else None
        tag_data = [None] * total_targets if self.trk.pTrkTag else None
        animal_conf_data = [None] * total_targets if has_animal_conf else None

        object_ids = sorted(self.objects.keys())

        for obj_index, object_id in enumerate(object_ids, start=1):
            target = self.target_map[object_id]

            track = self.objects[object_id]["track"]
            start_f = track.start_frame
            end_f = track.end_frame
            n_frames_in_track = max(0, end_f - start_f + 1)

            # Allocate arrays filled with default values (np.nan, False, -np.inf)
            coords = np.full(
                (self.trk.nlandmarks, self.trk.d, n_frames_in_track),
                np.nan,
                dtype=coord_dtype,
            )

            conf = (
                np.full(
                    (self.trk.nlandmarks, 1, n_frames_in_track),
                    np.nan,
                    dtype=conf_dtype,
                )
                if self.trk.pTrkConf
                else None
            )

            ts = (
                np.full(
                    (self.trk.nlandmarks, 1, n_frames_in_track),
                    -np.inf,
                    dtype=ts_dtype,
                )
                if self.trk.pTrkTS
                else None
            )

            tag = (
                np.full(
                    (self.trk.nlandmarks, 1, n_frames_in_track),
                    False,
                    dtype=tag_dtype,
                )
                if self.trk.pTrkTag
                else None
            )

            animal_conf = (
                np.full(
                    self.trk.pTrkAnimalConf.size_rest + (n_frames_in_track,),
                    np.nan,
                    dtype=animal_conf_dtype,
                )
                if has_animal_conf
                else None
            )

            # Fill the arrays at the correct frame index offset
            for frame_no, row in self.objects[object_id]["frames"].items():
                idx = frame_no - start_f
                if 0 <= idx < n_frames_in_track:
                    coords[:, :, idx] = row.coordinates
                    if conf is not None:
                        conf[:, :, idx] = row.confidence
                    if ts is not None:
                        ts[:, :, idx] = row.timestamp
                    if tag is not None:
                        tag[:, :, idx] = row.tag

            # Store in the containers
            coord_data[target] = coords
            if conf is not None:
                conf_data[target] = conf
            if ts is not None:
                ts_data[target] = ts
            if tag is not None:
                tag_data[target] = tag
            if animal_conf is not None:
                animal_conf_data[target] = animal_conf

        # Save for later installation
        self.coord_data = coord_data
        self.conf_data = conf_data
        self.ts_data = ts_data
        self.tag_data = tag_data
        self.animal_conf_data = animal_conf_data

        print("\nTracklet build complete.")

    def _install_tracklets(self):

        self.trk.pTrk.data = self.coord_data

        if self.trk.pTrkConf is not None:
            self.trk.pTrkConf.data = self.conf_data

        if self.trk.pTrkTS is not None:
            self.trk.pTrkTS.data = self.ts_data

        if self.trk.pTrkTag is not None:
            self.trk.pTrkTag.data = self.tag_data

        if hasattr(self.trk, "pTrkAnimalConf") and self.trk.pTrkAnimalConf is not None:
            self.trk.pTrkAnimalConf.data = self.animal_conf_data

        print("Tracklets Installed")

    def _write_metadata(self):

        total_targets = self.trk.ntargets

        startframes = np.full(
            total_targets,
            -1,
            dtype=np.int32,
        )

        endframes = np.full(
            total_targets,
            -2,
            dtype=np.int32,
        )

        for object_id, data in self.objects.items():

            target = self.target_map[object_id]

            track = data["track"]

            startframes[target] = track.start_frame
            endframes[target] = track.end_frame

        #
        # Main Track
        #
        self.trk.pTrk.startframes = startframes.copy()
        self.trk.pTrk.endframes = endframes.copy()

        #
        # Confidence
        #
        if self.trk.pTrkConf is not None:

            self.trk.pTrkConf.startframes = startframes.copy()
            self.trk.pTrkConf.endframes = endframes.copy()

        #
        # Timestamp
        #
        if self.trk.pTrkTS is not None:

            self.trk.pTrkTS.startframes = startframes.copy()
            self.trk.pTrkTS.endframes = endframes.copy()

        #
        # Tag
        #
        if self.trk.pTrkTag is not None:

            self.trk.pTrkTag.startframes = startframes.copy()
            self.trk.pTrkTag.endframes = endframes.copy()

        #
        # Animal Confidence
        #
        if (
            hasattr(self.trk, "pTrkAnimalConf")
            and self.trk.pTrkAnimalConf is not None
        ):

            self.trk.pTrkAnimalConf.startframes = startframes.copy()
            self.trk.pTrkAnimalConf.endframes = endframes.copy()

        print("Metadata Written")

    def _finalize_metadata(self):


        total_targets = self.trk.ntargets

        #
        # Update TRK target count
        #
        self.trk.ntargets = total_targets

        #
        # Update all available tracklets
        #
        tracklets = [
            self.trk.pTrk,
            self.trk.pTrkConf,
            self.trk.pTrkTS,
            self.trk.pTrkTag,
        ]

        if hasattr(self.trk, "pTrkAnimalConf"):
            tracklets.append(self.trk.pTrkAnimalConf)

        for tracklet in tracklets:

            if tracklet is None:
                continue

            tracklet.ntargets = total_targets

        #
        # Target IDs
        #
        self.trk.pTrkiTgt = np.arange(
            total_targets,
            dtype=np.int32,
        )
        

    @staticmethod
    def _sanitize_unsigned_dtypes(trk):
        """
        Ensure no unsigned-integer array reaches the .trk file.

        TrkFile's MATLAB<->Python index conversion does `array + (-1)` /
        `array + 1` on integer fields (pTrkiTgt, startframes, endframes).
        NumPy >= 2.0 raises OverflowError if that array's dtype is
        unsigned (e.g. uint64), even for a value that would round-trip
        fine mathematically. MATLAB-style HDF5 files also don't reliably
        round-trip numpy dtypes on read, sometimes handing back uint64
        for fields that were written as int32/float32.

        We can't edit the vendored TrkFile.py, so we guarantee here --
        right before writing -- that every integer field on the object
        we're about to save is a signed dtype. This makes the exported
        file safer to reopen later with any copy of TrkFile.py, on any
        NumPy version.
        """

        def fix_array(arr):
            if isinstance(arr, np.ndarray) and np.issubdtype(arr.dtype, np.unsignedinteger):
                return arr.astype(np.int64)
            return arr

        def fix_tracklet(tracklet):
            if tracklet is None:
                return
            if getattr(tracklet, "startframes", None) is not None:
                tracklet.startframes = fix_array(tracklet.startframes)
            if getattr(tracklet, "endframes", None) is not None:
                tracklet.endframes = fix_array(tracklet.endframes)
            data = getattr(tracklet, "data", None)
            if data is not None:
                tracklet.data = [fix_array(d) for d in data]

        fix_tracklet(trk.pTrk)
        for field_name in ("pTrkConf", "pTrkTS", "pTrkTag", "pTrkAnimalConf"):
            fix_tracklet(getattr(trk, field_name, None))

        if getattr(trk, "pTrkiTgt", None) is not None:
            trk.pTrkiTgt = fix_array(trk.pTrkiTgt)

    def _save_trk(self):

        #
        # Defensive: strip any unsigned-integer dtypes before writing,
        # regardless of where they came from.
        #
        self._sanitize_unsigned_dtypes(self.trk)

        self.trk.save(self.export_path)

        print("\nSAVE COMPLETE")

