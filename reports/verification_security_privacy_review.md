# Verification Security Privacy Review

Security and privacy boundaries added:

- User routes require `require_account()`.
- Admin review routes require an active admin session and verification-capable role.
- Support readonly roles cannot approve, reject, revoke, suspend, or restore.
- Admins cannot approve or review their own verification request.
- Sensitive verification actions write `verification_audit_logs`.
- Private document upload is wired through `/api/dashboard/account/verification/document`.
- Verification evidence is stored under the Flask instance private upload path, not as public URLs.
- Admin document access uses `/admin/verification/document/<id>` and writes `verification_document_accessed` audit events.
- Normal user pages do not expose reviewer-only notes, raw storage IDs, provider secrets, or private documents.

Remaining production hardening: connect malware scanning/provider quarantine if a production scanner is added. Current validation enforces file type, signature, and 8 MB size limits.
