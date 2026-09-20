from django import forms

from .models import Order


class CheckoutForm(forms.ModelForm):
    class Meta:
        model = Order
        fields = ["buyer_name", "buyer_email", "buyer_phone", "recipient_name", "notes"]
        labels = {"recipient_name": "Player / student name (if different)"}
        widgets = {"notes": forms.Textarea(attrs={"rows": 3})}
