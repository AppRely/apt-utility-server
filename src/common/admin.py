from unfold.admin import ModelAdmin as UnfoldModelAdmin


class BaseAdmin(UnfoldModelAdmin):
    """Custom admin to set created_by and updated_by."""

    readonly_fields = (
        "created_at",
        "updated_at",
    )
    actions = None

    def has_delete_permission(self, request, obj=None):
        # Disable delete permission for all users
        return False

    def get_queryset(self, request):
        # Use all_objects if the model defines it (for soft delete support)
        qs = super().get_queryset(request)
        if hasattr(self.model, "all_objects"):
            return self.model.all_objects.all()
        return qs

    def save_model(self, request, obj, form, change):
        if request.user.is_authenticated:
            if not obj.pk:
                obj.created_by = request.user
            obj.updated_by = request.user
        super().save_model(request, obj, form, change)
