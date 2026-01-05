from django.db.models.query import QuerySet


class SnapshotBuilder:
    """
    READ-ONLY snapshot builder.
    Handles models with custom primary keys.
    """

    @staticmethod
    def capture(data):
        if data is None:
            return []

        if isinstance(data, QuerySet):
            return list(data.values())

        if isinstance(data, list):
            return data

        raise TypeError(
            f"SnapshotBuilder.capture expects QuerySet or list, got {type(data)}"
        )

    @staticmethod
    def _get_pk(row: dict):
        """
        Dynamically find primary key field.
        """
        for key in row.keys():
            if key.endswith("_id") or key == "id":
                return key
        raise KeyError("No primary key found in snapshot row")

    @staticmethod
    def build(before_qs_map: dict, after_qs_map: dict):
        snapshot = {}

        model_names = set(before_qs_map.keys()) | set(after_qs_map.keys())

        for model_name in model_names:
            before_rows = SnapshotBuilder.capture(
                before_qs_map.get(model_name)
            )
            after_rows = SnapshotBuilder.capture(
                after_qs_map.get(model_name)
            )

            if before_rows:
                pk_field = SnapshotBuilder._get_pk(before_rows[0])
            elif after_rows:
                pk_field = SnapshotBuilder._get_pk(after_rows[0])
            else:
                snapshot[model_name] = {
                    "deleted": [],
                    "created": [],
                    "updated": [],
                }
                continue

            before_ids = {r[pk_field] for r in before_rows}
            after_ids = {r[pk_field] for r in after_rows}

            snapshot[model_name] = {
                "deleted": [r for r in before_rows if r[pk_field] not in after_ids],
                "created": [r for r in after_rows if r[pk_field] not in before_ids],
                "updated": [r for r in after_rows if r[pk_field] in before_ids],
            }

        return snapshot
