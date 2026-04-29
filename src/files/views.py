from rest_framework import mixins, viewsets
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated

from .models import File
from .serializers import FileSerializer


class FilesViewset(mixins.CreateModelMixin, viewsets.GenericViewSet):
    # MultiPartParser AND FormParser
    # https://www.django-rest-framework.org/api-guide/parsers/#multipartparser
    # "You will typically want to use both FormParser and MultiPartParser
    # together in order to fully support HTML form data."
    parser_classes = (MultiPartParser, FormParser)
    queryset = File.objects.all()
    serializer_class = FileSerializer
    permissions = {"default": (IsAuthenticated,)}

    def create(self, request, *args, **kwargs):
        """
        Create a MyModel
        ---
        parameters:
            - name: file
              description: file
              required: True
              type: file
        responseMessages:
            - code: 201
              message: Created
        """
        return super().create(request, *args, **kwargs)
