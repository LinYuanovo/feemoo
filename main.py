import base64
import hashlib
import random
import re
import string
import os
import time
import json
import secrets
import requests

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad
from base64 import b64encode, b64decode
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_v1_5
from urllib.parse import quote
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives import serialization

fm_token = os.environ.get("fm_token") or ""
# 以下两个参数抓一次后续无需更新
fm_pto = os.environ.get("fm_pto") or ""
fm_par = os.environ.get("fm_par") or ""
PUSHPLUS_TOKEN = os.environ.get("PUSHPLUS_TOKEN") or ""

# 微信小程序看视频链路（2026-09 起 APP 广告奖励迁移至微信小程序任务）
MINI_KEYS_FILE = "mini_keys.json"
FM_API_BASE_MINI = "https://fmpapi.feemoo.com"

if fm_token is None:
    print("请设置环境变量fm_token")
    exit(1)


class Utils:
    def __init__(self, keys_file=None):
        self.bits = 2048
        self.key_pair = RSA.generate(self.bits)
        if keys_file:
            # 合并密钥 JSON（p / pfile / sfile），小程序链路使用 mini_keys.json
            with open(keys_file, 'r', encoding='utf-8') as f:
                keys = json.load(f)
            self.pfile = keys["pfile"]
            self.sfile = keys["sfile"]
            self.p = keys["p"]
        else:
            self.pfile = self.read_file("pfile.txt")
            self.sfile = self.read_file("sfile.txt")
            self.p = self.read_file("p.txt")
        self.ak = self.genak()
        self.ed = self.re(self.ak, self.pfile)
        # self.pto = self.re(self.ak, self.p)
        # self.dataa = '{"device_key":"261ff2afcf5843bcd9ac94e46338de181"}'
        # self.par = self.secret(self.dataa, self.ak)
        # pto和par写死即可，并不校验
        self.pto = fm_pto
        self.par = fm_par

    # 读取文件
    def read_file(self, file_name):
        with open(file_name, 'r') as f:
            return f.read()

    # 生成参数
    def in_parameter(self, data):
        data = str(data).replace(" ", "").replace("\'", '\"')
        if data or data == {}:
            data = {
                "sn": self.ed,
                "jt": self.secret(data, self.ak)
            }
            return json.dumps(data)
        else:
            return data

    # 生成随机字符串
    def genak(self, length=12):
        alphabet = string.ascii_letters + string.digits
        return ''.join(secrets.choice(alphabet) for _ in range(length))

    # AES加密
    def ae(self, plaintext, key):
        cipher = AES.new(key.encode('utf-8'), AES.MODE_ECB)
        ciphertext = cipher.encrypt(pad(plaintext.encode('utf-8'), AES.block_size))
        return b64encode(ciphertext).decode('utf-8')

    # AES解密
    def ad(self, ciphertext_b64, key):
        ciphertext = b64decode(ciphertext_b64)
        cipher = AES.new(key.encode('utf-8'), AES.MODE_ECB)
        plaintext = unpad(cipher.decrypt(ciphertext), AES.block_size)
        return plaintext.decode('utf-8')

    # 公钥加密
    def re(self, plaintext, public_key):
        if isinstance(plaintext, dict):
            plaintext = json.dumps(plaintext)
        # publicKey = RSA.import_key(public_key)
        # cipher_rsa = PKCS1_v1_5.new(publicKey)
        # ciphertext = base64.b64encode(cipher_rsa.encrypt(plaintext.encode(encoding='utf-8')))
        # return b64encode(ciphertext).decode('utf-8')
        public_key = serialization.load_pem_public_key(public_key.encode())
        cipher_text = public_key.encrypt(
            plaintext.encode(),
            padding.PKCS1v15()
        )
        return b64encode(cipher_text).decode('utf-8')

    # RSA私钥解密
    def rd(self, ciphertext_b64, private_key):
        # private_key = private_key.replace("-----BEGIN RSA PRIVATE KEY-----", "").replace("-----END RSA PRIVATE KEY-----", "").replace("\n", "")
        privateKey = RSA.import_key(private_key)
        cipher_rsa = PKCS1_v1_5.new(privateKey)
        ciphertext = base64.b64decode(ciphertext_b64)
        decrypted = cipher_rsa.decrypt(ciphertext, None)
        try:
            return decrypted.decode('utf-8')
        except UnicodeDecodeError:
            # 如果解码失败，可能是原始数据不是字符串类型
            return decrypted

    # 使用MD5和AES进行加密解密
    def secret(self, string, code, operation=False):
        if isinstance(string, dict):
            string = json.dumps(string)
            string = str(string).replace(': ', ':')
        md5 = hashlib.md5()
        md5.update(code.encode('utf-8'))
        code_hash = md5.hexdigest()
        iv = code_hash[:16].encode('utf-8')
        key = code_hash[16:].encode('utf-8')

        if operation:  # 解密
            cipher = AES.new(key, AES.MODE_CBC, iv=iv)
            decrypted = unpad(cipher.decrypt(b64decode(string)), AES.block_size)
            return decrypted.decode('utf-8')
        else:  # 加密
            cipher = AES.new(key, AES.MODE_CBC, iv=iv)
            encrypted = cipher.encrypt(pad(string.encode(), AES.block_size))
            return base64.b64encode(encrypted).decode('utf-8')

    # 数据解密
    def decrypt(self, data):
        if data['ak']:
            plaintext = self.rd(data['ak'], self.sfile)
            obj = self.secret(data['ed'], plaintext, True)
            return json.loads(obj)
        else:
            return data


