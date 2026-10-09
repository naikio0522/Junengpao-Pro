-- Run once on the existing junengpao MySQL 8 database after taking a backup.
-- Existing account_users/account_sessions are defined in 000_accounts.sql;
-- this migration does not create or alter those existing tables.

CREATE TABLE IF NOT EXISTS account_memberships (
    user_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    valid_until_epoch BIGINT UNSIGNED NOT NULL,
    updated_at_epoch BIGINT UNSIGNED NOT NULL,
    PRIMARY KEY (user_id),
    CONSTRAINT fk_member_user FOREIGN KEY (user_id) REFERENCES account_users(id)
        ON DELETE CASCADE ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE TABLE IF NOT EXISTS payment_orders (
    id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    user_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    provider VARCHAR(10) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    plan_code VARCHAR(40) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    amount_fen BIGINT UNSIGNED NOT NULL,
    duration_days INT UNSIGNED NOT NULL,
    merchant_id VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    status VARCHAR(12) CHARACTER SET ascii COLLATE ascii_bin NOT NULL DEFAULT 'pending',
    provider_trade_no VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
    created_at_epoch BIGINT UNSIGNED NOT NULL,
    paid_at_epoch BIGINT UNSIGNED NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_provider_trade (provider, provider_trade_no),
    KEY idx_order_user (user_id, created_at_epoch),
    CONSTRAINT fk_order_user FOREIGN KEY (user_id) REFERENCES account_users(id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT chk_order_provider CHECK (provider IN ('alipay', 'wechat')),
    CONSTRAINT chk_order_status CHECK (status IN ('pending', 'paid', 'closed')),
    CONSTRAINT chk_order_amount CHECK (amount_fen > 0),
    CONSTRAINT chk_order_days CHECK (duration_days BETWEEN 1 AND 3660),
    CONSTRAINT chk_paid_has_trade CHECK (
        status <> 'paid' OR (provider_trade_no IS NOT NULL AND paid_at_epoch IS NOT NULL)
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
