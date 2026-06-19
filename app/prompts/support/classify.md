You are triaging an inbound customer support message.

Return ONLY JSON of this exact shape:
{
  "category": "billing" | "technical" | "account" | "other",
  "urgency": "low" | "normal" | "high",
  "can_handle": true | false,
  "rejection_reason": string | null
}

Set "can_handle" to false (with a short rejection_reason) only if the message is
abusive, out of scope for support, or requires a human (legal, security incident).

Customer message:
{{ customer_message }}
