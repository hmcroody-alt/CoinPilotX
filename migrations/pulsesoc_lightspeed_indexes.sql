-- PulseSoc Lightspeed measured index additions.
-- PostgreSQL-compatible; runtime schema initialization uses equivalent statements.

CREATE INDEX IF NOT EXISTS idx_notification_delivery_jobs_user_created
    ON notification_delivery_jobs(user_id, created_at);

CREATE INDEX IF NOT EXISTS idx_admin_audit_logs_created
    ON admin_audit_logs(created_at);

CREATE INDEX IF NOT EXISTS idx_admin_audit_logs_admin_created
    ON admin_audit_logs(admin_user_id, created_at);
