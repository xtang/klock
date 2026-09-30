# Klock

一个简单的工时工作空间：项目、计时、协作者、月度导出。暖白和墨绿界面，无前端构建步骤。本地体验无需第三方依赖；Google 登录使用官方 google-auth 库。

## 运行

```sh
python3 server.py
```

打开 http://localhost:5188 。首次访问显示明确标注的体验数据；点击「创建我的工作空间」建立管理员账号并进入空白空间。体验记录只存在当前页面内存，不会混入数据库。正式数据保存在 `data/klock.db`；浏览器使用 HttpOnly 会话 Cookie，保留 Cookie 即可保持身份。配置 Google 登录后可跨浏览器恢复同一身份；未绑定的本地身份仍依赖当前浏览器 Cookie。请备份数据库。

可通过 `PORT`、`HOST`、`KLOCK_DB` 环境变量配置端口、绑定地址和数据库文件。开发默认仅监听本机。需要在可信局域网协作时使用 `HOST=0.0.0.0 python3 server.py`，从本机的局域网地址进入后生成邀请链接，让协作者使用同一地址访问。首次管理员创建需在开放网络前完成；本版适用于个人和可信团队的自托管试用，公网部署前需配置 HTTPS、Google 登录、备份和访问保护。

## 功能

- 创建和归档项目；所有成员共享项目与工时记录。
- 开始 / 结束计时。计时状态存到 SQLite，关闭页面或重启服务后仍可恢复；按开始时浏览器时区在午夜拆分记录。
- 手动补记（精确到秒）、删除自己的记录、项目筛选。
- 管理员创建和撤销一次性邀请链接，7 天有效；自行复制分享，不自动发邮件。
- 自然月汇总、项目小计、成员与明细。小时数四舍五入到两位，总计从整数秒计算。
- 导出 CSV（UTF-8 BOM，包含整数秒，防公式注入）、JSON（兼容 monthly-time-report 技能）、打印版月报（浏览器「存储为 PDF」）。
- 桌面 / 手机布局、键盘操作、弹窗焦点限制、减少动画设置。

导出只包括已保存记录，排除运行中的计时。空白日期不代表零工时，不含费率、金额、审批或薪酬功能。JSON 可直接交给 monthly-time-report 技能生成其专业 PDF。

## 检查

```sh
python3 -m unittest discover -s tests -v
node --check public/app.js
node tests/test_ui.mjs
```

Python 标准库 HTTP 服务和 SQLite 便于本地运行，不包含邮件发送和云托管。Google 账号使用稳定的 Google 用户 ID 绑定成员；新成员仍需邀请链接。

## 配置 Google / Gmail 登录

支持 Gmail 和 Google Workspace 账号，仅申请 `openid email profile`，不申请 Gmail 邮件读写权限。

