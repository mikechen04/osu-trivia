from django import forms


class SuggestionForm(forms.Form):
    text = forms.CharField(
        label="Your question",
        min_length=15,
        max_length=300,
        widget=forms.Textarea(attrs={"rows": 3, "maxlength": 300}),
        help_text="Open-ended, with lots of valid answers of different rarity. Like “Name a mod you can turn on in osu!standard.”",
    )
    examples = forms.CharField(
        label="Example answers (optional)",
        required=False,
        max_length=1000,
        widget=forms.Textarea(attrs={"rows": 4, "maxlength": 1000}),
        help_text="One per line, up to 10. Helps us check the question works.",
    )

    def clean_text(self):
        return " ".join(self.cleaned_data["text"].split())

    def clean_examples(self):
        lines = []
        for line in self.cleaned_data["examples"].splitlines():
            line = " ".join(line.split())
            if line and line.lower() not in {x.lower() for x in lines}:
                lines.append(line)
        if len(lines) > 10:
            raise forms.ValidationError("Please give at most 10 example answers.")
        if any(len(line) > 80 for line in lines):
            raise forms.ValidationError("Each example answer must be 80 characters or fewer.")
        return "\n".join(lines)
