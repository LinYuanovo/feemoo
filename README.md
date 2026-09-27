
# 飞猫盘自动任务

此项目仅为记录个人学习Python代码过程，逆向解密相关流程记录如下：[某猫盘加密参数par与pto分析 - 吾爱破解](https://www.52pojie.cn/thread-1868285-1-1.html)

> **⚠️ 2026-09-07 更新**：飞猫盘 APP v4.00.73 起服务端校验 APP 原生请求头，旧版脚本全部返回
> 40100「设备错误，请重试」。请更新到最新 main.py，**原来抓的 fm_token / fm_par / fm_pto 仍然有效，
> 无需重新抓包**（par/pto 是设备长期凭据，实测 2026-08 抓取的值至今可用）。

## 目录

- [效果展示](#效果展示)
- [上手指南](#上手指南)
- [目录说明](#目录说明)

### 效果展示

项目可以在青龙上运行，实现每天自动完成看广告、签到等任务

![运行效果](https://raw.githubusercontent.com/LinYuanovo/pic_bed/refs/heads/main/feemo/%E8%BF%90%E8%A1%8C%E6%95%88%E6%9E%9C.jpg)

如果填写了pushplus的token还能在账号过期后进行推送，提醒及时抓包

![过期提醒](https://raw.githubusercontent.com/LinYuanovo/pic_bed/refs/heads/main/feemo/%E8%BF%87%E6%9C%9F%E6%8F%90%E9%86%92.png)

### 上手指南

克隆项目到本地
```shell
git clone https://github.com/LinYuanovo/feemoo.git
```

安装依赖包
```shell
pip install -r requirements.txt
```

填写参数

进入项目根目录下，修改main.py文件，填写以下参数(pto和par抓一次后续无需更新)
- fm_token： 飞猫盘APP内登录账号后抓包fmpapi.feimaoyun.com域名下的任意请求中token，无root可以尝试[模拟器抓包](https://www.bilibili.com/video/BV1qS411N7Kv/)
- fm_pto： 同上请求中的pto
- fm_pto： 同上请求中的par
- PUSHPLUS_TOKEN（可选）： pushplus的token，可以在[官网](https://www.pushplus.plus/)个人中心获取

如图所示

![CK填写](https://raw.githubusercontent.com/LinYuanovo/pic_bed/refs/heads/main/feemo/ck%E5%A1%AB%E5%86%99.png)

如果是青龙运行的话，则是添加环境变量fm_token和PUSHPLUS_TOKEN

运行项目
```shell
python main.py
```

至此已能够正常运行项目，不过仍然推荐使用青龙或是Linux下的crontab之类的定时任务实现每天自动完成任务

### 目录说明

```
filetree 
│
├── p.txt               密钥
├── pfile.txt           RSA公钥文件
├── sfile.txt           RSA私钥文件
├── requirements.txt    依赖文件
├── main.py             主程序
└── README.md
```

#### 
