import math
from abc import ABC, abstractmethod
from dataclasses import dataclass

from django.db.models import Q

from ..models import FrameObject, ObjectTrack


Point = tuple[float, float]
Sample = tuple[int, Point]


class CoordinateCentroid:
    """Convert supported bounding-box/keypoint coordinates to one point."""

    @staticmethod
    def extract(coordinates) -> Point | None:
        if not isinstance(coordinates, list) or not coordinates:
            return None

        if (
            len(coordinates) == 4
            and all(isinstance(value, (int, float)) for value in coordinates)
        ):
            return (
                (coordinates[0] + coordinates[2]) / 2.0,
                (coordinates[1] + coordinates[3]) / 2.0,
            )

        points = [
            point
            for point in coordinates
            if (
                isinstance(point, list)
                and len(point) >= 2
                and isinstance(point[0], (int, float))
                and isinstance(point[1], (int, float))
            )
        ]
        if not points:
            return None

        return (
            sum(point[0] for point in points) / len(points),
            sum(point[1] for point in points) / len(points),
        )


class TrajectorySampleRepository:
    SAMPLE_SIZE = 3

    @classmethod
    def source_samples(cls, *, project_id, object_id, break_start, break_end):
        base_query = FrameObject.objects.filter(
            frame__project_id_id=project_id,
            object_id=object_id,
            is_active=True,
        )
        before_rows = list(
            base_query.filter(frame__frame_no__lt=break_start)
            .order_by("-frame__frame_no")
            .values_list("frame__frame_no", "coordinates")[: cls.SAMPLE_SIZE]
        )
        after_rows = list(
            base_query.filter(frame__frame_no__gt=break_end)
            .order_by("frame__frame_no")
            .values_list("frame__frame_no", "coordinates")[: cls.SAMPLE_SIZE]
        )
        return cls._to_samples(reversed(before_rows)), cls._to_samples(after_rows)

    @classmethod
    def candidates(
        cls,
        *,
        project_id,
        source_object_id,
        target_frame,
        end_frame,
    ):
        active_track_ids = ObjectTrack.objects.filter(
            project_id_id=project_id,
            object_status=1,
        ).values("object_id")
        candidate_ids = list(
            FrameObject.objects.filter(
                frame__project_id_id=project_id,
                frame__frame_no=target_frame,
                is_active=True,
                object_id__in=active_track_ids,
            )
            .exclude(object_id=source_object_id)
            .order_by("object_id")
            .values_list("object_id", flat=True)
            .distinct()
        )
        if not candidate_ids:
            return {}

        rows = (
            FrameObject.objects.filter(
                Q(
                    frame__frame_no__gte=target_frame,
                    frame__frame_no__lte=target_frame + cls.SAMPLE_SIZE - 1,
                )
                | Q(
                    frame__frame_no__gte=end_frame - cls.SAMPLE_SIZE + 1,
                    frame__frame_no__lte=end_frame,
                ),
                frame__project_id_id=project_id,
                object_id__in=candidate_ids,
                is_active=True,
            )
            .order_by("object_id", "frame__frame_no")
            .values_list("object_id", "frame__frame_no", "coordinates")
        )
        samples_by_object = {candidate_id: [] for candidate_id in candidate_ids}
        seen_frames = {candidate_id: set() for candidate_id in candidate_ids}
        for candidate_id, frame_no, coordinates in rows:
            point = CoordinateCentroid.extract(coordinates)
            if point is not None and frame_no not in seen_frames[candidate_id]:
                samples_by_object[candidate_id].append((frame_no, point))
                seen_frames[candidate_id].add(frame_no)
        return samples_by_object

    @staticmethod
    def _to_samples(rows) -> list[Sample]:
        samples = []
        seen_frames = set()
        for frame_no, coordinates in rows:
            point = CoordinateCentroid.extract(coordinates)
            if point is not None and frame_no not in seen_frames:
                samples.append((frame_no, point))
                seen_frames.add(frame_no)
        return samples


def _distance(first: Point, second: Point) -> float:
    return math.hypot(first[0] - second[0], first[1] - second[1])


def _velocity(samples: list[Sample]) -> Point | None:
    if len(samples) < 2:
        return None
    start_frame, start_point = samples[-2]
    end_frame, end_point = samples[-1]
    frame_delta = end_frame - start_frame
    if frame_delta <= 0:
        return None
    return (
        (end_point[0] - start_point[0]) / frame_delta,
        (end_point[1] - start_point[1]) / frame_delta,
    )


@dataclass(frozen=True)
class MatchingContext:
    source_before: list[Sample]
    source_after: list[Sample]
    candidate: list[Sample]
    target_frame: int


class MatchingMetric(ABC):
    weight = 1.0

    @abstractmethod
    def score(self, context: MatchingContext) -> float:
        """Return a normalized score between zero and one."""


