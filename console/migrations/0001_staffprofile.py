import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

ROLE_CHOICES = [('owner', 'Owner'), ('manager', 'Manager'), ('staff', 'Staff')]


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='StaffProfile',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('role', models.CharField(choices=ROLE_CHOICES, default='staff', help_text='Owners can manage everyone. Managers can manage staff. Staff run stores and orders.', max_length=20)),
                ('phone', models.CharField(blank=True, help_text='Where to reach this person about an order.', max_length=30)),
                ('job_title', models.CharField(blank=True, help_text="Shown on the team list, e.g. 'Production lead'.", max_length=100)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('invited_by', models.ForeignKey(blank=True, help_text='Who invited this person. Blank for the original accounts.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='invitations_sent', to=settings.AUTH_USER_MODEL)),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='staff_profile', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['user__first_name', 'user__last_name', 'user__username'],
            },
        ),
    ]
