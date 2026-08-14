# Google 侧设置详解

这是整个安装过程唯一有点绕的部分。**不花钱、不需要 Google 审核**，全程约 5 分钟。

原理说明：Google 不允许第三方应用随便读写你的日历，你得在自己的 Google Cloud 里建一个「只有你自己能用」的应用，然后授权给它。这个应用永远处于 Testing 状态，只对你（和你手动添加的测试用户）开放，所以不需要提交审核。

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
| 「Google hasn't verified this app」 | 不是错误，见第 7 步，点 Advanced 继续 |
| 响应里没有 refresh_token | 之前授权过同一个应用。去 <https://myaccount.google.com/permissions> 移除 calendar-sync 的授权后重来 |
| 提示需要两步验证 | 先去 <https://myaccount.google.com/security> 开启 2SV，这是 Google Cloud 的硬性要求 |
| 回调页面打不开 / 一直转圈 | 本机 8765 端口被占用。关掉占用的程序，或改 `google_auth.py` 里的 `PORT` |

## 会用到哪些权限

只申请一个权限：`https://www.googleapis.com/auth/calendar.events`——读写日历中的日程。

不包含：读取邮件、读取联系人、删除日历本身。凭证只存在本机 `~/.config/calendar-sync/`（600 权限），不经过任何第三方服务器。
