import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models

STATUS_CHOICES = [("draft", "Draft"), ("scheduled", "Scheduled"), ("open", "Open"), ("closed", "Closed")]


class Migration(migrations.Migration):

    dependencies = [
        ("stores", "0005_store_time_zone"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="StoreStatusChange",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("from_status", models.CharField(blank=True, choices=STATUS_CHOICES, help_text="Status before the change. Blank when the store was just created.", max_length=20)),
                ("to_status", models.CharField(choices=STATUS_CHOICES, max_length=20)),
                ("changed_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("reason", models.CharField(choices=[("scheduled", "On schedule"), ("manual", "Changed by staff")], help_text="Scheduled: opened/closed by lifecycle_tick. Manual: set in the store form.", max_length=20)),
                ("changed_by", models.ForeignKey(blank=True, help_text="Staff member who made the change. Blank if the schedule did it.", null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="store_status_changes", to=settings.AUTH_USER_MODEL)),
                ("store", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="status_changes", to="stores.store")),
            ],
            options={
                "ordering": ["changed_at", "pk"],
            },
        ),
    ]
