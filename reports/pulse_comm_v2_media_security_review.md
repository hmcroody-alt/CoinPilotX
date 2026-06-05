Pulse Communications V2 Media Security Review

Security hooks added:
- Upload requires authenticated Communications V2 access.
- Upload checks conversation membership when a conversation id is provided.
- Message creation only links staged media IDs owned by the sender.
- Executable file extensions are blocked.
- File size limits are enforced server side.
- Attachment count is enforced server side.
- Voice uploads validate duration, MIME, extension, and size.
- Response payloads use existing safe media URLs and do not expose raw private storage secrets.

Future hooks reserved:
- Virus scanning hook.
- Media moderation scanning hook.
- True resumable uploads for very large files.
