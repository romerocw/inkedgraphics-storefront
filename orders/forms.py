from django import forms

from .models import Order


class CheckoutForm(forms.ModelForm):
    class Meta:
        model = Order
        fields = ["buyer_name", "buyer_email", "buyer_phone", "recipient_name", "notes"]
        labels = {"recipient_name": "Player / student name (if different)"}
        widgets = {"notes": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, store=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Group stores name a recipient on every line in the cart, so one order-wide name
        # would only contradict them.
        if store is not None and store.is_group:
            del self.fields["recipient_name"]
