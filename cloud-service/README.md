# 巨能跑 Pro 云端账号与会员服务（待商户接入）

这是一套**独立的服务端基础代码**，不是桌面应用的本地数据库替换，也不是已经上线的会员收款。桌面版目前仍使用本机 SQLite；账号服务部署并完成 HTTPS、数据库和注册登录验收后，才能将桌面端账号请求切到这里，支付联调是另外的步骤。数据库密码和支付私钥只允许保存在服务端，严禁放进 Electron 安装包、前端资源或 Git。

阿里云 FC 独立账号接口的生产部署步骤与验收门槛见 [DEPLOY_ALIYUN.md](DEPLOY_ALIYUN.md)。这些步骤**尚未在云端执行**。

## 当前完成范围

- 手机号 + 密码注册、登录、会话校验、退出。密码哈希与当前桌面本地测试库兼容（PBKDF2-HMAC-SHA256、每用户随机盐），会话只存 SHA-256 摘要。当前**没有短信验证**；`phone_verified` 保持 `false`。
- MySQL 账号、会话、会员、支付订单仓储；会员状态仅按服务端数据库中的到期时间判定。
- 对现有仅有 `account_users`、`account_sessions` 的阿里云 RDS，可在**服务端**设置 `JNP_ACCOUNT_ONLY_MODE=1`：注册/登录/会话只访问这两张表；会员统一视为未开通，套餐为空、支付订单和通知全部拒绝。该模式不执行迁移，也不授予会员。
- 服务端定价的会员套餐和支付宝/微信支付下单、通知接口契约。默认没有生产支付适配器，调用下单或通知接口返回 `503`，不会生成假付款或开通会员。
- 已验签通知的服务端事务：原订单渠道、商户号、金额、支付状态、交易号全部匹配后才延长会员；相同通知可重放但不会重复加时，交易号不可用于另一笔订单。

## 数据库与部署准备

1. 先解决云数据库稳定连接问题。`D:\视频裂变\cloud-sql\README.md` 记录现有阿里云 MySQL 账号库已建，但 2026-10-09 的只读复核仍出现 TCP 超时。这个服务未直接读取其私有凭据，也未执行云端迁移。现有实例试用期至 **2026-11-09**，正式写入账号前需安排续费和备份。
2. 在数据库做好备份后，核对现有 `account_users`、`account_sessions` 与 [`migrations/000_accounts.sql`](migrations/000_accounts.sql) 相容；新库才执行 `000_accounts.sql`。仅账号模式跳过 [`migrations/001_membership_orders.sql`](migrations/001_membership_orders.sql)；只有后续会员支付上线前才审阅并执行它。`CREATE TABLE IF NOT EXISTS` 不保证旧表结构正确，迁移需人工复核。不要执行 DROP 或覆盖现有表。
3. 在云服务器的私有环境配置数据库变量，参考 [`.env.example`](.env.example)。必须提供真实 CA 文件并验证 TLS 主机名。`JNP_DB_PASSWORD` 不得写入源码、聊天、日志或安装包。
4. ECS 等自管服务器可安装依赖并通过 HTTPS 反向代理运行 `uvicorn junengpao_cloud.api:app --host 127.0.0.1 --port 8000 --no-proxy-headers`；本项目当前优先选择独立 FC Web 函数，详见上方部署手册。先检查 `/health`，再检查 `/ready`（按模式核对所需表与数据库连接）。限制服务器入口、配置登录限流/监控、备份与日志脱敏。`/health` 仅表示进程活着，不代表数据库可用。

当前现有云库只有两张账号表时，先设置 `JNP_ACCOUNT_ONLY_MODE=1`，此时 `/ready` 只核对这两张表。部署必须有**供桌面客户端访问的 HTTPS 域名**；桌面后端只配置 `JNP_ACCOUNT_API_URL=https://账号服务域名`，不配置数据库密码、阿里云 AccessKey 或函数计算测试密钥。现有 `jnp-account-test` 是签名 HTTP 触发器并要求 `JNP_TEST_KEY`，只能用于受控验收，**不能直接作为桌面版公共账号入口**。在取得安全 HTTPS 入口、限流/防滥用方案并完成真实注册与登录验收前，桌面版保持本地测试库模式，不声称云端账号已上线。

