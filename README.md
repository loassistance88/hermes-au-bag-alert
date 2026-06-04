# Hermes AU Bag Alert

这个脚本会检查 Hermès 澳洲官网的 women's bags and clutches 分类页，提取包包款式名、颜色、价格和链接。它会把已见过的商品保存到本地 JSON 文件；以后每次运行时，只有发现新商品才会发邮件到：

`.env` 或 GitHub Secrets 里配置的收件邮箱

如果新商品名称包含 `Neo Garden 23` 或 `Herbag Zip 20 bag`，邮件 Subject 会以 `❗` 开头。

## 文件

- `hermes_au_bag_alert.py`：主脚本，无第三方 Python 依赖。
- `.env.example`：邮件和监控配置模板。
- `run_once.ps1`：手动运行一次。
- `install_windows_task.ps1`：安装 Windows 定时任务。

## 配置

1. 安装 Python 3.10+。
2. 把 `.env.example` 复制成 `.env`。
3. 在 `.env` 里填入发件 Gmail：

```env
SMTP_USER=your_sender_gmail@gmail.com
SMTP_PASSWORD=your_gmail_app_password
SMTP_FROM=your_sender_gmail@gmail.com
```

Gmail 普通密码通常不能直接用于 SMTP，需要在 Google 账号里开启两步验证，然后生成 App Password。

生成 App Password 后，可以用这个脚本安全输入并写入 `.env`：

```powershell
powershell -ExecutionPolicy Bypass -File .\set_gmail_app_password.ps1
```

然后发送测试邮件：

```powershell
powershell -ExecutionPolicy Bypass -File .\run_once.ps1 --test-email
```

## 手动测试

先运行 dry-run，确认能抓到商品：

```powershell
powershell -ExecutionPolicy Bypass -File .\run_once.ps1 --dry-run
```

`run_once.ps1` 会自动寻找用户目录里安装的 Python 3.12/3.13；即使 `python` 命令还没刷新进 PATH，也可以运行。

第一次正式运行默认只建立 baseline，不会把当前页面已有商品全部发一遍：

```powershell
powershell -ExecutionPolicy Bypass -File .\run_once.ps1
```

如果你想第一次就把当前所有商品都发到邮箱：

```powershell
powershell -ExecutionPolicy Bypass -File .\run_once.ps1 --send-initial
```

## 定时运行

每 10 分钟检查一次：

```powershell
powershell -ExecutionPolicy Bypass -File .\install_windows_task.ps1 -EveryMinutes 10
```

之后可以在 Windows Task Scheduler 里看到任务名：

`Hermes AU Bag Alert`

## GitHub Actions 24 小时运行

如果要电脑关机也继续检查，推荐用 public GitHub repo 加 GitHub Actions。

GitHub repo 需要添加这些 Secrets：

```text
EMAIL_TO=收件邮箱
SMTP_USER=发件 Gmail
SMTP_PASSWORD=你的 Gmail App Password
SMTP_FROM=发件 Gmail
```

本仓库里的 workflow 位于：

```text
.github/workflows/hermes-alert.yml
```

它会每 5 分钟运行一次。第一次运行只保存当前商品作为 baseline，不发邮件；之后发现新商品才会发邮件。每次检查后，`hermes_au_bag_state.json` 会被自动提交回仓库，用来记住已经提醒过的商品。

## 调整重点提醒款式

修改 `.env` 里的这一行即可：

```env
WATCH_TERMS=Neo Garden 23,Herbag Zip 20 bag
```

匹配不区分大小写，会忽略多数标点差异。

## 说明

Hermès 页面结构可能会变。如果脚本报 `No products were parsed`，可以运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\run_once.ps1 --debug-html hermes_debug.html
```

这样会保存抓到的网页 HTML，方便排查页面结构变化。