class Request:
    def __init__(self):
        self.utils = Utils()
        self.random_string = ''.join(random.sample(string.ascii_letters + string.digits, 16))
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/x-www-form-urlencoded",
            "token": fm_token,
            "Host": "fmpapi.feimaoyun.com",
            # 2026-09-07 起服务端在鉴权前校验 APP 原生请求头，缺失即返回
            # 40100「设备错误，请重试」。以下头集合为 v4.00.74 原生 okhttp 实抓值：
            # devicetoken 与机型字符串实测不与账号绑定（占位即可），par/pto 仍用抓包值。
            "User-Agent": "FeemooApp/Android15 v4.00.74/Xiaomi 22061218C",
            "os": "android",
            "device-name": "Xiaomi++22061218C",
            "app-ver": "4.00.74",
            "network-type": "WiFi",
            "os-ver": "Android+15",
            "fmver": "170",
            "plat": "feemoo",
            "devicetoken": "0000000000000000000",
            "fm-lang": "zh-CN",
            "par": self.utils.par,
            "pto": self.utils.pto,
        })

    def get(self, url, params=None):
        try:
            response = self.session.get(url, params=params).json()
            if response['status'] == 1:
                return self.utils.decrypt(response['data'])
            else:
                print(response['msg'])
                return None
        except Exception as e:
            print(f"{url}请求失败: {e}")
            return None

    def post(self, url, data=None):
        try:
            if data == {}:
                body = ''
            else:
                if data is not None:
                    data = self.utils.in_parameter(data)
                    data = json.loads(data)
                body = f"jt={quote(data['jt'])}&sn={quote(data['sn'])}"
            response = self.session.post(url, data=body).json()
            if response['status'] == '1':
                # print(response['msg'])
                # print(self.utils.decrypt(response['data']))
                return self.utils.decrypt(response['data'])
            else:
                return response
        except Exception as e:
            print(f"{url}请求失败: {e}")
            return None