公开注册/登录默认关闭（`JNP_PUBLIC_ACCOUNTS=0`）。准备上线时，先备份数据库并单独核对、执行 [`migrations/002_account_rate_limits.sql`](migrations/002_account_rate_limits.sql)；再在**云服务端**设置 `JNP_PUBLIC_ACCOUNTS=1` 及不少于 32 字节的随机 `JNP_RATE_LIMIT_HMAC_KEY`。`/ready` 此时也检查限流表。每次注册/登录按实际连接来源 IP 和规范化手机号分别计数，限流记录只保存带密钥摘要，不保存原始 IP/手机号；返回 429 时不泄漏哪个条件触发。不要信任客户端传来的 `X-Forwarded-For`；HTTPS 反向代理必须正确传递可信来源 IP，否则所有用户可能共用一个 IP 限额。该限流只是一层基础防护，上线还需网关/WAF 级防滥用策略。当前没有短信验证，`phone_verified` 始终为 `false`，界面不得声称手机号归属已核实，也不可将该字段用作找回密码或支付身份依据。

## 账号 API

路径与目前桌面版本地后端一致：

| 路径 | 功能 |
| --- | --- |
| `POST /api/account/register` | `{ "phone": "13800138000", "password": "..." }`；返回 token 与用户信息 |
| `POST /api/account/login` | 同上 |
| `GET /api/account/me` | `Authorization: Bearer <token>`；含 `is_member`、`member_until` |
| `POST /api/account/logout` | 使当前会话失效 |

未来桌面端必须通过这个 HTTPS API 认证，并使用服务器签发的身份与会员状态。不能让用户编辑前端 `is_member` 字段解除水印；正式版本还应让云端签发短时效、可验签的会员授权，或让受保护的渲染请求实时向云端核验。

## 会员支付对接契约

管理员在服务端配置 `JNP_MEMBERSHIP_PLANS_JSON`，例如 `[ { "code": "month", "title": "月会员", "amount_fen": 1900, "duration_days": 30 } ]`。客户端只能选择 `plan_code`，不能提交金额或会员天数。此金额只是格式示例，不是已确认的实际售价。

| 路径 | 功能 |
| --- | --- |
| `GET /api/membership/plans` | 读取服务端套餐 |
| `GET /api/membership/status` | Bearer token；返回 `{ "is_member": false, "member_until": null }` 等服务端状态 |
| `POST /api/membership/orders` | Bearer token + `{ "provider": "alipay" 或 "wechat", "plan_code": "month" }`，适配器接通后返回付款地址 |
| `GET /api/membership/orders/{order_id}` | 只允许订单所属账户查看支付状态；前端不得自行宣告成功 |
| `POST /api/payment/callback/{provider}` | 支付渠道的原始通知；必须经真实适配器验签/解密后才能结算 |

生产适配器需要实现 `PaymentGateway.create_order()` 与 `verify_notification()`，部署于服务器并通过 `create_app(gateways={...})` 注入。**目前没有这些适配器，也没有商户私钥、证书、应用号、商户号和回调服务协议，因此会员充值入口保持不可用。** 不要把“赞助我”的个人收款二维码用作会员支付。

支付宝通知须验证官方签名、应用号、收款商户、交易状态，并用服务端原订单核对金额和订单号；微信支付 API v3 须先验证平台签名，再解密 AES-256-GCM 通知，核对商户号、订单号、金额和成功状态。适配器应拒绝时间戳异常、签名错误、重复交易号与任何不完整通知。不可仅信任浏览器跳转、用户截图或前端请求。官方参考：[支付宝开放平台](https://open.alipay.com/support/supportCenter.htm)、[微信支付回调验签](https://pay.wechatpay.cn/doc/v3/merchant/4012587960)、[微信支付回调解密](https://pay.wechatpay.cn/doc/v3/merchant/4012791861)。

对接前需提供**路径或接口文档，不要发送密钥**：云端部署位置与 HTTPS 域名、现有商户服务代码/接口、支付宝应用及商户配置所在服务、微信支付商户配置所在服务、支付回调地址与协议、确认的会员套餐/价格，以及是否已有短信验证服务。

## 测试

先安装 `python -m pip install -r requirements-dev.txt`，再在 `cloud-service` 目录运行 `python -m unittest discover -s tests -v`。测试只使用内存伪仓储与伪验证适配器，不访问真实云数据库，也不发起任何真实支付。真正上线前还必须在受控测试库验证 MySQL 迁移、并发结算和商户沙箱通知。
