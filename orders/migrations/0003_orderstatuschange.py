import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

STATUS_CHOICES = [
    ('pending', 'Pending payment'),
    ('paid', 'Paid'),
    ('sent_to_ops', 'Sent to production'),
    ('fulfilled', 'Fulfilled'),
    ('cancelled', 'Cancelled'),
    ('refunded', 'Refunded'),
]


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('orders', '0002_order_stripe_checkout_session'),
    ]

    operations = [
        migrations.CreateModel(
            name='OrderStatusChange',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('from_status', models.CharField(choices=STATUS_CHOICES, max_length=20)),
                ('to_status', models.CharField(choices=STATUS_CHOICES, max_length=20)),
                ('changed_at', models.DateTimeField(auto_now_add=True)),
                ('note', models.TextField(blank=True, help_text='Why the status changed. Required when cancelling an order.')),
                ('order', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='status_changes', to='orders.order')),
                ('changed_by', models.ForeignKey(blank=True, help_text='Staff member who made the change. Blank if the system did it.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='order_status_changes', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['changed_at'],
            },
        ),
    ]
