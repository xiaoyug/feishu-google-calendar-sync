# 飞书 ⇄ Google 日历 双向同步

> 你有没有遇到过这种事：Google 日历上排满了会，同事在飞书上一看你「空闲」，直接约了同一个时段。

这个工具让两边日历自动保持一致——**Google 上有会，飞书自动 block；飞书上有会，Google 自动 block**。设计原则是「宁可多，不能漏」：多挡一格无所谓，漏一个会就是事故。

装完之后每 10 分钟自动对齐一次，不用管。

## 为什么不能用官方的日历订阅

试过的人都会先想到订阅，但两条路都堵死：

| 方案 | 为什么不行 |
|------|-----------|
| 飞书订阅 Google 的 ICS 链接 | 订阅日历**不计入忙闲状态**——同事约你时，你依然显示「空闲」 |
| Google 订阅飞书 | 飞书不提供公开的 ICS 导出地址；且 Google 拉取外部订阅的延迟可达 24 小时以上 |

所以这个工具走 API 双向镜像：把对面的日程**以真实日程的形式**建进你的主日历，忙闲状态才真正生效。

## 快速开始

需要 macOS 或 Linux、Python 3.9+、Node.js。三条命令：

```bash
git clone https://github.com/xiaoyug/feishu-google-calendar-sync.git
```

```bash
cd feishu-google-calendar-sync && python3 setup.py
```

安装向导会带你走完 6 步（约 10 分钟）：检查环境 → 飞书扫码授权 → 测试日历读写 → 建 Google OAuth 凭证 → Google 授权 → 试运行并装定时任务。

**中途卡住不要紧**：每一步都会先检测，重新运行 `python3 setup.py` 会自动跳过已完成的部分。Google 那一步最容易卡，详细图文见 [docs/google-setup.md](docs/google-setup.md)。

## 工作原理

- 每 10 分钟全量比对两边「昨天 ～ 未来 60 天」的日程。**无本地状态文件**，每轮都是重新对账，所以天然幂等、断了自愈
- Google 日程 → 在飞书主日历建镜像：标题前缀 `[G] `，忙闲=忙
- 飞书日程 → 在 Google 建镜像：标题前缀 `[飞书] `
- 源头改时间或改标题 → 镜像跟着改；源头删除 → 镜像删除
- 带前缀的镜像**永不反向同步**，不会滚雪球
- 重复日程按实例逐个展开（周会、每日站会都能正确挡住）
- **任何一侧读取失败，本轮立即中止**——绝不会因为读不到数据就误删对面的镜像
- 你已拒绝（declined）的邀请不同步

## 安全须知（重要）

这个工具会拿到你日历的读写权限，用之前请花一分钟看完这节。

**凭证只在你自己电脑上。** 全部存放于 `~/.config/calendar-sync/`，权限 600，不经过任何第三方服务器。作者也看不到——这个仓库里没有任何凭证，你的授权是你自己在 Google 和飞书那边完成的。

**数据只在你的两个账号之间流动**，没有中间服务器。

**同事会看到你的日程标题吗？** 取决于你飞书主日历的权限设置。飞书默认「仅显示忙闲」，同事只看到你忙、看不到标题。想确认：飞书日历 → 我的日历 → 权限设置。

**两个文件绝对不要外发：**

| 文件 | 为什么危险 |
|------|-----------|
| `~/.config/calendar-sync/` 下的 `*token*.json` / `*client_secret*.json` | 等同于你日历的钥匙 |
| `~/.config/calendar-sync/sync.log` | 逐条记录了你的**会议标题** |

**求助时贴这个，不要贴日志：**

```bash
python3 sync.py --doctor
```

它会输出一份诊断报告，把会议标题、日程 ID、私密地址全部替换成占位符，可以安全地贴给同事、AI 助手或 GitHub issue。

**用 AI 助手（Codex / Claude Code）辅助安装？** 仓库里的 [AGENTS.md](AGENTS.md) 会被它们自动读取，里面写好了红线：不许读凭证文件、不许读原始日志、不许代做 OAuth。但请注意，**安装向导需要扫码和浏览器授权，最好由你自己在真实终端里跑** `python3 setup.py`，让 AI 在旁边答疑就好。

**建议加一道保险**：给 Google 授权时用的是你自己 GCP 项目里的「测试应用」，随时可以在 <https://myaccount.google.com/permissions> 一键撤销；飞书侧撤销用 `lark-cli auth logout`。

## 日常使用

```bash
tail -f ~/.config/calendar-sync/sync.log
```

| 想做什么 | 怎么做 |
|---------|--------|
| 看同步日志 | `tail -f ~/.config/calendar-sync/sync.log` |
| 手动跑一次 | `python3 sync.py` |
| 只看计划不改数据 | `python3 sync.py --dry-run` |
| 生成脱敏诊断报告 | `python3 sync.py --doctor` |
| 改同步频率为 5 分钟 | `./install.sh 5` |
| 改同步时间范围 | 编辑 `~/.config/calendar-sync/config.json` 的 `window_future_days`（默认 60 天） |
| 停用 | `./uninstall.sh` |
| 停用并删除本地凭证 | `./uninstall.sh --purge` |

改完 `sync.py` 后重跑 `./install.sh` 即可重新部署（运行时跑的是 `~/.config/` 下的拷贝）。

## 常见问题

**镜像日程的 `[G] ` / `[飞书] ` 前缀能删吗？**
不能。它既是来源标识，也是防止无限循环的标记。

**我手动删了一条镜像，会怎样？**
下一轮自动重建——**以源头为准**。同理，改会议请改源头，直接改镜像会被覆盖。

**同一个会两边都收到邀请了，会重复吗？**
会各多出一条镜像。这是刻意的取舍：宁可多一条，不能漏一个。

**第一次跑创建了几十条，正常吗？**
正常，那是把你已有的存量日程补齐镜像。之后每轮通常都是 0 改动。

**报错 `403 access_denied`？**
Google 那步没把自己加进 Test users。见 [docs/google-setup.md](docs/google-setup.md) 第 4 步。

**跑了几天突然全部失败，报 `invalid_grant`？**
你的 Google 应用还停在 Testing 状态——这种状态下 refresh token **满 7 天必失效**，是 Google 的硬性策略，不是 bug。解法：把应用发布到 Production（见 [docs/google-setup.md](docs/google-setup.md) 第 6.5 步），再重跑 `python3 google_auth.py`。发布不需要过审。

**报错 `找不到 lark-cli`？**
`npm i -g @larksuite/cli`，然后 `lark-cli auth login`。

**电脑合盖 / 关机期间会漏吗？**
不会漏，只会延迟。开机后 10 分钟内自动补齐——因为每轮都是全量对账，不依赖「上次跑到哪」。想要 7×24 实时，把它部署到一台常开的服务器上（需要该服务器能访问 googleapis.com）。

## 技术栈

纯 Python 标准库，零第三方依赖。飞书侧调用[官方 lark-cli](https://github.com/larksuite/cli)（用户身份自带日历权限，省掉自建应用申请权限的流程），Google 侧调用 Calendar API v3。

`sync.py` 里还保留了一个**只读降级模式**：如果暂时搞不定 Google OAuth，可以在 config.json 里填 Google 日历的私密 ICS 地址，先把 Google→飞书 单向跑起来（内置 RRULE 展开器，支持 DAILY/WEEKLY/MONTHLY/YEARLY + EXDATE + 单次改期）。

## License

MIT
