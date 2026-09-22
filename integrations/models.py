from django.db import models


class CatalogSyncState(models.Model):
    """Where the last catalog sync got to. One row.

    The cursor is the `server_time` ops sent with a list response, stored and sent back
    verbatim (§4). It is never a time this machine worked out: the two boxes' clocks only have
    to disagree by a second for styles to fall through the gap and never sync.
    """

    SINGLETON_PK = 1

    cursor = models.CharField(
        max_length=40, blank=True,
        help_text="The server_time from the last fully successful sync. Blank = next run is a full sync.",
    )
    last_started_at = models.DateTimeField(null=True, blank=True)
    last_finished_at = models.DateTimeField(null=True, blank=True)
    last_full_sync_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, help_text="Why the last run stopped, or blank if it finished.")
    styles_seen = models.PositiveIntegerField(default=0, help_text="Styles in the last successful run.")

    class Meta:
        verbose_name = "catalog sync state"

    def __str__(self):
        return f"catalog sync @ {self.cursor or 'never'}"

    @classmethod
    def load(cls):
        return cls.objects.get_or_create(pk=cls.SINGLETON_PK)[0]

    def save(self, *args, **kwargs):
        self.pk = self.SINGLETON_PK
        super().save(*args, **kwargs)
