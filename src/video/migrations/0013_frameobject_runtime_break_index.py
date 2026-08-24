from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("video", "0012_project_trk_storage_path_project_video_storage_path"),
    ]

    operations = [
        migrations.RemoveIndex(
            model_name="frameobject",
            name="frame_objec_object__a33112_idx",
        ),
        migrations.AddIndex(
            model_name="frameobject",
            index=models.Index(
                fields=["object_id", "is_active", "frame"],
                name="fo_runtime_break_idx",
            ),
        ),
    ]
