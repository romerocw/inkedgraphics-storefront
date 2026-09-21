import django.db.models.deletion
from django.db import migrations, models

import messaging.models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [
        migrations.CreateModel(
            name="OutboxEmail",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("kind", models.CharField(help_text="What sort of email this is, e.g. order_confirmation. For reporting.", max_length=50)),
                ("to_email", models.EmailField(help_text="Recipient address.", max_length=254)),
                ("to_name", models.CharField(blank=True, help_text="Recipient name, shown in the To: header if given.", max_length=200)),
                ("from_email", models.CharField(default=messaging.models.default_from_email, help_text="Sender, e.g. 'Inked Graphics Stores <orders@…>'.", max_length=254)),
                ("subject", models.CharField(max_length=255)),
                ("text_body", models.TextField(help_text="Plain-text version, rendered when the email was queued.")),
                ("html_body", models.TextField(blank=True, help_text="HTML version, rendered when the email was queued.")),
                ("status", models.CharField(choices=[("queued", "Queued"), ("sent", "Sent"), ("failed", "Failed")], db_index=True, default="queued", max_length=10)),
                ("attempts", models.PositiveSmallIntegerField(default=0, help_text="How many times sending has been tried.")),
                ("last_error", models.TextField(blank=True, help_text="What went wrong on the most recent failed attempt.")),
                ("next_attempt_at", models.DateTimeField(blank=True, help_text="When a failed email will be tried again. Blank once it's sent or we've given up.", null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("sent_at", models.DateTimeField(blank=True, null=True)),
                ("related_type", models.ForeignKey(blank=True, help_text="Type of the record this email is about, e.g. an order.", null=True, on_delete=django.db.models.deletion.SET_NULL, to="contenttypes.contenttype")),
                ("related_id", models.PositiveBigIntegerField(blank=True, help_text="ID of the record this email is about.", null=True)),
            ],
            options={
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(fields=["status", "next_attempt_at"], name="outbox_due"),
                    models.Index(fields=["related_type", "related_id"], name="outbox_related"),
                ],
            },
        ),
    ]
