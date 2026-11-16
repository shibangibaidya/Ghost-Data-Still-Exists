You transcribe one evidence file (an application screenshot or a PDF document) from an online shop's internal systems. Copy what is visible. Do not infer, guess or judge anything.

1. `document_kind`: a short description of the screen or document.

2. `visible_identifiers`: every identifier visible anywhere in the file, exactly as written. Include:
   - email addresses, phone numbers and person names;
   - user references such as `user_42`;
   - 64-character hexadecimal hashes;
   - record identifiers made of a lowercase prefix, an underscore and digits, such as `account_5762`, `search_45`, `support_9538`, `analytics_77`, `export_12`, `data_flow_9`, `backup_3`, `audit_record_88`, `tombstone_17`, `legal_hold_4`, `marketing_2`, `object_19`, `cache_5` and `email_delivery_6`;
   - business references such as ticket numbers, order numbers or statement numbers.

3. `records`: one entry per record identifier (lowercase prefix, underscore, digits) shown in the file. For each record:
   - `resource_id`: the identifier exactly as written.
   - `system`: the system or service name shown for it, or null.
   - `record_type`: a record category if the file states one (for example `USER_PROFILE` or `SUPPORT_ATTACHMENT`), else null.
   - `status`: the status, state or result word shown for it (for example FOUND, NOT_FOUND, ACTIVE, DELETED, DISABLED, BLOCKED, ANONYMIZED, REDACTED, ENABLED, Restore allowed), else null.
   - `observed_at`: the date-time shown for that record as ISO 8601 with timezone (assume UTC when none is shown), else null.
   - `fields`: every other attribute shown next to the record, as name/value pairs, copied verbatim. Examples: email, phone, name, user reference, customer reference, file name, order, enabled, restore allowed, retain until, connector, archive key, ticket. A visibly empty or redacted value is copied as shown (for example "—" or "[redacted]").

If an attribute clearly belongs to a record (same row, card or section), put it in that record's fields. Never invent records or values that are not visible.
