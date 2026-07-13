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

        logger.info("=" * 80)
        logger.info("NEW DATABASE EXPORT")
        logger.info("=" * 80)

        self._load_project()

        self._prepare_export_file()

        self._load_trk()

        self._load_database()

        self._build_object_map()

        self._verify_original_trk()

        self._print_database_info()

        self._allocate_targets()

        self._verify_allocated_trk()
        # self._write_single_object(1387)
        # self._write_object(1387)
        # self._write_all_coordinates()
        # self._write_all_track_data()
        # self._verify_payload_shape(1387)
        # self._write_single_frame(1387)

        #
        # PART-2
        #
        # self._allocate_targets()

        #
        # PART-3
        #
        # self._write_coordinates()

        #
        # PART-4
        #
        # self._write_metadata()

        #
        # PART-5
        #
        # self._save()

        self._write_all_track_data()

        self._install_tracklets()

        self._write_metadata()

        self._finalize_metadata()

        self._save_trk()

        self._verify_saved_trk()
       
        # Compare metadata with the original
        self._verify_metadata_integrity()

        return {
            "project_id": self.project_id,
            "trk_version": self.trk_version,
            "trk_path": self.export_path,
        }

        # return {
        #     "project_id": self.project_id,
        #     "trk_version": self.trk_version,
        #     "trk_path": self.export_path,
        # }

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

        self.trk = Trk(
            self.export_path
        )

        print("\n" + "=" * 80)
        print("ORIGINAL TRACKLET INFORMATION")
        print("=" * 80)

        print("Data Type        :", type(self.trk.pTrk.data))
        print("Element Type     :", type(self.trk.pTrk.data[0]))
        print("Coordinate Dtype :", self.trk.pTrk.data[0].dtype)
        print("Coordinate Shape :", self.trk.pTrk.data[0].shape)

        if self.trk.pTrkConf is not None:
            print("Confidence Dtype :", self.trk.pTrkConf.data[0].dtype)

        if self.trk.pTrkTS is not None:
            print("Timestamp Dtype  :", self.trk.pTrkTS.data[0].dtype)

        if self.trk.pTrkTag is not None:
            print("Tag Dtype        :", self.trk.pTrkTag.data[0].dtype)

        print("=" * 80)

        print(
            "Original TRK Loaded"
        )

        print(
            "Targets     : %d",
            self.trk.ntargets,
        )

        print(
            "Frames      : %d",
            self.trk.T,
        )

        print(
            "Landmarks   : %d",
            self.trk.nlandmarks,
        )

    # =====================================================
    # LOAD DATABASE
    # =====================================================

    def _load_database(self):

        logger.info(
            "Loading ObjectTrack..."
        )

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

        logger.info(
            "Tracks : %d",
            len(self.object_tracks),
        )

        logger.info(
            "Loading VideoFrame..."
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

        logger.info(
            "Frames : %d",
            len(self.video_frames),
        )

        self.frame_map = {

            x.frame_no: x

            for x in self.video_frames

        }

        logger.info(
            "Loading FrameObject..."
        )

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

        logger.info(
            "FrameObjects : %d",
            len(self.frame_objects),
        )

    # =====================================================
    # OBJECT MAP
    # =====================================================

    def _build_object_map(self):

        print("\n" + "=" * 80)
        print("BUILDING OBJECT MAP")
        print("=" * 80)

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

        total_objects = len(self.objects)

        print(f"Objects Loaded : {total_objects}")

        #
        # Print only first 5 objects
        #
        object_ids = sorted(self.objects.keys())[:5]

        for object_id in object_ids:

            print(
                f"Object {object_id} -> "
                f"{len(self.objects[object_id]['frames'])} Frames"
            )

        print("=" * 80)

    def _print_trk_info(self):

        print("\n" + "=" * 80)
        print("TRK INFORMATION")
        print("=" * 80)

        print("ntargets : %d", self.trk.ntargets)
        print("T0       : %d", self.trk.T0)
        print("T        : %d", self.trk.T)
        print("Landmarks: %d", self.trk.nlandmarks)
        print("Sparse   : %s", self.trk.issparse)

        print("pTrkiTgt : %d", len(self.trk.pTrkiTgt))

        print("pTrk      : %d", self.trk.pTrk.ntargets)

        if self.trk.pTrkConf is not None:
            print("pTrkConf  : %d", self.trk.pTrkConf.ntargets)

        if self.trk.pTrkTS is not None:
            print("pTrkTS    : %d", self.trk.pTrkTS.ntargets)

        if self.trk.pTrkTag is not None:
            print("pTrkTag   : %d", self.trk.pTrkTag.ntargets)

        if hasattr(self.trk, "pTrkAnimalConf") and self.trk.pTrkAnimalConf is not None:
            print("AnimalConf: %d", self.trk.pTrkAnimalConf.ntargets)

    def _print_database_info(self):

        print("\n" + "=" * 80)
        print("DATABASE INFORMATION")
        print("=" * 80)

        print(f"Object Tracks    : {len(self.object_tracks)}")
        print(f"Frame Objects    : {len(self.frame_objects)}")

        ids = sorted(self.objects.keys())

        print(f"First Object ID  : {ids[0]}")
        print(f"Last Object ID   : {ids[-1]}")
        print(f"Highest ObjectID : {max(ids)}")

        print("=" * 80)


    def _allocate_targets(self):

        print("\n" + "=" * 80)
        print("ALLOCATING TARGETS")
        print("=" * 80)

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

        print(f"Highest Object ID : {highest_id}")
        print(f"Database Objects  : {len(object_ids)}")

        print(f"Allocating Targets : {total_targets}")

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

        print("\nAllocation Complete")
        print("----------------------------")
        print(f"TRK Targets      : {self.trk.ntargets}")
        print(f"Target Array     : {len(self.trk.pTrkiTgt)}")
        print(f"Highest Target   : {highest_id}")
        print(f"Active Objects   : {len(self.target_map)}")
        print("=" * 80)

    def _verify_original_trk(self):

        print("\nVERIFY ORIGINAL TRK")

        print("=" * 80)

        print("Targets :", self.trk.ntargets)

        print("Last IDs :", self.trk.pTrkiTgt[-20:])

        print("Startframes :", self.trk.pTrk.startframes[-5:])

        print("Endframes :", self.trk.pTrk.endframes[-5:])

        print("=" * 80)


    def _verify_allocated_trk(self):

        print("\n")
        print("=" * 80)
        print("VERIFY ALLOCATED TRK")
        print("=" * 80)

        print("TRK Targets      :", self.trk.ntargets)
        print("Target Array     :", len(self.trk.pTrkiTgt))

        print("Last Target IDs  :")
        print(self.trk.pTrkiTgt[-20:])

        print()

        print("Startframes Shape :", self.trk.pTrk.startframes.shape)
        print("Endframes Shape   :", self.trk.pTrk.endframes.shape)

        print()

        print("Last Startframes")
        print(self.trk.pTrk.startframes[-10:])

        print()

        print("Last Endframes")
        print(self.trk.pTrk.endframes[-10:])

        print("=" * 80)


    # def _write_single_object(self, object_id):

    #     if object_id not in self.objects:
    #         print(f"Object {object_id} not found.")
    #         return

    #     target = self.target_map[object_id]

    #     print("\n" + "=" * 80)
    #     print(f"OBJECT : {object_id}")
    #     print(f"TARGET : {target}")
    #     print("=" * 80)

    #     frames = self.objects[object_id]["frames"]

    #     print(f"Total Frames : {len(frames)}")

    #     #
    #     # First frame only
    #     #
    #     frame_no = sorted(frames.keys())[0]

    #     row = frames[frame_no]

    #     print("Frame :", frame_no)

    #     print("Coordinates")
    #     print(row.coordinates)

    #     print("Confidence")
    #     print(row.confidence)

    #     print("Timestamp")
    #     print(row.timestamp)

    #     print("Tag")
    #     print(row.tag)

    def _verify_payload_shape(self, object_id):

        target = self.target_map[object_id]

        frame_no = min(self.objects[object_id]["frames"])

        print("\n" + "=" * 80)
        print("VERIFY PAYLOAD")
        print("=" * 80)

        frame = self.trk.getframe(frame_no)

        print("Frame Shape :", frame.shape)

        print()

        print("Target Shape :")

        print(frame[..., target])

        print()

        print("Target Shape Dimensions")

        print(frame[..., target].shape)

        print("=" * 80)


    def _write_single_frame(self, object_id):

        target = self.target_map[object_id]

        frame_no = min(self.objects[object_id]["frames"])

        row = self.objects[object_id]["frames"][frame_no]

        coords = np.asarray(
            row.coordinates,
            dtype=np.float32,
        )

        coords = coords[:, :, None, None]

        print("\n")
        print("=" * 80)
        print("WRITING SINGLE FRAME")
        print("=" * 80)

        print("Frame :", frame_no)

        print("Target:", target)

        print("Payload Shape:", coords.shape)

        fs = np.array(
            [frame_no],
            dtype=np.int32,
        )
        print(type(fs))
        print(fs)
        self.trk.settargetframe(
            coords,
            targets=[target],
            fs=fs,
        )
        #
        # Read back
        #

        frame = self.trk.getframe(frame_no)

        written = frame[..., target]

        print()

        print("Written Shape :", written.shape)

        print()

        print("Written Values")

        print(written)

        print("=" * 80)

    def _write_object(self, object_id):

        if object_id not in self.objects:
            print(f"Object {object_id} not found.")
            return

        target = self.target_map[object_id]

        frames = self.objects[object_id]["frames"]

        print("\n" + "=" * 80)
        print(f"WRITING OBJECT : {object_id}")
        print(f"TARGET         : {target}")
        print(f"TOTAL FRAMES   : {len(frames)}")
        print("=" * 80)

        frame_numbers = sorted(frames.keys())

        for i, frame_no in enumerate(frame_numbers):

            row = frames[frame_no]

            #
            # Coordinates
            #
            coords = np.asarray(
                row.coordinates,
                dtype=np.float32,
            )

            #
            # (17,2) -> (17,2,1,1)
            #
            coords = coords[:, :, None, None]

            self.trk.settargetframe(
                coords,
                targets=[target],
                fs=np.array([frame_no], dtype=np.int32),
            )

            #
            # Progress
            #
            if i % 100 == 0:
                print(
                    f"Written {i + 1}/{len(frame_numbers)} frames"
                )

        #
        # Verify first frame
        #
        first_frame = frame_numbers[0]

        frame = self.trk.getframe(first_frame)

        print("\nVerification")

        print(frame[..., target])

        print("=" * 80)

    def _write_all_coordinates(self):

        print("\n" + "=" * 80)
        print("WRITING ALL COORDINATES")
        print("=" * 80)

        object_ids = sorted(self.objects.keys())

        total_objects = len(object_ids)

        for obj_index, object_id in enumerate(object_ids, start=1):

            target = self.target_map[object_id]
            frames = self.objects[object_id]["frames"]

            print(
                f"[{obj_index}/{total_objects}] "
                f"Object {object_id} "
                f"Frames={len(frames)}"
            )

            for frame_no, row in sorted(frames.items()):

                coords = np.asarray(
                    row.coordinates,
                    dtype=np.float32,
                )

                coords = coords[:, :, None, None]

                self.trk.settargetframe(
                    coords,
                    targets=[target],
                    fs=np.array([frame_no], dtype=np.int32),
                )

        print("\nFinished writing all coordinates.")


    def _write_all_track_data(self):
        print("\n" + "=" * 80)
        print("BUILDING TRACKLETS")
        print("=" * 80)

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
        total_objects = len(object_ids)

        for obj_index, object_id in enumerate(object_ids, start=1):
            if (obj_index == 1 or obj_index % 100 == 0 or obj_index == total_objects):
                print(f"[{obj_index}/{total_objects}] Object {object_id}")

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

        print("="*80)
        print("INSTALL TRACKLETS")
        print("="*80)
        print("=" * 80)
        print("CHECK TRACKLET DATA")
        print("=" * 80)

        print(type(self.coord_data))
        print(type(self.coord_data[0]))

        print(self.coord_data[0].dtype)
        print(self.coord_data[1].dtype)

        print(self.coord_data[-1])
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

        print("\n" + "=" * 80)
        print("WRITING METADATA")
        print("=" * 80)

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

        print("First Start :", startframes[:5])
        print("Last Start  :", startframes[-5:])

        print("First End   :", endframes[:5])
        print("Last End    :", endframes[-5:])


    def _finalize_metadata(self):

        print("\n" + "=" * 80)
        print("FINALIZING TRK")
        print("=" * 80)

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
        print("pTrkiTgt dtype :", self.trk.pTrkiTgt.dtype)

        print(f"Targets : {total_targets}")

        print("Final Target IDs")

        print(
            self.trk.pTrkiTgt[-20:]
            if total_targets >= 20
            else self.trk.pTrkiTgt
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

        print("\n" + "=" * 80)
        print("SAVING TRK")
        print("=" * 80)

        print("Output File")
        print(self.export_path)

        #
        # Defensive: strip any unsigned-integer dtypes before writing,
        # regardless of where they came from.
        #
        self._sanitize_unsigned_dtypes(self.trk)

        print("\n" + "=" * 80)
        print("VERIFY DTYPES BEFORE SAVE")
        print("=" * 80)

        print("startframes :", self.trk.startframes.dtype)
        print("endframes   :", self.trk.endframes.dtype)
        print("nframes     :", self.trk.nframes.dtype)
        print("pTrkiTgt    :", self.trk.pTrkiTgt.dtype)

        print()

        print("Last Startframes")
        print(self.trk.startframes[-10:])

        print("Last Endframes")
        print(self.trk.endframes[-10:])

        print("Last NFrames")
        print(self.trk.nframes[-10:])

        print("Last Target IDs")
        print(self.trk.pTrkiTgt[-10:])

        print()

        print("Coordinate Container :", type(self.trk.pTrk.data))
        print("Coordinate Element   :", type(self.trk.pTrk.data[0]))
        print("Coordinate Dtype     :", self.trk.pTrk.data[0].dtype)

        if self.trk.pTrkConf is not None:
            print("Confidence Dtype     :", self.trk.pTrkConf.data[0].dtype)

        if self.trk.pTrkTS is not None:
            print("Timestamp Dtype      :", self.trk.pTrkTS.data[0].dtype)

        if self.trk.pTrkTag is not None:
            print("Tag Dtype            :", self.trk.pTrkTag.data[0].dtype)

        self.trk.save(self.export_path)

        print("\nSAVE COMPLETE")


    def _verify_saved_trk(self):

        print("\n" + "=" * 80)
        print("VERIFY SAVED TRK")
        print("=" * 80)

        trk = Trk(self.export_path)

        print("Targets :", trk.ntargets)

        print("Target Array :", len(trk.pTrkiTgt))

        print("Last IDs")

        print(trk.pTrkiTgt[-20:])

        print()

        print("Startframes")

        print(trk.pTrk.startframes[-10:])

        print()

        print("Endframes")

        print(trk.pTrk.endframes[-10:])

        print("=" * 80)

    def _verify_metadata_integrity(self):

        print("=" * 80)
        print("VERIFY METADATA INTEGRITY")
        print("=" * 80)

        original = Trk(self.original_trk_path)
        exported = Trk(self.export_path)

        print("T0              :", original.T0 == exported.T0)
        print("T               :", original.T == exported.T)
        print("T1              :", original.T1 == exported.T1)

        print("nlandmarks      :", original.nlandmarks == exported.nlandmarks)
        print("dimensions      :", original.d == exported.d)

        print("issparse        :", original.issparse == exported.issparse)

        # print("movfile         :", original.movfile == exported.movfile)
        # print("trxfile         :", original.trxfile == exported.trxfile)

        # print("Skeleton        :", np.array_equal(original.skeleton, exported.skeleton))

        print("=" * 80)