-- New table for public account API. Back up the target database and verify
-- account_users/account_sessions before applying. No existing table is altered.
-- bucket_hash is an HMAC-SHA256 digest; no plain IP or phone is stored.
CREATE TABLE IF NOT EXISTS account_rate_limits (
    bucket_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    window_started_epoch BIGINT UNSIGNED NOT NULL,
    attempts INT UNSIGNED NOT NULL,
    PRIMARY KEY (bucket_hash),
    KEY idx_account_rate_limits_window (window_started_epoch)
) ENGINE=InnoDB DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
