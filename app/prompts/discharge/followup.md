Write the "follow-up instructions" paragraph of a discharge letter for {{ discharge.patient_name }}.

Cover the medications to take at home and the follow-up appointments or actions
required. Be precise and easy to follow.

Medications:
{% for med in discharge.medications %}- {{ med }}
{% endfor %}
Follow-up:
{% for item in discharge.follow_up %}- {{ item }}
{% endfor %}

Return only the paragraph — no heading. Do not add medications or appointments
that are not listed above.