class MiniRequest:
    """小程序协议客户端：只用 mini_keys.json，不与 APP Request 混用密钥。"""

    BASE = FM_API_BASE_MINI

    def __init__(self, token=""):
        # 强制小程序密钥
        self.utils = Utils(keys_file=MINI_KEYS_FILE)
        # 优先用登录 JWT 内 device_id，避免与 APP 设备不一致
        device_id = self._device_id_from_jwt(token)
        if not device_id:
            device_id = ''.join(random.choices(string.ascii_letters + string.digits, k=33))
        self.utils.device_key = device_id
        self.utils.dataa = '{"device_key":"' + device_id + '"}'
        self.utils.par = self.utils.secret(self.utils.dataa, self.utils.ak)
        self.utils.pto = self.utils.re(self.utils.ak, self.utils.p)
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/x-www-form-urlencoded",
            "token": token or "",
            "Host": "fmpapi.feemoo.com",
            "User-Agent": (
                "Mozilla/5.0 (Linux; Android 12; M2012K11AC) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Version/4.0 Chrome/126.0.0.0 Mobile Safari/537.36 "
                "MicroMessenger/8.0.49"
            ),
            "os": "wx_applet",
            "fmver": "1.1.3",
            # 小程序 header 大小写
            "Par": self.utils.par,
            "Pto": self.utils.pto,
        })

    @staticmethod
    def _device_id_from_jwt(token):
        try:
            if not token or token.count(".") < 2:
                return ""
            payload = token.split(".")[1]
            pad = "=" * (-len(payload) % 4)
            data = json.loads(base64.urlsafe_b64decode(payload + pad).decode("utf-8"))
            return (data.get("data") or {}).get("device_id") or ""
        except Exception:
            return ""

    def set_token(self, token):
        self.session.headers["token"] = token or ""

    def post(self, path, data=None, empty_body=False):
        """
        empty_body=True: 无参接口（小程序 u(undefined)）
        data={} / 有字段: sn/jt 加密
        """
        url = path if path.startswith("http") else f"{self.BASE}{path}"
        try:
            if empty_body or data is None:
                body = ""
            else:
                packed = json.loads(self.utils.in_parameter(data))
                body = f"jt={quote(packed['jt'])}&sn={quote(packed['sn'])}"
            response = self.session.post(url, data=body, timeout=30).json()
            if str(response.get("status")) == "1":
                raw = response.get("data")
                if isinstance(raw, dict) and raw.get("ak") and raw.get("ed"):
                    return self.utils.decrypt(raw)
                return raw if raw is not None else {}
            return response
        except Exception as e:
            print(f"{url}请求失败: {e}")
            return None