1. 在 [Google Cloud / Google Auth Platform](https://console.cloud.google.com/auth/overview) 选择或创建项目，填写 Branding 和 Audience；如应用处于 Testing，按控制台提示添加测试用户。
2. 创建 **Web application** 类型的 OAuth client。在 Authorized redirect URIs 中添加：

   ```text
   http://localhost:5188/api/auth/google/callback
   ```

   部署后改用 `https://你的域名/api/auth/google/callback`，须与实际回调完全一致。本实现通过服务端跳转，不需要浏览器 JavaScript client 配置。

3. 安装依赖，在本地填写凭据：

   ```sh
   python3 -m venv .venv
   .venv/bin/python -m pip install -r requirements.txt
   cp .env.example .env
   ```

   编辑 `.env` 中的 `GOOGLE_CLIENT_ID`、`GOOGLE_CLIENT_SECRET` 和 `KLOCK_BASE_URL`。`.env` 已忽略，不要提交或把 Secret 放入前端；进程环境变量优先于 `.env`。

4. 停止旧服务，然后启动：

   ```sh
   .venv/bin/python server.py
   ```

   从 `KLOCK_BASE_URL` 指定的地址访问，避免混用 localhost 和 127.0.0.1。变更 `.env` 后需重启。公网地址必须 HTTPS；`HOST` / `PORT` 控制实际监听地址，`KLOCK_BASE_URL` 是浏览器访问的地址。

### 身份与邀请

- 空工作空间的第一位 Google 用户成为管理员；首次创建应在公开服务前完成。
- 已有成员再次登录回到同一成员和历史记录，不以邮箱或显示名猜测合并账号。
- 新 Google 用户必须使用有效邀请；邀请在成功加入时原子消耗，取消登录不会消耗。
- 旧本地身份：保留原浏览器登录状态，右上角 **账号 → 绑定 Google 账号**。绑定保留项目、工时和管理员权限。不能把已属于另一成员的 Google 账号合并过来。已经丢失旧 Cookie 的身份不可仅凭姓名或邮箱找回。
- 绑定后可通过 **账号 → 退出登录** 注销当前会话。退出不停止计时。
- 配置 Google 后，原来的填名字创建 / 加入接口关闭，现有本地会话保留用于迁移。没有配置时继续保留本地体验，并明确显示 Google 尚未启用。

服务端使用 authorization code + PKCE、浏览器绑定的一次性 state（10 分钟）与 nonce；官方 google-auth 验证 ID token 的签名、issuer、audience 和有效期。回调地址固定取配置，不信任请求 Host。Google token 和 Client Secret 不保存到浏览器或日志；只持久化 Google `sub` 与已验证邮箱。HTTPS 部署的会话 Cookie 带 Secure、HttpOnly、SameSite=Lax。

参考：[Google 官方 OpenID Connect 文档](https://developers.google.com/identity/openid-connect/openid-connect)。自动化测试覆盖登录、重登、邀请、旧身份绑定、退出、state 重放和错误回调；真实 Google 授权仍需配置自己的 OAuth 凭据后验证。

## 可复用的部署配置

仓库只保存模板，不包含生产域名、服务器地址或管理员邮箱。应用运行配置使用私有 `.env.prod`：

```dotenv
GOOGLE_CLIENT_ID=your-client-id
GOOGLE_CLIENT_SECRET=your-client-secret
KLOCK_BASE_URL=https://klock.example.com
KLOCK_BOOTSTRAP_EMAIL=owner@example.com
```

不要提交实际 `.env.prod`。HTTPS 环境首次创建空间时，只允许 `KLOCK_BOOTSTRAP_EMAIL` 指定的 Google 账号成为管理员。

用 `KLOCK_DOMAIN` 或 `KLOCK_BASE_URL` 生成 Nginx 和证书续期 hook：

```sh
KLOCK_DOMAIN=klock.example.com python3 deploy/render.py
# 或：KLOCK_BASE_URL=https://klock.example.com python3 deploy/render.py
```

输出在被 Git 忽略的 `deploy/generated/` 中。渲染器只替换域名占位符，保留 Nginx 的 `$host`、`$request_uri` 和 shell 变量；域名会先校验，两个配置同时提供时必须一致。只有默认输出目录自动被忽略。

服务器布局沿用 `/opt/klock`：代码及 Dockerfile 放 `app/`，`compose.yaml` 放 `deploy/`，私有环境放根目录 `.env.prod`（权限 600），数据挂载到 `data/`（容器 UID/GID 为 10001）。不要把数据库加入构建上下文。

SSH 目标由本地变量指定，不在仓库写死：

```sh
export DEPLOY_HOST=user@your-server
ssh "$DEPLOY_HOST" 'docker compose -p klock -f /opt/klock/deploy/compose.yaml up -d --build'
```

首次申请证书前使用生成的 `nginx-http.conf`，ACME webroot 为 `/var/www/klock-acme`。证书名应与 `KLOCK_DOMAIN` 一致；拿到证书后改用生成的 `nginx.conf`，执行 `nginx -t` 后重载。生成的 `renew-hook.sh` 可安装到 Certbot 的 `renewal-hooks/deploy/` 目录。

Nginx 模板支持 Cloudflare Automatic SSL/TLS：只有来自官方 Cloudflare IP 段的请求可以用 HTTPS scheme 头避免重定向循环。HTTP-origin 模式下 Cloudflare 到服务器这一段仍未加密；Full (strict) 可启用这段加密。IP 段列表来自模板注释中的官方来源，需要随官方变更更新。

检查部署时显式指定地址：

```sh
KLOCK_BASE_URL=https://klock.example.com python3 deploy/verify.py
# 在服务器上直接验证本机源站，保留 TLS 主机名校验：
KLOCK_BASE_URL=https://klock.example.com python3 deploy/verify.py --origin
```

验证脚本依赖同目录的 `render.py`，复制时保留两者。Google OAuth 控制台的回调地址需与 `${KLOCK_BASE_URL}/api/auth/google/callback` 完全匹配。

`deploy/backup.sh` 可在服务器运行，使用 SQLite 在线备份并校验完整性，备份保留 14 天。容器重建不应删除数据目录；同服务器备份不能替代异地备份。
