import math
from statistics import median

from ..models import FrameObject, Project


class TrajectoryClipSampleRepository:
    """Read active trajectory samples without exposing ORM details to the analyzer."""

    @staticmethod
    def fetch(*, project_id, object_id, start_frame, end_frame):
        return list(
            FrameObject.objects.filter(
                frame__project_id_id=project_id,
                object_id=object_id,
                is_active=True,
                frame__frame_no__gte=start_frame,
                frame__frame_no__lte=end_frame,
            )
            .order_by("frame__frame_no")
            .values_list("frame__frame_no", "coordinates")
        )

    @staticmethod
    def project_fps(*, project_id):
        return Project.objects.filter(project_id=project_id).values_list(
            "fps",
            flat=True,
        ).first()


class TrajectoryClipSuggestionService:
    """Suggest clip ranges containing movement that is unusual for one track."""

    DEFAULT_LIMIT = 5
    MIN_TRANSITIONS = 4
    ANOMALY_THRESHOLD = 3.5
    MIN_SCALE = 1.0
    MERGE_GAP = 2
    PADDING_SECONDS = 0.5
    FALLBACK_PADDING_FRAMES = 5

    def __init__(self, repository=None):
        self.repository = repository or TrajectoryClipSampleRepository

    def suggest(
        self,
        *,
        project_id,
        object_id,
        start_frame,
        end_frame,
        limit=DEFAULT_LIMIT,
    ):
        rows = self.repository.fetch(
            project_id=project_id,
            object_id=object_id,
            start_frame=start_frame,
            end_frame=end_frame,
        )
        samples = self._samples(rows)
        transitions = self._movements(samples)

        result = {
            "project_id": project_id,
            "object_id": object_id,
            "analyzed_range": {
                "start_frame": start_frame,
                "end_frame": end_frame,
            },
            "baseline_movement": None,
            "suggestions": [],
        }
        if len(transitions) < self.MIN_TRANSITIONS:
            return result

        padding_frames = self._padding_frames(
            self.repository.project_fps(project_id=project_id)
        )

        movement_values = [item[1] for item in transitions]
        baseline = median(movement_values)
        deviations = [abs(value - baseline) for value in movement_values]
        robust_scale = max(1.4826 * median(deviations), self.MIN_SCALE)
        result["baseline_movement"] = round(baseline, 6)

        anomalies = []
        for frame_no, movement in transitions:
            # Clip suggestions are intended for unusually large displacement,
            # not for periods where the object merely slows down or stops.
            severity = (movement - baseline) / robust_scale
            if severity >= self.ANOMALY_THRESHOLD:
                anomalies.append(
                    {
                        "frame": frame_no,
                        "movement": movement,
                        "severity": severity,
                        "reason": "movement_spike",
                    }
                )

        groups = self._group_anomalies(anomalies)
        suggestions = [
            self._build_suggestion(
                group=group,
                transitions=transitions,
                start_frame=start_frame,
                end_frame=end_frame,
                padding_frames=padding_frames,
            )
            for group in groups
        ]
        result["suggestions"] = self._select_diverse_suggestions(
            suggestions=suggestions,
            start_frame=start_frame,
            end_frame=end_frame,
            limit=limit,
        )
        return result

    @classmethod
    def _padding_frames(cls, fps):
        if cls._is_number(fps) and fps > 0:
            return max(math.ceil(fps * cls.PADDING_SECONDS), 1)
        return cls.FALLBACK_PADDING_FRAMES

    @staticmethod
    def _select_diverse_suggestions(
        *,
        suggestions,
        start_frame,
        end_frame,
        limit,
    ):
        """Prefer the strongest candidate from each part of the frame range.

        After range coverage is established, any unused result slots are filled
        with the highest-scoring remaining candidates. The final response stays
        score-ranked for a predictable frontend contract.
        """
        if not suggestions or limit <= 0:
            return []

        ranked = sorted(
            suggestions,
            key=lambda item: (-item["score"], item["start_frame"]),
        )
        frame_count = end_frame - start_frame + 1
        bucket_size = max(math.ceil(frame_count / limit), 1)
        best_by_bucket = {}
        for suggestion in ranked:
            bucket = min(
                (suggestion["peak_frame"] - start_frame) // bucket_size,
                limit - 1,
            )
            best_by_bucket.setdefault(bucket, suggestion)

        selected = list(best_by_bucket.values())
        selected_ids = {id(item) for item in selected}
        for suggestion in ranked:
            if len(selected) >= limit:
                break
            if id(suggestion) not in selected_ids:
                selected.append(suggestion)
                selected_ids.add(id(suggestion))

        selected.sort(
            key=lambda item: (-item["score"], item["start_frame"]),
        )
        return selected[:limit]

    @classmethod
    def _samples(cls, rows):
        samples = []
        seen_frames = set()
        for frame_no, coordinates in rows:
            if frame_no in seen_frames:
                continue
            point = cls._centroid(coordinates)
            if point is not None:
                samples.append((frame_no, point))
                seen_frames.add(frame_no)
        samples.sort(key=lambda item: item[0])
        return samples

    @staticmethod
    def _centroid(coordinates):
        if not isinstance(coordinates, list) or not coordinates:
            return None

        if len(coordinates) == 4 and all(
            TrajectoryClipSuggestionService._is_number(value)
            for value in coordinates
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
                and TrajectoryClipSuggestionService._is_number(point[0])
                and TrajectoryClipSuggestionService._is_number(point[1])
            )
        ]
        if not points:
            return None
        return (
            sum(point[0] for point in points) / len(points),
            sum(point[1] for point in points) / len(points),
        )

    @staticmethod
    def _is_number(value):
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
        )

    @staticmethod
    def _movements(samples):
        transitions = []
        for previous, current in zip(samples, samples[1:]):
            previous_frame, previous_point = previous
            current_frame, current_point = current
            if current_frame - previous_frame != 1:
                continue
            transitions.append(
                (
                    current_frame,
                    math.hypot(
                        current_point[0] - previous_point[0],
                        current_point[1] - previous_point[1],
                    ),
                )
            )
        return transitions

    @classmethod
    def _group_anomalies(cls, anomalies):
        groups = []
        for anomaly in anomalies:
            if (
                not groups
                or anomaly["frame"] - groups[-1][-1]["frame"]
                > cls.MERGE_GAP + 1
            ):
                groups.append([anomaly])
            else:
                groups[-1].append(anomaly)
        return groups

    @classmethod
    def _build_suggestion(
        cls,
        *,
        group,
        transitions,
        start_frame,
        end_frame,
        padding_frames,
    ):
        interval_start = max(start_frame, group[0]["frame"] - padding_frames)
        interval_end = min(end_frame, group[-1]["frame"] + padding_frames)
        peak = max(group, key=lambda item: item["severity"])
        raw_span = max(group[-1]["frame"] - group[0]["frame"] + 1, 1)
        density_score = len(group) / raw_span
        interval_transition_count = sum(
            interval_start <= frame_no <= interval_end
            for frame_no, _movement in transitions
        )
        expected_transition_count = max(interval_end - interval_start, 1)
        quality_score = min(
            interval_transition_count / expected_transition_count,
            1.0,
        )
        severity_score = min(peak["severity"] / 10.0, 1.0)
        score = (
            0.60 * severity_score
            + 0.25 * density_score
            + 0.15 * quality_score
        )
        return {
            "start_frame": interval_start,
            "end_frame": interval_end,
            "peak_frame": peak["frame"],
            "score": round(min(max(score, 0.0), 1.0), 6),
            "peak_movement": round(peak["movement"], 6),
            "reason": peak["reason"],
        }
