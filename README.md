# 飞猫盘自动任务（dev 分支：账号密码登录版）

自动完成飞猫盘每日任务：**APP 签到**、**APP 看视频**、**小程序看视频**、**领取能量球**，支持多账号与青龙面板 / crontab 定时运行。

与 `main` 分支的区别：

| | main 分支 | dev 分支（本分支） |
|---|---|---|
| 凭据来源 | 手动抓包三件套（fm_token / fm_par / fm_pto） | **账号密码自动登录**，无需抓包 |
| 登录验证 | 无 | 极验 v4 滑块（远程 ddddocr 服务识别） |
| 多账号 | 单账号 | 支持（`fm_account` 多组） |
| token 管理 | 手动更新环境变量 | `sessions.json` 自动缓存、失效自动重登 |

逆向解密相关流程记录：[某猫盘加密参数par与pto分析 - 吾爱破解](https://www.52pojie.cn/thread-1868285-1-1.html)

## 目录

- [效果展示](#效果展示)
- [上手指南](#上手指南)
- [环境变量](#环境变量)
- [目录说明](#目录说明)
- [常见问题](#常见问题)

### 效果展示

项目可以在青龙上运行，实现每天自动完成看视频、签到等任务

![运行效果](https://raw.githubusercontent.com/LinYuanovo/pic_bed/refs/heads/main/feemo/%E8%BF%90%E8%A1%8C%E6%95%88%E6%9E%9C.jpg)

如果填写了 pushplus 的 token，还能在账号过期后进行推送提醒

![过期提醒](https://raw.githubusercontent.com/LinYuanovo/pic_bed/refs/heads/main/feemo/%E8%BF%87%E6%9C%9F%E6%8F%90%E9%86%92.png)

### 上手指南

1. 克隆 dev 分支

```shell
git clone -b dev https://github.com/LinYuanovo/feemoo.git
```

2. 安装依赖

```shell
pip install -r requirements.txt
```

3. 准备一个远程 ddddocr 识别服务（用于极验 v4 滑块），地址填入环境变量 `DDDD_API_BASE`。

4. 配置环境变量（详见下一节），至少需要 `fm_account` 与 `DDDD_API_BASE`，然后运行：

```shell
python main.py
```

密钥文件已随仓库提供：`app_keys.json`（APP 协议）与 `mini_keys.json`（微信小程序协议），两者不是同一套、不可混用，无需任何手动准备。

首次运行会走「登录 → 过滑块 → 缓存 token 到 sessions.json」，之后每天直接使用缓存 token，失效时自动重新登录。推荐使用青龙面板或 crontab 定时运行。

### 环境变量

**必填**

| 变量 | 说明 |
|---|---|
| `fm_account` | 账号密码，格式 `user&password`；多账号用换行或 `@` 分隔，如 `user1&pass1@user2&pass2` |
| `DDDD_API_BASE` | 远程 ddddocr 服务地址（滑块/点选识别），兼容旧变量 `DDDD_SLIDE_URL` |

**可选**

| 变量 | 说明 |
|---|---|
| `PUSHPLUS_TOKEN` | [pushplus](https://www.pushplus.plus/) 的 token，账号异常时推送提醒 |
| `GEETEST_CAPTCHA_ID` | 极验 captcha_id，默认已内置，一般无需修改 |
| `GEETEST_RISK_TYPE` | 极验验证类型，默认 `slide` |
| `GEETEST_MAX_RETRY` | 滑块失败重试次数，默认 `3` |
| `GEETEST_HTTP` | geeked 的 HTTP 后端选择（调试用） |
| `FM_WATCH_DELAY` | 模拟看视频等待秒数，如 `15-20`（默认）/ `15` / `20-30` |
| `FM_APP_AD_MAX` | 单轮看视频最大领取次数，不设或 `0` 为不限制 |
| `FM_MINI_TOKEN` | 覆盖小程序链路使用的登录 token（默认用当前账号会话 token） |
| `FM_JUMP_TOKEN` | 手动指定 jumpToken（默认从 `taskInfoV2` 自动获取，一般无需设置） |
| `FM_SESSIONS_FILE` | 会话缓存文件路径，默认 `sessions.json` |
| `FM_API_HOST` | API 域名，默认 `fmpapi.feemoo.com` |

### 目录说明

```
filetree
│
├── geeked/             极验 v4 滑块处理（轨迹、签名、缺口识别，识别走远程 ddddocr）
├── app_keys.json       APP 协议密钥
├── mini_keys.json      微信小程序协议密钥（与 APP 密钥不是同一套，禁止混用）
├── requirements.txt    依赖文件
├── main.py             主程序
└── README.md

本地运行时文件（已被 .gitignore 排除，不会误提交）：
├── sessions.json       多账号 token / 设备缓存
└── device.txt 等       历史遗留凭据文件（自动迁移进 sessions.json）
```

### 常见问题

- **提示「请设置环境变量 fm_account ...」**：`fm_account` 或 `DDDD_API_BASE` 未配置，两者均为必填。
- **滑块一直失败**：检查 `DDDD_API_BASE` 服务是否可用；可通过 `GEETEST_MAX_RETRY` 增加重试次数。
- **看视频提示「今日已获得所有奖励」**：正常现象，当日额度已用完，次日自动恢复。
- **看视频提示「未拿到 video_ad_task_token」**：常见原因为今日次数已满或福利开关关闭。
- **账号过期**：脚本会自动尝试重新登录；若配置了 `PUSHPLUS_TOKEN` 会推送提醒。
