Write the opening paragraph of a hospital discharge letter for {{ discharge.patient_name }}.

Summarise why the patient was admitted and the primary diagnosis. Keep it to a
single clear paragraph addressed to the patient.

Admission date: {{ discharge.admission.date }}
Reason for admission: {{ discharge.admission.reason }}
Primary diagnosis: {{ discharge.diagnosis }}

Return only the paragraph — no heading, no salutation.