class PredictedDistanceMetric(MatchingMetric):
    weight = 0.45
    DISTANCE_SCALE = 100.0

    def score(self, context):
        if not context.source_before or not context.candidate:
            return 0.0
        source_frame, source_point = context.source_before[-1]
        velocity = _velocity(context.source_before) or (0.0, 0.0)
        elapsed = context.target_frame - source_frame
        predicted = (
            source_point[0] + velocity[0] * elapsed,
            source_point[1] + velocity[1] * elapsed,
        )
        return 1.0 / (
            1.0 + _distance(predicted, context.candidate[0][1]) / self.DISTANCE_SCALE
        )


class DirectionMetric(MatchingMetric):
    weight = 0.20

    def score(self, context):
        source_velocity = _velocity(context.source_before)
        if source_velocity is None or not context.candidate or not context.source_before:
            return 0.5
        displacement = (
            context.candidate[0][1][0] - context.source_before[-1][1][0],
            context.candidate[0][1][1] - context.source_before[-1][1][1],
        )
        source_length = math.hypot(*source_velocity)
        displacement_length = math.hypot(*displacement)
        if source_length == 0 or displacement_length == 0:
            return 0.5
        cosine = (
            source_velocity[0] * displacement[0]
            + source_velocity[1] * displacement[1]
        ) / (source_length * displacement_length)
        return (max(-1.0, min(1.0, cosine)) + 1.0) / 2.0


class MovementConsistencyMetric(MatchingMetric):
    weight = 0.15

    def score(self, context):
        source_velocity = _velocity(context.source_before)
        candidate_velocity = _velocity(context.candidate)
        if source_velocity is None or candidate_velocity is None:
            return 0.5
        difference = math.hypot(
            source_velocity[0] - candidate_velocity[0],
            source_velocity[1] - candidate_velocity[1],
        )
        return 1.0 / (1.0 + difference)


class PostBreakProximityMetric(MatchingMetric):
    weight = 0.15
    DISTANCE_SCALE = 100.0

    def score(self, context):
        if not context.source_after or not context.candidate:
            return 0.5
        candidate_frame, candidate_point = context.candidate[-1]
        source_after_frame, source_after_point = context.source_after[0]
        candidate_velocity = _velocity(context.candidate) or (0.0, 0.0)
        elapsed = max(source_after_frame - candidate_frame, 0)
        predicted = (
            candidate_point[0] + candidate_velocity[0] * elapsed,
            candidate_point[1] + candidate_velocity[1] * elapsed,
        )
        return 1.0 / (
            1.0
            + _distance(source_after_point, predicted) / self.DISTANCE_SCALE
        )


class ContinuityMetric(MatchingMetric):
    weight = 0.05

    def score(self, context):
        if not context.candidate:
            return 0.0
        if not context.source_after:
            return 0.5
        reaches_break_end = context.candidate[-1][0] >= context.source_after[0][0]
        return 1.0 if reaches_break_end else 0.5


class WeightedMatchingScore:
    def __init__(self, metrics=None):
        self.metrics = metrics or (
            PredictedDistanceMetric(),
            DirectionMetric(),
            MovementConsistencyMetric(),
            PostBreakProximityMetric(),
            ContinuityMetric(),
        )

    def calculate(self, context):
        total_weight = sum(metric.weight for metric in self.metrics)
        return sum(
            metric.weight * metric.score(context) for metric in self.metrics
        ) / total_weight


class TrajectoryMatchingService:
    DEFAULT_LIMIT = 5

    def __init__(self, scorer=None, repository=None):
        self.scorer = scorer or WeightedMatchingScore()
        self.repository = repository or TrajectorySampleRepository

    def suggest(self, *, project_id, object_id, break_start, break_end, limit=None):
        limit = limit or self.DEFAULT_LIMIT
        target_frame = break_start
        source_before, source_after = self.repository.source_samples(
            project_id=project_id,
            object_id=object_id,
            break_start=break_start,
            break_end=break_end,
        )

        suggestions = []
        candidates = self.repository.candidates(
            project_id=project_id,
            source_object_id=object_id,
            target_frame=target_frame,
            end_frame=break_end + 1,
        )
        for candidate_id, candidate in candidates.items():
            if not candidate:
                continue
            score = self.scorer.calculate(
                MatchingContext(
                    source_before=source_before,
                    source_after=source_after,
                    candidate=candidate,
                    target_frame=target_frame,
                )
            )
            suggestions.append({"object_id": candidate_id, "score": round(score, 6)})

        suggestions.sort(key=lambda item: (-item["score"], item["object_id"]))
        return {
            "object_id": object_id,
            "break_start": break_start,
            "break_end": break_end,
            "suggestions": suggestions[:limit],
        }
