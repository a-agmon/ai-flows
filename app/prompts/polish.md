Polish the following customer letter.

Keep it clear, professional, and concise. Preserve the meaning; improve flow and
fix any awkward phrasing. Match this tone: {{ tone }}.

Draft:
{{ draft_letter }}

{% set disclaimer = legal_disclaimer | default('') %}
{% if disclaimer %}
Append this legal disclaimer as a final paragraph, verbatim:
{{ disclaimer }}
{% endif %}

Return only the final letter text.
