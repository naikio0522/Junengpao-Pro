# 飞书妙搭账号资料镜像：可行性与接入边界

状态：**方案文档，尚未联调、部署或同步任何真实账号**。巨能跑 Pro 的注册、密码校验、会话和会员状态仍由 [`cloud-service`](../cloud-service/README.md) 及其 MySQL 账号库负责；妙搭只适合作为受限的资料镜像与浏览器管理视图，不能因妙搭自带登录而替换现有桌面账号认证。现有公网账号 API 尚未完成生产部署，且手机号尚无短信验证，`phone_verified=false`，须先满足[云端账号上线验收](../cloud-service/DEPLOY_ALIYUN.md)。

## 最小同步设计

1. 在妙搭应用中建独立镜像表，以源库 `account_users.id`（UUID）映射为 `source_user_id`，为此列建**唯一约束**。默认字段白名单仅含 `source_user_id`、`registered_at`（源库 `created_at`）和 `synced_at`；确需展示验证状态时才增加 `phone_verified`，并如实标为“未验证”。默认**不传手机号**；如有明确业务必要，先完成告知/授权、访问范围、保留期评审。绝不传明文密码、`password_hash`、会话 token/摘要、支付信息或数据库凭据。
2. “一键同步”由桌面端向**自有 HTTPS 账号服务**发起；服务端先验证当前会话与操作权限，再从自己的 MySQL 读取当前用户资料。若扩展为全量/批量同步，必须另设管理员权限，不允许普通用户以客户端参数选择任意用户或表。同步异步执行，不作为登录成功的前置条件，也不以镜像数据判定会员资格。
3. 优先评估官方 Spark v1 `POST /open-apis/spark/v1/apps/:app_id/tables/:table_name/records`：`records` 是 JSON 数组字符串，单次最多 500 条；使用 `on_conflict=source_user_id` 及适当的 `columns` 执行 UPSERT，显式指定 `env=dev` 先验收，再考虑 `online`。有唯一约束时重复点击应更新同一源账号记录；**实际返回、部分失败和并发行为仍须在测试应用验证**，不可仅凭接口定义宣称已实现幂等。官方[SDK 接口声明](https://github.com/larksuite/oapi-sdk-go/blob/v3.12.0/service/spark/v1/resource.go#L2667-L2689)标注此接口只支持 `user_access_token`，不能把 `tenant_access_token`、App ID/Secret 或桌面内置密钥当成可用凭证；用户授权、应用权限和令牌续期须在服务端处理。[UPSERT 参数定义](https://github.com/larksuite/oapi-sdk-go/blob/v3.12.0/service/spark/v1/model.go#L9229-L9272)见官方 SDK。
4. 如果需要长期无人值守的服务端调用，可**另行开发并发布**妙搭应用自己的 `POST /openapi/account-sync` 路由，由该路由对镜像表做唯一键 UPSERT，并仅给这一 HTTP 方法和路径签发受限 API Key。此 Key 机制适用于应用自定义 `/openapi/**` 路由，**不是**直接调用上述 Spark 表记录接口的替代令牌；密钥只保存在服务端密钥管理中，不进入 Electron、前端、Git 或日志。创建/轮换时原值仅显示一次，疑似泄露先停用。参见飞书官方 [妙搭 OpenAPI Key 说明](https://github.com/larksuite/cli/blob/main/skills/lark-apps/references/lark-apps-openapi-key.md)。

## 权限、运行与撤销

妙搭应用所有者需核对 Spark 记录写入接口在本租户的具体授权项、用户对目标应用/表的访问资格；官方 CLI 的**部分应用写命令**要求 `spark:app:write`，但**记录接口所需权限以开放平台控制台的当前页面和实际授权结果为准**。浏览器管理页只给指定运营角色访问，配置应用可见范围及镜像表的行级读写策略；默认只读，禁止从妙搭反向改写账号主库。妙搭官方说明支持数据库管理、唯一约束、行级策略、发布应用的登录要求和 MySQL→妙搭同步，但未在此方案中验证内置 MySQL 同步的增量去重契约，因此不依赖其实现“一键幂等”。参见[飞书妙搭官方产品说明](https://www.feishu.cn/content/article/7597741503372512473)及[官方妙搭应用操作说明](https://github.com/larksuite/cli/blob/main/skills/lark-apps/SKILL.md)。

每次同步记录无敏感字段的 `job_id`、操作者、源 UUID、目标环境、记录数、结果与错误码；限制并发和批次，遇限流/暂时故障退避重试，失败批次可按同一唯一键重放并人工对账。设定镜像保留期与删除/停用策略：撤销授权时停止任务并撤销用户令牌或停用专用 Key；需删除镜像时按源 UUID 定向处理并留审计，不删除 MySQL 主数据。日志不得包含手机号、token、Key、密码或完整请求体。

## 联调前提与验收

需由负责人提供**信息而非在聊天中发送密钥**：妙搭租户/应用所有者、`app_id`、目标表结构与唯一约束、测试环境、经审批的可见范围和角色、选定的用户 OAuth 授权或自定义路由方案、数据处理/保留期决定，以及已部署可访问的自有 HTTPS 账号服务和受控测试账号。先在 `dev` 用虚构数据验证：同一 UUID 重放不增行、500 条以内分批、部分失败重试、权限拒绝、令牌/Key 失效、撤销与定向清理，再由负责人审批生产发布。**本仓库目前没有妙搭应用、令牌、API Key 或已运行的同步链路；本文不代表联调通过。**
