# 巨能跑 Pro 会员充值：支付宝与微信支付开通指引

状态：接入准备文档，更新于 2026-10-09。本文没有代替商户申请、产品签约、平台审核或真实支付联调；产品权限和所需材料以申请时的官方页面为准。

## 先确定收款场景和主体

本软件是 Windows 桌面程序，现有“会员充值”会在系统浏览器打开 **HTTPS 付款页**。拟接入的是商户订单支付：用户购买有期限的会员权益，云端服务按订单确认后开通会员。申请前应准备能实际访问的 PC 网站/收银台，清楚展示软件、会员权益、价格、有效期、客服与退款方式，并核对网站域名和备案资料。若目前只有安装包、没有可供平台审核的网站与经营场景，先向两家平台确认可申请的产品，不要把 Windows 程序直接当作 iOS/Android APP 支付场景申报。[支付宝网页/移动应用接入流程](https://open.alipay.com/module/webApp)、[微信支付 PC 网站接入指引](https://pay.wechatpay.cn/static/applyment_guide/applyment_detail_website.shtml)、[微信 Native 支付权限申请指引](https://pay.wechatpay.cn/doc/v3/merchant/4012791875)。

由实际经营和收款的主体开通商户账户。企业通常需准备营业执照、法定代表人身份证明、对公结算账户；个体工商户需准备营业执照、经营者身份证明及平台接受的结算账户。网站、经营内容、收款主体与申请资料应一致；微信 PC 网站场景的官方指引还要求填写域名，备案主体不同时可能需要网站授权函。支付宝的具体准入与材料以其[电脑网站支付产品说明](https://opendocs.alipay.com/open/270/105898)和当前申请页为准。[微信支付 PC 网站申请材料](https://pay.wechatpay.cn/static/applyment_guide/applyment_detail_website.shtml)、[微信 Native 权限申请细则](https://pay.wechatpay.cn/doc/v3/merchant/4012791875)。

| 渠道 | 本软件拟采用的产品与页面 | 申请前核对 |
| --- | --- | --- |
| 支付宝 | **电脑网站支付**：在商户 PC 网站/HTTPS 收银台创建会员订单，跳转支付宝收银台；桌面程序只打开该 HTTPS 页面。 | 商家账户签约产品，创建网页应用、配置密钥并提交应用审核；核对网站与线上服务是否符合当前准入。[产品说明](https://opendocs.alipay.com/open/270/105898)、[快速接入](https://opendocs.alipay.com/open/270/105899)。 |
| 微信支付 | **Native 支付**：商户服务逐单下单取得 `code_url`，由自有 HTTPS 收银台生成并展示动态二维码，用户用微信扫码。 | 申请 PC 网站经营场景和 Native 权限；准备已认证且可绑定的 APPID，并把它与商户号绑定。`code_url` 是二维码数据，不是本软件可直接打开的 HTTPS `pay_url`。[产品介绍](https://pay.wechatpay.cn/guide/qrcode_payment.shtml)、[开发接入准备](https://pay.wechatpay.cn/doc/v3/merchant/4015614538)、[Native 下单](https://pay.wechatpay.cn/doc/v3/merchant/4012791877)。 |

## 开通和联调顺序

1. **申请商户与产品。** 用经营主体注册、实名并完成支付宝商家账户和微信支付商户号的资料提交、账户验证及协议签署。支付宝在开放平台创建网页应用、添加电脑网站支付能力并按要求提交审核；微信在商户平台申请 Native 支付权限，PC 网站场景按页面填写域名和经营信息。分别确认商户账户、产品和应用都处于可用状态，再安排正式交易。[支付宝网页应用流程](https://open.alipay.com/module/webApp)、[微信 PC 网站申请流程](https://pay.wechatpay.cn/static/applyment_guide/applyment_detail_website.shtml)、[微信 Native 权限申请](https://pay.wechatpay.cn/doc/v3/merchant/4012791875)。
2. **关联收款主体与应用。** 记录支付宝商家账号、应用 APPID 及其产品权限，确保收款商家与应用的关联/授权符合控制台要求；配置应用公钥并取得验签所需的支付宝公钥。微信侧取得 `mchid` 和已认证的 `appid`，由商户超级管理员发起绑定、由 APPID 管理员确认，再配置商户 API 证书/私钥、微信支付公钥或平台证书及 APIv3 密钥。不要把仅有桌面安装包误当作已完成应用绑定。[支付宝应用配置与审核](https://open.alipay.com/module/webApp)、[微信 Native 接入准备](https://pay.wechatpay.cn/doc/v3/merchant/4015614538)、[微信开发必要参数](https://pay.wechatpay.cn/doc/v3/merchant/4013070756)。
3. **部署自有云端订单与回调。** 云端服务保存套餐价格和待支付订单，为每笔订单生成唯一商户订单号，分别对接商户产品。支付宝配置服务端异步通知；微信 Native 下单传入公网可达的 `notify_url`。本仓库预留的接收路径是 `POST /api/payment/callback/alipay` 和 `POST /api/payment/callback/wechat`，实际地址须使用已部署服务的 HTTPS 域名。支付宝返回页、扫码完成提示、桌面端的“我已支付”按钮都不能自行认定会员已开通。[支付宝支付结果异步通知](https://opendocs.alipay.com/open/270/105902)、[微信 Native 开发指引](https://pay.wechatpay.cn/doc/v3/merchant/4012791891)。
4. **验签、查单和测试。** 仅在云端核验支付宝通知签名，或核验微信支付 APIv3 回调签名并解密通知；然后核对原订单号、收款商户/应用、金额和成功状态，幂等结算会员。先核对支付宝该产品当前的沙箱支持范围；可用时以官方沙箱联调，再用受控测试订单验证下单、未支付、成功、错误签名、金额不符、重复通知和通知丢失后的主动查单。微信按官方 Native 开发指引在获批的测试条件下联调二维码、回调和查单。上线前核对账单与本地订单，并走通退款/售后处理。[支付宝沙箱与联调入口](https://open.alipay.com/support/supportCenter.htm)、[微信 Native 开发指引](https://pay.wechatpay.cn/doc/v3/merchant/4012791891)、[微信回调通知](https://pay.wechatpay.cn/doc/v3/merchant/4013070368)、[微信回调与查单指引](https://pay.wechatpay.cn/doc/v3/merchant/4012075249)。
5. **保管密钥并等待审核。** 私钥、APIv3 密钥、证书、数据库口令只存放在受控云端密钥管理或服务器私有配置中，限制访问并准备轮换；不要放入 Electron 安装包、前端、Git、日志或聊天。平台审核、产品权限、经营类目和网站要求均可能因主体及实际业务不同而变化，提交材料或完成开发不保证获批或可上线收款。[支付宝应用审核流程](https://open.alipay.com/module/webApp)、[微信证书与密钥说明](https://pay.wechatpay.cn/doc/v3/merchant/4013070756)、[微信 Native 权限申请](https://pay.wechatpay.cn/doc/v3/merchant/4012791875)。

## 当前代码能做什么

仓库已有会员套餐、订单与回调的**接口框架**：桌面后端的 [`billing.py`](../backend/app/api/billing.py) 只代理到独立云端账号服务；云端的 [`payments.py`](../cloud-service/junengpao_cloud/payments.py) 定义订单和已验签通知契约，但默认没有支付宝或微信支付的生产适配器，未配置或未接通时返回不可用，不会创建真实付款或自动授予会员。桌面端目前还要求订单返回 HTTPS `pay_url`，所以微信 `code_url` 必须由自有 HTTPS 收银台承载。相关部署前提见 [`cloud-service/README.md`](../cloud-service/README.md)。

界面上的“赞助我”是个人赞助二维码，和商户会员订单没有关联；用户扫该码付款**不会、也不能据此自动开通会员**。商户产品获批、云端账号及支付适配器完成联调前，应继续保持会员充值不可用，并如实告知用户。
