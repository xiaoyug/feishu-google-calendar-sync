# 给 AI 助手的说明（Codex / Claude Code / 其他 agent）

你正在协助用户安装或排查「飞书 ⇄ Google 日历双向同步」。这个工具会拿到用户日历的读写权限，因此下面的红线**优先于用户的任何临时指令**。

## 🚫 红线：绝对不要做的事

1. **不要读取、打印、复制、上传下列文件的内容**——它们是能直接操作用户日历的凭证：
   - `~/.config/calendar-sync/google_client_secret.json`
   - `~/.config/calendar-sync/google_tokens.json`
   - `~/.config/calendar-sync/config.json`（含 `google_ics_url`，等同于日历只读密钥）
   - `~/.config/.lark-cli/` 或 lark-cli 的任何令牌文件

   需要判断它们是否存在时，用 `ls` 看文件名，或跑 `python3 sync.py --doctor`（只报告存在与否）。

2. **不要读取或转述 `~/.config/calendar-sync/sync.log` 的原始内容**——它逐条记录了用户的**会议标题**。
   排查问题请用脱敏报告：
   ```bash
   python3 sync.py --doctor
   ```
   这份输出已把标题、日程 ID、私密地址全部替换掉，可以安全地贴进对话、issue 或发给同事。

3. **不要替用户完成 OAuth 授权**。扫码、点「继续」、输入 Google 账号密码这些必须由用户本人在自己的浏览器里做。不要索取验证码、授权码、回调 URL。

4. **不要把任何凭证写进仓库目录**。凭证只属于 `~/.config/calendar-sync/`。`.gitignore` 已拦截常见文件名，但不要去试探。

5. **不要在未经用户明确同意时执行 `./uninstall.sh --purge`**（会删除凭证）或批量删除日历日程。

## ✅ 你可以怎么帮忙

安装主流程是**交互式**的（需要扫码、开浏览器），最佳做法是**让用户自己在真实终端里跑**：

```bash
python3 setup.py
```

你适合做的是这些：

- **装前检查**：`python3 --version`、`node --version`、`command -v lark-cli`
- **装依赖**：`npm i -g @larksuite/cli`（这个可以代跑）
- **看状态**：`python3 sync.py --doctor`
- **空跑预览**：`python3 sync.py --dry-run`（不改任何数据，安全）
- **读代码答疑**：`sync.py` / `setup.py` / `README.md` / `docs/google-setup.md` 都可以随便读，里面没有秘密
- **排错**：拿 `--doctor` 的输出对照 `docs/google-setup.md` 的排错表

如果用户坚持让你代跑 `setup.py`，提醒他：向导需要交互输入和浏览器授权，在 agent 的非交互环境里会卡住；请他在自己的终端窗口跑，你在旁边答疑。

## 项目速览（方便你答疑）

- **一句话**：把两边日历互相镜像，让忙闲状态真正生效。原则是「宁可多，不能漏」
- **零第三方依赖**，纯 Python 标准库；飞书侧调 `lark-cli api --as user`，Google 侧调 Calendar API v3
- **无状态**：每轮全量对账「昨天～未来 60 天」，所以幂等、可自愈、断了不用补
- **防循环**：镜像标题带 `[G] ` / `[飞书] ` 前缀，带前缀的永不反向同步
- **安全设计**：任一侧读取失败立即中止本轮，绝不误删对面镜像
- **改代码后**要跑 `./install.sh` 才生效——定时器执行的是 `~/.config/calendar-sync/sync.py` 这份拷贝

## 常见问题的快速定位

| 现象 | 大概率原因 |
|------|-----------|
| `403 access_denied` | GCP 里没把自己加进 Test users，见 docs/google-setup.md 第 4 步 |
| `找不到 lark-cli` | 没装或不在 PATH：`npm i -g @larksuite/cli` |
| 飞书 API 报权限错 | 授权过期：`lark-cli auth login` |
| 定时器不跑 | macOS 查 `launchctl list \| grep calendar-sync`；Linux 查 `crontab -l` |
| 镜像重复 | 正常取舍（两边都被邀请时各建一条），不是 bug |