class Function:
    def __init__(self):
        self.utils = Utils()
        self.request = Request()

    def get_uid(self):
        uid = 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'
        result = []
        for char in uid:
            if char == 'x':
                result.append(f'{random.randint(0, 15):x}')
            elif char == 'y':
                result.append(f'{random.randint(8, 11):x}')
            else:
                result.append(char)
        return ''.join(result)

    def get_str(self, n):
        chars = 'qwertyuiopasdfghjklzxcvb1234567890'
        v = ''
        for _ in range(n):
            R_id = random.randint(0, len(chars) - 1)
            v += chars[R_id]
        return v

    # 获取APP版本
    def get_version(self):
        getVersionRes = self.request.post("https://fmpapi.feimaoyun.com/user-service/common/getAppUpdateInfo", {})
        server_version = getVersionRes['server_version']
        self.request.session.headers.update({
            "fmver": server_version,
        })
        # app-ver / User-Agent 的版本号同步为服务端最新版本，避免日后 APP 升级后
        # 头校验收紧再次出现 40100（new_version 形如 "V4.00.74"）
        new_version = str(getVersionRes.get('new_version') or '').lstrip('Vv')
        if new_version:
            headers = self.request.session.headers
            headers['app-ver'] = new_version
            headers['User-Agent'] = re.sub(r'v[\d.]+/', 'v%s/' % new_version, headers['User-Agent'])

    # 获取用户信息
    def get_user_info(self):
        userInfoRes = self.request.post("https://fmpapi.feimaoyun.com/user-service/user/info", {})
        # userInfo = userInfoRes['data']
        userId = userInfoRes['user_id']
        print(f"【获取用户信息】用户ID：{userId}")
        return userId

    # 获取视频奖励
    def reward_video(self, aid, user_id):
        url = 'https://api-access.pangolin-sdk-toutiao.com/api/ad/union/mediation/reward_video/reward/'
        headers = {
            "Host": "api-access.pangolin-sdk-toutiao.com",
            'user-agent': 'Dalvik/2.1.0 (Linux; U; Android 12; zh-CN; M2012K11AC Build/SKQ1.220303.001)',
            'Content-Type': 'application/json; charset=utf-8',
            'accept-encoding': 'gzip'
        }
        keyA = self.get_str(8)
        keyB = self.get_str(8)
        transId = self.get_uid()
        linkId = self.get_uid()
        timeMs = int(time.time() * 1000)
        bodyDict = {
            "sdk_version": "4.2.0.3",
            "user_agent": "Dalvik/2.1.0 (Linux; U; Android 12; zh-CN; M2012K11AC Build/SKQ1.220303.001)",
            "network": 1,
            "play_start_ts": timeMs - 20000,
            "play_end_ts": timeMs,
            "user_id": user_id,
            "trans_id": f"{transId}",
            "link_id": f"{linkId}",
            "prime_rit": "102375589",
            "adn_rit": "952723628",
            "reward_name": "",
            "reward_amount": 0,
            "media_extra": f'{{\"os\": \"Android\", \"aid\": \"{aid}\", \"version\": 110}}',
            "adn_name": "pangle",
            "ecpm": "0.0"
        }
        bodyStr = json.dumps(bodyDict)
        body = f'{{"message":"2{keyA}{keyB}{self.utils.ae(bodyStr, keyB + keyA)}","cypher":2}}'
        response = requests.post(url, headers=headers, data=body)
        responseData = response.json()
        return responseData

    # 看广告（旧链路：abTaskInfo + 伪造穿山甲回调。2026-09 起广告奖励已迁移
    # 微信小程序任务，abTaskInfo 不再下发 aid，此链路已停用，仅保留代码备查）
    def watch_ad(self):
        user_id = self.get_user_info()
        while True:
            abTaskInfoRes = self.request.post("https://fmpapi.feimaoyun.com/user-service/welfare/abTaskInfo", {})
            if 'aid' in abTaskInfoRes:
                print(f'【请求广告返回】剩余广告次数：{abTaskInfoRes["count"]}，获得福利点：{abTaskInfoRes["ad_point"]}点')
                adAid = abTaskInfoRes['aid']
                self.reward_video(adAid, user_id)
            else:
                print(f'【请求广告返回】{abTaskInfoRes["msg"]}')
                break

    # ---------- 微信小程序看视频链路（迁移自 dev 分支，复用 fm_token，无需 OCR/登录） ----------

    def _app_post_packed(self, url, payload):
        """APP 协议 POST，始终发送 jt/sn 加密体（Request.post 对 {} 走空 body 捷径）。"""
        try:
            packed = json.loads(self.request.utils.in_parameter(payload))
            body = f"jt={quote(packed['jt'])}&sn={quote(packed['sn'])}"
            resp = self.request.session.post(url, data=body, timeout=30).json()
            if str(resp.get("status")) == "1":
                return self.request.utils.decrypt(resp["data"])
            return resp
        except Exception as e:
            print(f"{url}请求失败: {e}")
            return None

    @staticmethod
    def _is_biz_error(res):
        """
        识别 MiniRequest 返回的「接口失败」整包（含 status/msg）。
        注意：成功时 decrypt 后的业务 data 也可能带 status 字段
        （如 getAppTaskInfo 的任务状态 0=未完成），不能当失败。
        """
        if not isinstance(res, dict):
            return False
        # 成功业务体特征
        if "aid" in res or "current_progress" in res or "max_progress" in res:
            return False
        if "status" not in res:
            return False
        # 接口失败包一般有 msg；纯业务 data 往往没有 msg
        if "msg" not in res and "showMsg" not in res:
            return False
        return str(res.get("status")) != "1"

    def _simulate_watch(self):
        """模拟看视频耗时：随机 15-20 秒（环境变量 FM_WATCH_DELAY=秒数 可覆盖，如 15-20 / 15 / 20-30）。"""
        spec = (os.environ.get("FM_WATCH_DELAY") or "15-20").strip()
        try:
            if "-" in spec:
                lo, hi = spec.split("-", 1)
                delay = random.uniform(float(lo), float(hi))
            else:
                delay = float(spec)
        except (TypeError, ValueError):
            delay = random.uniform(15, 20)
        print(f"【模拟看视频】等待 {delay:.1f}s")
        time.sleep(delay)

    @staticmethod
    def _parse_jump_token(jump_token):
        """解析 jumpToken 明文与是否过期。返回 (info_dict|None, expired:bool)"""
        try:
            part = jump_token.split(".", 1)[0]
            pad = "=" * (-len(part) % 4)
            info = json.loads(base64.b64decode(part + pad).decode("utf-8"))
            exp = int(info.get("exp_time") or 0)
            expired = bool(exp and exp < int(time.time()))
            return info, expired
        except Exception:
            return None, False

    def _fetch_jump_token_from_api(self):
        """
        从 APP taskInfoV2 取服务端下发的 video_ad_task_token。
        反编译确认：BenefitsNewFragment.j1 type=10 时
          openGetPoint(ctx, BenefitsNewBean.video_ad_task_token)
        客户端不本地签名。
        """
        res = self._app_post_packed(
            "https://fmpapi.feimaoyun.com/user-service/welfare/taskInfoV2", {}
        )
        if not isinstance(res, dict):
            return "", ""
        if self._is_biz_error(res):
            print(f"【APP看视频】taskInfoV2：{res.get('msg')}")
            return "", ""
        tok = res.get("video_ad_task_token") or ""
        daily = res.get("daily_tasks") or {}
        if isinstance(daily, dict) and daily:
            print(
                f"【APP看视频】taskInfoV2 任务 "
                f"{daily.get('status_name') or daily.get('desc_txt') or ''} "
                f"status={daily.get('status')}"
            )
        if tok:
            return str(tok).strip(), "taskInfoV2"
        return "", "taskInfoV2(empty)"

    def _resolve_jump_token(self):
        """
        APP→小程序 jumpToken（body 里的 token，不是登录 JWT）。
        形态: base64(json).签名 ；json 含 os/userid/exp_time/type=ad_task

        来源（优先服务端）:
        1) taskInfoV2.video_ad_task_token（APP 正式路径）
        2) FM_JUMP_TOKEN / fm_jump_token
        3) jump_token.txt / fm_jump_token.txt
        """
        tok, src = self._fetch_jump_token_from_api()
        if tok:
            return tok, src
        for k in ("FM_JUMP_TOKEN", "fm_jump_token", "FM_APP_JUMP_TOKEN"):
            v = (os.environ.get(k) or "").strip()
            if v:
                return v, "env"
        for name in ("jump_token.txt", "fm_jump_token.txt"):
            if os.path.exists(name):
                with open(name, "r", encoding="utf-8") as f:
                    v = f.read().strip()
                if v:
                    return v, name
        return "", src or ""

    # 小程序首页看视频（无 jumpToken；密钥 mini_keys.json）
    def watch_mini_ad(self):
        token = (os.environ.get("FM_MINI_TOKEN") or fm_token or "").strip()
        if not token:
            print("【小程序看视频】缺少登录 token（fm_token）")
            return
        print(f"【小程序看视频】登录 token 来源: fm_token（密钥: {MINI_KEYS_FILE}）")

        mini = MiniRequest(token=token)
        task_path = "/user-service/welfare/appletTaskInfo"
        claim_path = "/user-service/welfare/appletTaskCallback"

        while True:
            taskRes = mini.post(task_path, empty_body=True)
            if taskRes is None:
                print("【小程序看视频】创建任务失败")
                break
            if self._is_biz_error(taskRes):
                print(f"【小程序看视频】{taskRes.get('msg')}")
                break
            if not isinstance(taskRes, dict) or "aid" not in taskRes:
                print(f"【小程序看视频】创建任务异常：{taskRes}")
                break

            aid = taskRes.get("aid")
            count = taskRes.get("count", "?")
            ad_point = taskRes.get("ad_point", "?")
            print(f"【小程序看视频】创建任务成功 aid={aid}，剩余次数：{count}，单次福利点：{ad_point}")

            self._simulate_watch()

            claimRes = mini.post(claim_path, {"task_id": str(aid)})
            if claimRes is None:
                print("【小程序看视频】领取失败")
                break
            if self._is_biz_error(claimRes):
                print(f"【小程序看视频】领取失败：{claimRes.get('msg')}")
                break
            print(f"【小程序看视频】领取成功，获得 {ad_point} 福利点")
            time.sleep(0.5)

    # APP 专属看视频（jumpToken + getAppTaskInfo + appletTaskInfo + appletTaskCallback）
    def watch_app_ad(self):
        login_token = (os.environ.get("FM_MINI_TOKEN") or fm_token or "").strip()
        jump_token, jump_src = self._resolve_jump_token()
        if not login_token:
            print("【APP看视频】缺少登录 JWT（fm_token）")
            return
        if not jump_token:
            print(
                "【APP看视频】未拿到 video_ad_task_token"
                f"（来源尝试: {jump_src or '无'}）。"
                "常见原因：今日次数已满、福利开关关闭，或 token 文件未配置。"
            )
            return

        info, expired = self._parse_jump_token(jump_token)
        if info:
            print(
                f"【APP看视频】jumpToken 来源: {jump_src}，"
                f"userid={info.get('userid')} type={info.get('type')} "
                f"exp_time={info.get('exp_time')} expired={expired}"
            )
        else:
            print(f"【APP看视频】jumpToken 来源: {jump_src}（无法解析明文）")
        if expired:
            print("【APP看视频】jumpToken 已过期，请在 APP 重新点「看视频」抓取新 token")
            return
        print(f"【APP看视频】登录 JWT 来源: fm_token（密钥: {MINI_KEYS_FILE}）")

        mini = MiniRequest(token=login_token)
        # 可选校验
        validate = mini.post("/user-service/common/jumpAppletValidate", {"token": jump_token})
        if self._is_biz_error(validate):
            print(f"【APP看视频】jumpAppletValidate 失败：{validate.get('msg')}")
            return

        # 可选限制次数，避免一次刷满（测试用：set FM_APP_AD_MAX=1）
        try:
            max_claim = int((os.environ.get("FM_APP_AD_MAX") or "0").strip() or "0")
        except ValueError:
            max_claim = 0

        claimed = 0
        while True:
            if max_claim > 0 and claimed >= max_claim:
                print(f"【APP看视频】已达 FM_APP_AD_MAX={max_claim}，停止")
                break
            detail = mini.post("/user-service/welfare/getAppTaskInfo", {"token": jump_token})
            if detail is None:
                print("【APP看视频】getAppTaskInfo 请求/解密失败")
                break
            if self._is_biz_error(detail):
                print(f"【APP看视频】getAppTaskInfo：{detail.get('msg') or detail}")
                break
            if not isinstance(detail, dict):
                print(f"【APP看视频】getAppTaskInfo 异常：{detail}")
                break
            # 成功体应含进度；若没有则打印原文便于排查
            if "current_progress" not in detail and "max_progress" not in detail:
                print(f"【APP看视频】getAppTaskInfo 无进度字段：{detail}")
                break

            cur = detail.get("current_progress")
            mx = detail.get("max_progress")
            point = detail.get("point", "?")
            # detail.status 是任务状态(0未完成/1进行中/2已完成)，不是 HTTP 业务码
            print(
                f"【APP看视频】进度 {cur}/{mx}，单次福利点：{point}，"
                f"任务status={detail.get('status')}"
            )
            try:
                if cur is not None and mx is not None and int(cur) >= int(mx):
                    print("【APP看视频】今日次数已满")
                    break
            except (TypeError, ValueError):
                pass

            taskRes = mini.post("/user-service/welfare/appletTaskInfo", {"token": jump_token})
            if taskRes is None:
                print("【APP看视频】创建任务失败")
                break
            if self._is_biz_error(taskRes):
                print(f"【APP看视频】创建任务：{taskRes.get('msg')}")
                break
            if not isinstance(taskRes, dict) or "aid" not in taskRes:
                print(f"【APP看视频】创建任务异常：{taskRes}")
                break

            aid = taskRes.get("aid")
            ad_point = taskRes.get("ad_point", point)
            count = taskRes.get("count", "?")
            print(f"【APP看视频】创建任务成功 aid={aid}，剩余：{count}，单次：{ad_point}")

            self._simulate_watch()

            claimRes = mini.post(
                "/user-service/welfare/appletTaskCallback",
                {"task_id": str(aid), "token": jump_token},
            )
            if claimRes is None:
                print("【APP看视频】领取失败")
                break
            if self._is_biz_error(claimRes):
                # 兼容仅 task_id
                claimRes = mini.post(
                    "/user-service/welfare/appletTaskCallback",
                    {"task_id": str(aid)},
                )
            if claimRes is None or self._is_biz_error(claimRes):
                msg = claimRes.get("msg") if isinstance(claimRes, dict) else claimRes
                print(f"【APP看视频】领取失败：{msg}")
                break

            claimed += 1
            msg = claimRes.get("msg") if isinstance(claimRes, dict) else ""
            print(f"【APP看视频】领取成功（第{claimed}次）{('：' + msg) if msg else f' +{ad_point}'}")
            time.sleep(0.5)

        if claimed:
            print(f"【APP看视频】本轮领取 {claimed} 次")
            try:
                detail = mini.post("/user-service/welfare/getAppTaskInfo", {"token": jump_token})
                if isinstance(detail, dict) and not self._is_biz_error(detail):
                    print(
                        f"【APP看视频】最终进度 "
                        f"{detail.get('current_progress')}/{detail.get('max_progress')}"
                    )
            except Exception:
                pass
            try:
                user = self.request.post(
                    "https://fmpapi.feimaoyun.com/user-service/user/info", {}
                )
                if isinstance(user, dict) and (
                    user.get("point") is not None or user.get("point_int") is not None
                ):
                    print(
                        f"【APP看视频】当前积分：{user.get('point') or user.get('point_int')}"
                    )
            except Exception:
                pass

    # 签到
    def signin(self):
        signinRes = self.request.post("https://fmpapi.feimaoyun.com/user-service/welfare/signInApp", {})
        if 'msg' in signinRes:
            print(f'【APP签到】{signinRes["msg"]}')
            if '请先登录' in signinRes['msg']:
                # 账号过期，推送消息
                if PUSHPLUS_TOKEN:
                    self.push_message()
                else:
                    print('【推送】未填写PushPlus的token，不进行推送')
                return
        else:
            print(f'【APP签到】连续签到天数：{signinRes["sigcount"]}，获得福利点：{signinRes["add"]}点')

    # 超级签到
    def super_signin(self):
        superSigninData = {"aid": "8197906"}
        while True:
            superSigninRes = self.request.post("https://fmpapi.feimaoyun.com/user-service/welfare/signInSuper",
                                               superSigninData)
            print(f'超级签到：{superSigninRes}')

    # 任务详情
    def task_info(self):
        taskInfoRes = self.request.post("https://fmpapi.feimaoyun.com/user-service/welfare/taskInfo", {})
        if 'msg' in taskInfoRes:
            print(f'【任务详情】{taskInfoRes["msg"]}')
            return
        energyBallList = taskInfoRes['energyBall_list']
        for energyBall in energyBallList:
            taskId = energyBall['id']
            receiveEnergyBallData = {"task_id": taskId}
            # 领取能量球
            self.receive_energy_ball(receiveEnergyBallData)

    # 领取能量球
    def receive_energy_ball(self, data):
        try:
            receiveEnergyBallRes = self.request.post(
                "https://fmpapi.feimaoyun.com/user-service/welfare/receiveWelfarePoints", data)
            print(f'【领取】{receiveEnergyBallRes["point_txt"]}点数，总点数：{receiveEnergyBallRes["point"]}')
            time.sleep(0.2)
        except Exception as e:
            print(f"领取失败: {e}")

    # pushplus推送
    def push_message(self):
        url = 'http://www.pushplus.plus/send'
        headers = {
            "Content-Type": "application/json"
        }
        body = {
            "token": PUSHPLUS_TOKEN,
            "title": "飞猫盘账号过期提醒",
            "content": f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time()))}已过期\n",
        }
        response = requests.post(url, headers=headers, json=body)
        res_json = response.json()
        print(f"【pushplus推送】{res_json['msg']}")
        exit(1)


class Run:

    def __init__(self):
        self.function = Function()

    def run(self):
        # 获取APP最新版本
        self.function.get_version()
        # 签到
        self.function.signin()
        # # 超级签到
        # self.function.super_signin(superSigninData)
        # 看视频（微信小程序链路；旧 watch_ad 链路已停用，保留代码不再调用）
        self.function.watch_app_ad()
        self.function.watch_mini_ad()
        # 任务详情
        self.function.task_info()


if __name__ == '__main__':
    run = Run()
    run.run()
