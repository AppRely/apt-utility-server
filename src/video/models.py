from django.db import models

# Create your models here.
# from src.common.models import BaseModel

# models.py
class Video(models.Model):
    id = models.AutoField(primary_key=True)
    title = models.CharField(max_length=255, blank=True, null=True)
    file = models.FileField(upload_to="videos/")
    description = models.TextField(blank=True, null=True)  # Add this
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title or f"Video {self.id}"