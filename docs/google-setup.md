# Google 侧设置详解

这是整个安装过程唯一有点绕的部分。**不花钱、不需要 Google 审核**，全程约 5 分钟。

原理说明：Google 不允许第三方应用随便读写你的日历，你得在自己的 Google Cloud 里建一个「只有你自己能用」的应用，然后授权给它。这个应用不需要提交 Google 审核，但**必须把发布状态改成 Production**（见第 6.5 步）——否则授权满 7 天就会自动失效。

前置条件：一个 Google 账号，且**已开启两步验证**（Google Cloud 从 2025 年起强制要求）。没开的话先去 <https://myaccount.google.com/security> 开一下。

---

## 第 1 步 · 建项目

打开 <https://console.cloud.google.com/projectcreate>

- **Project name** 随便填，比如 `calendar-sync`
- **Location** 保持 `No organization`
- 点 **Create**，等几秒

创建完成后，右上角通知里点 **Select Project** 切换过去。确认页面左上角的项目名已经变成你新建的这个。

---

## 第 2 步 · 启用日历接口

打开 <https://console.cloud.google.com/apis/library/calendar-json.googleapis.com>

确认页面顶部是你刚建的项目，点 **Enable**。

---

## 第 3 步 · 配置授权页

左侧菜单 **APIs & Services → OAuth consent screen**（新版界面叫 **Google Auth Platform → Overview**），点 **Get started**：

1. **App Information**：App name 填 `calendar-sync`，User support email 选你自己 → Next
2. **Audience**：选 **External** → Next
3. **Contact Information**：填你自己的邮箱 → Next
4. **Finish**：勾选同意条款 → Continue → **Create**

> 选 External 不代表别人能用。应用处于 Testing 状态时，只有你添加的测试用户能授权。

---

## 第 4 步 · 把自己加进测试用户 ⚠️

**这一步最容易漏，漏了下一步一定会报 `403: access_denied`。**

左侧 **Audience** 页面 → 下方 **Test users** → **Add users** → 填你自己的 Gmail 地址 → **Save**。

保存后确认列表里能看到你的邮箱。

---

## 第 5 步 · 创建桌面客户端

左侧 **Clients** → **Create client**：

- **Application type** 选 **Desktop app** ← 必须是这个，选 Web application 会因为回调地址对不上而失败
- Name 保持默认
- 点 **Create**

---

## 第 6 步 · 下载凭证并放到位

创建成功的弹窗里，点 **Download JSON**（关掉弹窗后也可以在 Clients 列表里重新下载）。

把下载的文件移动到指定位置并改名：

```bash
mv ~/Downloads/client_secret*.json ~/.config/calendar-sync/google_client_secret.json
```

如果 `~/.config/calendar-sync/` 目录还不存在，先建：

```bash
mkdir -p ~/.config/calendar-sync
```

---

## 第 6.5 步 · 把应用发布到 Production ⚠️⚠️ 最重要的一步

**不做这步，同步会在整整 7 天后自动死掉，而且不会有任何提示。**

Google 的硬性策略：发布状态为 **Testing** 的应用，签发的 refresh token **7 天后失效**。到期后同步会一直报 `invalid_grant`，两边日历各自漂移。

做法：左侧 **Audience** 页面 → 顶部 Publishing status 下点 **PUBLISH APP** → 弹窗确认。
发布后状态变成 **In production**，refresh token 不再过期。**不需要提交 Google 审核**——审核只影响那个「未验证应用」的警告页，不影响功能。

### 如果 PUBLISH APP 按钮是灰的

页面会提示「Your app's OAuth configuration is incomplete」。按顺序排查：

1. **Branding 页面**：`App name` 和 `User support email` 两个带 `*` 的必填项都要有值
2. **Data Access 页面**：点 `Add or remove scopes` → 在最下方「Manually add scopes」文本框粘贴
   `https://www.googleapis.com/auth/calendar.events` → `Add to table` → 勾选 → `Update` → `Save`
   （这一步很容易被忽略：授权能正常工作，但 scope 没在控制台登记过，就会卡住发布）
3. **App domain**：`calendar.events` 属于敏感权限，Google 可能还要求填应用主页和隐私政策链接，
   且域名要先加进 Authorized domains。**这一步需要你有自己的域名**

### 实在发布不了怎么办

如果第 3 条卡住（没有自己的域名），有两个退路：

- **接受 7 天一次的重新授权**：本工具已内置失败告警，令牌一失效就会弹系统通知，
  收到后跑一次 `python3 google_auth.py` 即可，约 1 分钟。数据不会丢——恢复后会自动补齐落下的日程
- **如果你的组织有 Google Workspace**：用组织账号建 GCP 项目，User type 选 **Internal**。
  内部应用没有 7 天限制、也不需要任何审核，是最干净的方案（但同步的会是组织账号的日历）

---

## 第 7 步 · 回到向导完成授权

回到终端，重新运行：

```bash
python3 setup.py
```

向导会检测到凭证已就位，直接进入授权环节，打开浏览器让你登录 Google。

**中途会看到一个吓人的警告页**：「Google hasn't verified this app」。这是因为你的应用没提交审核（也不需要）。点左下角 **Advanced**（高级）→ **Go to calendar-sync (unsafe)**（继续前往）即可。这个应用是你自己五分钟前建的，安全。

最后一页勾选/确认授予日历权限，点 **Continue**。浏览器显示「✅ Google 授权成功」就完成了。

---

## 排错

| 报错 | 原因和解法 |
|------|-----------|
| `403: access_denied` / 「developer hasn't given you access」 | 第 4 步没把自己加进 Test users。加完**重新运行** `python3 setup.py` |
| `invalid_grant` / 同步跑了几天后突然全部失败 | 应用还停在 Testing 状态，refresh token 满 7 天失效。按第 6.5 步发布到 Production，然后重跑 `python3 google_auth.py` |
| 「Google hasn't verified this app」 | 不是错误，见第 7 步，点 Advanced 继续 |
| 响应里没有 refresh_token | 之前授权过同一个应用。去 <https://myaccount.google.com/permissions> 移除 calendar-sync 的授权后重来 |
| 提示需要两步验证 | 先去 <https://myaccount.google.com/security> 开启 2SV，这是 Google Cloud 的硬性要求 |
| 回调页面打不开 / 一直转圈 | 本机 8765 端口被占用。关掉占用的程序，或改 `google_auth.py` 里的 `PORT` |

## 会用到哪些权限

只申请一个权限：`https://www.googleapis.com/auth/calendar.events`——读写日历中的日程。

不包含：读取邮件、读取联系人、删除日历本身。凭证只存在本机 `~/.config/calendar-sync/`（600 权限），不经过任何第三方服务器。
