-- Fresh-install account schema, matching the already initialized MySQL cloud DB.
-- On the existing junengpao database verify the table definitions first;
-- do not blindly replace or drop existing account data.

CREATE TABLE IF NOT EXISTS account_users (
    id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    phone VARCHAR(11) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    password_hash VARCHAR(255) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    phone_verified TINYINT UNSIGNED NOT NULL DEFAULT 0,
    created_at VARCHAR(40) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_account_users_phone (phone),
    CONSTRAINT chk_account_users_phone_verified CHECK (phone_verified IN (0, 1))
) ENGINE=InnoDB DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE TABLE IF NOT EXISTS account_sessions (
    token_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    user_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    created_at BIGINT UNSIGNED NOT NULL,
    expires_at BIGINT UNSIGNED NOT NULL,
    PRIMARY KEY (token_hash),
    KEY idx_account_sessions_user (user_id),
    KEY idx_account_sessions_expiry (expires_at),
    CONSTRAINT fk_account_sessions_user FOREIGN KEY (user_id) REFERENCES account_users(id)
        ON DELETE CASCADE ON UPDATE RESTRICT,
    CONSTRAINT chk_account_sessions_expiry CHECK (expires_at > created_at)
) ENGINE=InnoDB DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
