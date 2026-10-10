from django import forms

from portfolio.models import Portfolio


class PortfolioForm(forms.ModelForm):
    class Meta:
        model = Portfolio
        fields = [
            "name",
            "description",
            "initial_cash",
            "current_cash",
            "snaptrade_user_id",
            "snaptrade_account_id",
            "snaptrade_user_secret",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "description": forms.Textarea(
                attrs={"class": "form-control", "rows": 3}
            ),
            "initial_cash": forms.NumberInput(
                attrs={"class": "form-control", "step": "0.01"}
            ),
            "current_cash": forms.NumberInput(
                attrs={"class": "form-control", "step": "0.01"}
            ),
            "snaptrade_user_id": forms.TextInput(attrs={"class": "form-control"}),
            "snaptrade_account_id": forms.TextInput(attrs={"class": "form-control"}),
            "snaptrade_user_secret": forms.TextInput(attrs={"class": "form-control"}),
        }

    def clean(self):
        cleaned_data = super().clean()
        initial_cash = cleaned_data.get("initial_cash")
        current_cash = cleaned_data.get("current_cash")
        if current_cash is None and initial_cash is not None:
            cleaned_data["current_cash"] = initial_cash
        return cleaned_data
