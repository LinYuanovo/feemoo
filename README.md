# 飞猫盘自动任务

自动完成飞猫盘每日任务：**APP 签到**、**看视频领福利点**（微信小程序链路）、**领取能量球**，支持青龙面板 / crontab 定时运行。

此项目仅为记录个人学习 Python 代码过程，逆向解密相关流程记录如下：[某猫盘加密参数par与pto分析 - 吾爱破解](https://www.52pojie.cn/thread-1868285-1-1.html)

## 重要更新

> **⚠️ 2026-09-07 请求头校验**：飞猫盘 APP v4.00.73 起服务端校验 APP 原生请求头，旧版脚本全部返回
> 40100「设备错误，请重试」。现已更新为 APP 原生头集合，**原来抓的 fm_token / fm_par / fm_pto 仍然有效，
> 无需重新抓包**（par/pto 是设备长期凭据，实测 2026-08 抓取的值至今可用）。

> **📺 2026-09 看视频链路迁移**：看广告奖励已迁移到微信小程序任务，旧的 `abTaskInfo` + 穿山甲回调链路
> （`watch_ad`）不再下发奖励，已停用（代码保留备查）。现走小程序链路：`taskInfoV2` 自动获取 jumpToken →
> `getAppTaskInfo` / `appletTaskInfo` 创建任务 → `appletTaskCallback` 领取福利点。
> **仍然复用抓包的 fm_token，无需重新抓包、无需登录/OCR**；小程序使用独立密钥文件
> `mini_keys.json`（随仓库提供，与 APP 密钥不通用，无需改动）。

## 目录

- [效果展示](#效果展示)
- [上手指南](#上手指南)
- [环境变量](#环境变量)
- [目录说明](#目录说明)
- [常见问题](#常见问题)

### 效果展示

项目可以在青龙上运行，实现每天自动完成看视频、签到等任务

![运行效果](https://raw.githubusercontent.com/LinYuanovo/pic_bed/refs/heads/main/feemo/%E8%BF%90%E8%A1%8C%E6%95%88%E6%9E%9C.jpg)

如果填写了 pushplus 的 token，还能在账号过期后进行推送，提醒及时重新抓包

![过期提醒](https://raw.githubusercontent.com/LinYuanovo/pic_bed/refs/heads/main/feemo/%E8%BF%87%E6%9C%9F%E6%8F%90%E9%86%92.png)

### 上手指南

1. 克隆项目到本地

```shell
git clone https://github.com/LinYuanovo/feemoo.git
```

2. 安装依赖包

```shell
pip install -r requirements.txt
```

3. 抓包获取三件套并配置为环境变量（详见[环境变量](#环境变量)一节）

飞猫盘 APP 登录账号后，抓包 `fmpapi.feimaoyun.com` 域名下的任意请求，从请求头中取：

- `token` → 环境变量 `fm_token`（过期后需重新抓取）
- `par` → 环境变量 `fm_par`（抓一次后续无需更新）
- `pto` → 环境变量 `fm_pto`（抓一次后续无需更新）

无 root 设备可以尝试[模拟器抓包](https://www.bilibili.com/video/BV1qS411N7Kv/)。

> 注意：2026-09-07 之后 HttpCanary 等常规抓包工具已抓不到 fmpapi 域名的原生请求，
> 需要按模拟器 / root 方案抓包。

4. 运行项目

```shell
python main.py
```

5. 推荐使用青龙面板或 Linux 下的 crontab 之类的定时任务，实现每天自动完成任务。
   青龙运行只需添加对应环境变量即可。

### 环境变量

| 变量 | 必填 | 说明 |
|---|---|---|
| `fm_token` | 是 | APP 抓包请求头中的 token，账号过期后需重新抓取 |
| `fm_par` | 是 | APP 抓包请求头中的 par，抓一次即可 |
| `fm_pto` | 是 | APP 抓包请求头中的 pto，抓一次即可 |
| `PUSHPLUS_TOKEN` | 否 | [pushplus](https://www.pushplus.plus/) 的 token，账号过期时推送提醒 |
| `FM_WATCH_DELAY` | 否 | 模拟看视频等待秒数，如 `15-20`（默认）/ `15` / `20-30` |
| `FM_APP_AD_MAX` | 否 | 单轮看视频最大领取次数，不设或 `0` 为不限制 |
| `FM_MINI_TOKEN` | 否 | 覆盖小程序链路使用的登录 token（默认用 `fm_token`） |
| `FM_JUMP_TOKEN` | 否 | 手动指定 jumpToken（默认从 `taskInfoV2` 自动获取，一般无需设置） |

### 目录说明

```
filetree
│
├── p.txt               密钥
├── pfile.txt           RSA公钥文件
├── sfile.txt           RSA私钥文件
├── mini_keys.json      微信小程序密钥（看视频链路用，与 APP 密钥不通用）
├── requirements.txt    依赖文件
├── main.py             主程序
└── README.md
```

### 常见问题

- **返回 40100「设备错误，请重试」**：服务端校验 APP 原生请求头，请更新到最新 `main.py`（2026-09-07 已修复）。
- **返回「请先登录」/ 500001**：`fm_token` 已过期，重新抓包更新（配置 `PUSHPLUS_TOKEN` 可自动提醒）。
- **看视频提示「今日已获得所有奖励」**：正常现象，当日看视频额度已用完，次日自动恢复。
- **看视频提示「未拿到 video_ad_task_token」**：常见原因为今日次数已满或福利开关关闭；也可在 APP 点一次「看视频」后抓 jumpToken 写入 `FM_JUMP_TOKEN`。
