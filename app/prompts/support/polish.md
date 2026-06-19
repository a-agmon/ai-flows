Polish this customer support reply. Keep the meaning; improve clarity, warmth and
concision. Match this tone: {{ tone }}.

Draft:
{{ draft }}

{% set note = disclaimer | default('') %}
{% if note %}
Append this disclaimer as a final, separate line, verbatim:
{{ note }}
{% endif %}

Return only the final reply text.
