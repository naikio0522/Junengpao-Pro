# 阿里云账号生产接口上线手册（尚未执行）

本手册只针对账号注册/登录。**当前没有已部署的公网账号 API；本机 RDS 公网只读连接也曾超时。**现有 `jnp-account-test` 是签名触发器加测试密钥的只读验收环境，保持原样。不要把 RDS 密码、阿里云 AccessKey、FC 测试密钥或支付密钥放入桌面安装包。云端手机号**没有短信验证**，`phone_verified=false`；不得在产品中称其为“已验证手机号”。会员支付仍关闭。

## 选择与上线前提

优先新建杭州地域、同 VPC 的独立 FC Web 函数 `jnp-account-prod`。已有测试函数经 VPC 内网 TLS 查询 RDS 成功，复用同类网络拓扑比新建 ECS 少一套系统、反向代理和服务器补丁运维，但**不复用测试触发器**。阿里云[Web 函数指引](https://help.aliyun.com/zh/functioncompute/creating-a-web-function)支持自定义运行时、ZIP 上传、启动命令和监听端口；[同 VPC 访问 RDS](https://help.aliyun.com/zh/functioncompute/access-the-rds-mysql-example)要求函数与数据库同地域/同 VPC、将 vSwitch 网段加入 RDS IP 白名单，且明确建议用 IP 白名单而不是 RDS 安全组授权。

上线前由账号所有者确认：

1. 已备案/接入阿里云的自有域名及其 DNS 管理权限、有效 HTTPS 证书；FC 官方说明生产固定域名应使用[自定义域名](https://help.aliyun.com/zh/functioncompute/configure-custom-domain-names)，不能把默认 `fcapp.run` 测试 URL 当正式对外域名。
2. 对新 FC 函数、域名、WAF（如果选用）和 RDS 备份/迁移有权限的阿里云操作身份。当前本机没有 Aliyun CLI 或可用的阿里云身份；**不要在聊天里发送 AccessKey/私钥/数据库密码**。可在阿里云控制台完成下述操作。
3. 接受新函数、域名、WAF 可选项和 RDS 可能产生的费用；现有 RDS 试用实例据本地交付记录将于 **2026-11-09 00:00（北京时间）** 到期，正式存放账号前须确认续费与备份计划。
4. 确认服务端实际取得的可信客户端 IP。代码当前**忽略不可信的 `X-Forwarded-For`**，只取 ASGI 连接对端。阿里云[原始 IP 文档](https://help.aliyun.com/zh/functioncompute/fc/how-to-get-the-original-ip-address-of-the-client-when-the-http-trigger-calls-the-built-in-runtime-function)警告转发头可被伪造；若 FC 传入统一网关 IP，所有用户会共用一个 IP 限额。未在受控预发环境验证此项前，**不得开放生产注册**。若需要解析转发头，先由网关明确覆盖/清洗头部，再实现、测试可信代理配置；不要直接读取来路请求的第一个 XFF。

## 准备代码包（不包含任何私密配置）

在 Linux x86_64、Python 3.10 环境运行。构建机的 Python/平台须与目标自定义运行时相符，避免跨平台二进制 wheel 失效；阿里云[Web 函数打包示例](https://help.aliyun.com/zh/functioncompute/web-function-quick-start)采用 ZIP 中同时放代码和依赖的方式。

```bash
cd cloud-service
python3.10 build_fc_zip.py \
  --ca-file /secure/ApsaraDB-CA-Chain.pem \
  --output dist/jnp-account-prod-v0.1.0.zip
```

脚本只复制 `junengpao_cloud/*.py`、Python 依赖与**公开 CA 证书**，拒绝含私钥标记的 PEM，拒绝覆盖已有 ZIP，并输出 SHA-256。ZIP 不包含 `.env`、数据库凭据或本机账户库。Python 3.10/Linux x86_64 以外的本机不能声称已完成 FC 包构建。部署前抽查 ZIP 文件列表与 SHA-256。

## 数据库准备（操作前备份）

1. 在 RDS 控制台对目标 `junengpao` 数据库创建手动备份，**等到状态“执行成功”、进度 100%**；阿里云有[手动备份与确认步骤](https://help.aliyun.com/zh/rds/apsaradb-rds-for-mysql/full-backup)。记录备份任务 ID/时间及恢复责任人。
2. 只读核对现有 `account_users`、`account_sessions` 的字段、索引和行数；不要执行 `000_accounts.sql` 覆盖现有表，不执行会员支付迁移 `001_membership_orders.sql`。
3. 在 DMS 的目标库执行 [`migrations/002_account_rate_limits.sql`](migrations/002_account_rate_limits.sql)，它只创建新的哈希限流表，不修改现有账号表；执行前审阅 SQL 与目标库，执行后只读核对 `account_rate_limits` 的表结构。阿里云[ DMS SQL 窗口](https://help.aliyun.com/zh/dms/overview-3)可用于受控执行。**本仓库没有执行此迁移。**

## 新建函数与入口（不改测试函数）

1. 新建 `jnp-account-prod` FC Web 函数，杭州地域，Python 3.10 自定义运行时，上传上一步 ZIP。启动命令：

   ```text
   python3 -m uvicorn junengpao_cloud.api:app --host 0.0.0.0 --port 9000 --no-proxy-headers
   ```

   监听端口设为 `9000`。不要让 Uvicorn 自动相信来路不明的代理头。FC 的[Web 函数机制](https://help.aliyun.com/zh/functioncompute/web-functions)要求 HTTP 服务监听配置的端口。
2. 与 RDS 使用相同 VPC，选可用的 vSwitch、安全组（不开放不必要入站）；仅将**实际使用的 vSwitch 网段**加进独立 RDS IP 白名单，不设 `0.0.0.0/0`。配置 RDS **内网域名**。不要改动 `jnp-account-test` 的触发器或白名单。
3. 在**函数运行环境**设置 `JNP_DB_HOST`、`JNP_DB_PORT`、`JNP_DB_NAME=junengpao`、`JNP_DB_USER`、`JNP_DB_PASSWORD`、`JNP_DB_CA_FILE=/code/certs/rds-ca.pem`、`JNP_ACCOUNT_ONLY_MODE=1`。密码仅存云端受控配置，限制能读取环境变量的 RAM 权限；代码会强制 CA、主机名与 TLS 1.2+。初次上线设 `JNP_PUBLIC_ACCOUNTS=0`，不能提前开放注册。
4. 在服务端生成不少于 32 字节的随机 `JNP_RATE_LIMIT_HMAC_KEY`，仅放函数环境；再设 `JNP_PUBLIC_ACCOUNTS=1` 后 `/ready` 才会同时检查限流表。不要把此密钥写入 Git、构建包或客户端。会员套餐/支付配置保持空。除 `health`/`ready` 等非敏感结果外，不记录请求体、密码、令牌、手机号或私密环境变量。API 已关闭自动 `/docs`、`/openapi.json`。
5. 预发阶段先用**签名 HTTP 触发器**验收新函数的 `/health` 和 `/ready`、数据库内网 TLS 与限流表；不要在桌面端加入任何 FC 签名材料。生产入口的注册/登录必须在没有预先持有密钥的情况下可用，因此可在安全检查通过后把**新函数**的触发器设为无需 FC 认证（应用层自行认证会话、持久限流），同时禁用默认公网 URL，只允许绑定的自定义域名访问。阿里云[HTTP 触发器设置](https://help.aliyun.com/zh/functioncompute/fc-3-0/user-guide/configure-an-http-trigger-for-a-function-and-invoke-the-function-by-using-http-requests)说明“无需认证”允许任何人调用，也说明禁用默认公网 URL 后自定义域名仍可访问；这是公开账号入口的风险边界，不能当作 FC 已经替应用提供登录鉴权。
6. 给新函数绑定已备案的同地域域名，路由 `/*`，配置证书与强制 HTTPS，并验证 HTTP 请求只能跳转 HTTPS、证书域名匹配、无明文账号请求。杭州地域 FC 自定义域名可选接入[阿里云 WAF](https://help.aliyun.com/zh/waf/web-application-firewall-3-0/add-a-custom-domain-name-in-function-compute-to-waf)；启用前先确认费用和可用规则，WAF 不能替代应用级限流。

## 上线验收门槛（全部通过才接桌面版）

- [ ] 新函数与测试函数完全分离；RDS 实例、库、账号、VPC/vSwitch、白名单目标逐项核对；手动备份已成功且恢复方案有人负责。
- [ ] `002` 新表结构验收；`/health` 为 200、`/ready` 为 200。故意移除/改错 CA 的预发检查应失败，不得降级明文连接。
- [ ] 通过**两种受控外网来源**验证应用取到不同的真实来源 IP。用无效手机号/有效格式密码发起受控注册测试：IP A 达到 20 次/小时后返回 429，IP B 同时仍应返回原有 400，而不是 429；若两端共享限额，先停，不要相信任意 XFF。此测试会占用预发限额，但不创建账号。
- [ ] 仅用运营人员本人控制的测试手机号执行真实注册 201、重复注册 409、正确密码登录 200、错密码 401、`/me` 200、退出后旧 token 401；数据库仅保留明确授权的测试账号。`phone_verified` 必须为 `false`。
- [ ] HTTPS 证书、DNS、强制跳转、默认公网 URL 关闭、应用 CORS 范围和 WAF/网关规则复核；验证码/短信验证未接入前，不将号码用于身份恢复或支付归属判断。
- [ ] 会员计划空、下单/支付回调 503；所有非会员导出仍有“俊小白”水印；日志不含密码、令牌、原始手机号和密钥。
- [ ] 观察 RDS 连接数、FC 冷启动/错误率、429 比例与账单；准备停用新生产函数或将 `JNP_PUBLIC_ACCOUNTS=0` 的回退动作。不可删除现有用户表或强行恢复旧备份覆盖新注册用户。

验收后只在桌面后端配置 `JNP_ACCOUNT_API_URL=https://你的已验收账号域名`，重新打包并从独立网络执行注册/登录冒烟测试。既有桌面安装包没有这个生产域名，不能因云端函数创建成功就声称客户端已接入。
