# 本地账户测试库

开发测试模式的手机号注册/登录使用本机 SQLite，**尚未连接公司数据库，也没有短信验证**。手机号只作格式校验，不能视为已证明归属；账户目前不限制剪辑功能。桌面正式安装包默认要求云端模式：未配置 HTTPS 账号服务时，界面显示“云端账户服务尚未接通”，注册与登录返回 503，绝不写入本机测试库。

- 默认位置：Windows `%LOCALAPPDATA%\巨能跑pro版\accounts.sqlite3`；macOS/Linux `~/.local/share/junengpao-pro/accounts.sqlite3`（可由 `XDG_DATA_HOME` 改变）。
- 测试时可用 `VIDEOMATRIX_ACCOUNT_DB` 指定独立数据库文件路径，避免写入真实用户目录。
- 密码使用独立随机盐的 PBKDF2-HMAC-SHA256（600,000 次）哈希。数据库只保存会话令牌的 SHA-256 摘要，不保存明文密码或令牌。
- `POST /api/account/register`、`POST /api/account/login` 均接收 `{ "phone": "13800138000", "password": "..." }`，返回 `{ "token": "...", "user": {"id":"...", "phone":"...", "created_at":"...", "phone_verified":false} }`。
- `GET /api/account/me` 和 `POST /api/account/logout` 使用 `Authorization: Bearer <token>`。会话有效期 30 天，退出后立即撤销。

独立云端服务部署完成后，发布构建可设置 `JNP_DESKTOP_ACCOUNT_API_URL=https://已验收账号域名`，将公开的 HTTPS 根地址写入桌面主进程；GitHub Actions 的 Windows 和 macOS 构建从同名仓库变量读取该地址。桌面后端通过该地址代理注册、登录和会话查询。临时验收也可在启动桌面软件前设置运行时 `JNP_ACCOUNT_API_URL`。桌面正式安装包始终以 `JNP_ACCOUNT_MODE=cloud` 启动后端，云端连接失败不会回退到本机库。开发测试可用 `JNP_ACCOUNT_MODE=local_test` 显式启用本机库。数据库密码、阿里云密钥不得写进源码、安装包或聊天记录。正式部署前还需手机号验证、密码找回、服务端限流、访问控制、HTTPS 和数据库备份/权限策略；当前本机库只供开发测试。
