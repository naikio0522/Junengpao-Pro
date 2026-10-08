# 本地账户测试库

v0.1.2 的手机号注册/登录先使用本机 SQLite，**尚未连接公司数据库，也没有短信验证**。手机号只作格式校验，不能视为已证明归属；账户目前不限制剪辑功能。

- 默认位置：Windows `%LOCALAPPDATA%\巨能跑pro版\accounts.sqlite3`；macOS/Linux `~/.local/share/junengpao-pro/accounts.sqlite3`（可由 `XDG_DATA_HOME` 改变）。
- 测试时可用 `VIDEOMATRIX_ACCOUNT_DB` 指定独立数据库文件路径，避免写入真实用户目录。
- 密码使用独立随机盐的 PBKDF2-HMAC-SHA256（600,000 次）哈希。数据库只保存会话令牌的 SHA-256 摘要，不保存明文密码或令牌。
- `POST /api/account/register`、`POST /api/account/login` 均接收 `{ "phone": "13800138000", "password": "..." }`，返回 `{ "token": "...", "user": {"id":"...", "phone":"...", "created_at":"...", "phone_verified":false} }`。
- `GET /api/account/me` 和 `POST /api/account/logout` 使用 `Authorization: Bearer <token>`。会话有效期 30 天，退出后立即撤销。

以后接入公司数据库时，实现 `app.services.account_service.AccountRepository` 接口，再在 `app.api.accounts.get_account_service` 注入新仓储。不要把数据库密码写进源码、安装包或聊天记录。正式部署前还需手机号验证、密码找回、服务端限流、访问控制、HTTPS 和数据库备份/权限策略；当前实现只供本机流程测试。
