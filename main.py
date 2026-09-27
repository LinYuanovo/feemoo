import base64
import hashlib
import random
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

fm_account = os.environ.get("fm_account") or ""
PUSHPLUS_TOKEN = os.environ.get("PUSHPLUS_TOKEN") or ""
DDDD_API_BASE = (os.environ.get("DDDD_API_BASE") or "").strip()
DDDD_SLIDE_URL = (os.environ.get("DDDD_SLIDE_URL") or "").strip()  # 兼容旧变量
GEETEST_CAPTCHA_ID = (os.environ.get("GEETEST_CAPTCHA_ID") or "36df6e46b1d10baf1858267b6f468a63").strip()
GEETEST_RISK_TYPE = (os.environ.get("GEETEST_RISK_TYPE") or "slide").strip()
GEETEST_MAX_RETRY = int(os.environ.get("GEETEST_MAX_RETRY") or "3")

# 现网 API（APK 硬编码 fmpapi.feemoo.com；历史 feimaoyun 已废弃）
FM_API_HOST = (os.environ.get("FM_API_HOST") or "fmpapi.feemoo.com").strip()
FM_API_BASE = (os.environ.get("FM_API_BASE") or f"https://{FM_API_HOST}").rstrip("/")

# 多账号会话缓存（替代 fm_token.txt / device.txt）
SESSIONS_FILE = (os.environ.get("FM_SESSIONS_FILE") or "sessions.json").strip()


def parse_accounts(raw: str) -> list:
    """
    解析多账号。分隔符：换行 或 @；单账号格式 user&password。
    例:
      user1&pass1
      user2&pass2
      user1&pass1@user2&pass2
    """
    if not raw or not str(raw).strip():
        return []
    text = str(raw).replace("\r\n", "\n").replace("\r", "\n").strip()
    chunks = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        if "@" in line:
            for part in line.split("@"):
                part = part.strip()
                if part:
                    chunks.append(part)
        else:
            chunks.append(line)
    accounts = []
    seen = set()
    for c in chunks:
        if "&" not in c:
            print(f"【账号格式错误】跳过（需 user&password）: {c[:20]}")
            continue
        user, password = c.split("&", 1)
        user, password = user.strip(), password.strip()
        if not user or not password:
            continue
        if user in seen:
            continue
        seen.add(user)
        accounts.append({"username": user, "password": password})
    return accounts


def _require_runtime_env():
    accounts = parse_accounts(fm_account)
    if not accounts or (DDDD_API_BASE == "" and DDDD_SLIDE_URL == ""):
        print(
            "请设置环境变量 fm_account（支持多账号：换行或 @ 分隔，每项 user&password）"
            " 以及 DDDD_API_BASE（或旧版 DDDD_SLIDE_URL）"
        )
        raise SystemExit(1)
    return accounts


class AccountStore:
    """sessions.json：按 username 隔离 token / device，避免多账号串号。"""

    def __init__(self, path=None):
        self.path = path or SESSIONS_FILE
        self.data = {"version": 1, "accounts": {}}
        self._load()
        self._migrate_legacy()

    def _load(self):
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict) and isinstance(raw.get("accounts"), dict):
                self.data = raw
                self.data.setdefault("version", 1)
        except Exception as e:
            print(f"【sessions】读取失败 {self.path}: {e}，将重建")

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def _migrate_legacy(self):
        """一次性把 fm_token.txt / device.txt 迁入 sessions（不删旧文件）。"""
        if self.data.get("accounts"):
            return
        token = ""
        if os.path.exists("fm_token.txt"):
            try:
                token = open("fm_token.txt", encoding="utf-8").read().strip()
            except Exception:
                token = ""
        device_token, device_key = "", ""
        if os.path.exists("device.txt"):
            try:
                lines = open("device.txt", encoding="utf-8").read().splitlines()
                device_token = (lines[0] if lines else "").strip()
                device_key = (lines[1] if len(lines) > 1 else "").strip()
            except Exception:
                pass
        if not token and not device_token:
            return
        # 挂到第一个配置账号上
        accs = parse_accounts(fm_account)
        key = accs[0]["username"] if accs else "_legacy"
        entry = self.data["accounts"].setdefault(key, {"username": key})
        if token and not entry.get("token"):
            entry["token"] = token
        if device_token and not entry.get("device_token"):
            entry["device_token"] = device_token
        if device_key and not entry.get("device_key"):
            entry["device_key"] = device_key
        entry["updated_at"] = int(time.time())
        self.save()
        print(f"【sessions】已从旧 txt 迁移缓存 → {self.path}（账号键: {key}）")

    def get(self, username: str) -> dict:
        acc = self.data["accounts"].setdefault(username, {"username": username})
        return acc

    def update(self, username: str, **fields):
        acc = self.get(username)
        for k, v in fields.items():
            if v is not None:
                acc[k] = v
        acc["username"] = username
        acc["updated_at"] = int(time.time())
        self.save()

    def ensure_device(self, username: str) -> tuple:
        acc = self.get(username)
        dt = (acc.get("device_token") or "").strip()
        dk = (acc.get("device_key") or "").strip()
        if dt and dk:
            return dt, dk
        dt = "".join(random.choices(string.ascii_letters + string.digits, k=16))
        dk = "".join(random.choices(string.ascii_letters + string.digits, k=33))
        self.update(username, device_token=dt, device_key=dk)
        print(f"【sessions】为 {username} 生成 device")
        return dt, dk

class Utils:
    # APP 密钥文件（与小程序 mini_keys.json 不是同一套，禁止混用）
    APP_KEYS_FILE = "app_keys.json"
    MINI_KEYS_FILE = "mini_keys.json"

    def __init__(self, keys_file=None, device_token=None, device_key=None):
        self.bits = 2048
        self.key_pair = RSA.generate(self.bits)
        # keys_file:
        #   app_keys.json  -> APP 登录/签到/能量球
        #   mini_keys.json -> 仅小程序看视频链路（由 MiniRequest 使用）
        keys_file = keys_file or self.APP_KEYS_FILE
        self.keys_file = keys_file
        keys = self.load_keys(keys_file)
        self.pfile = keys["pfile"]
        self.sfile = keys["sfile"]
        self.p = keys["p"]
        self.ak = self.genak()
        self.ed = self.re(self.ak, self.pfile)
        self.pto = self.re(self.ak, self.p)
        self.device_token, self.device_key = self._resolve_device(device_token, device_key)
        self.dataa = '{"device_key":"' + self.device_key + '"}'
        self.par = self.secret(self.dataa, self.ak)

    @staticmethod
    def load_keys(keys_file):
        """从合并后的密钥 JSON 读取 p / pfile / sfile。"""
        if not os.path.exists(keys_file):
            raise FileNotFoundError(
                f"密钥文件不存在: {keys_file}。"
                f"APP 用 {Utils.APP_KEYS_FILE}，小程序用 {Utils.MINI_KEYS_FILE}"
            )
        with open(keys_file, "r", encoding="utf-8") as f:
            keys = json.load(f)
        for k in ("p", "pfile", "sfile"):
            if not keys.get(k):
                raise ValueError(f"密钥文件 {keys_file} 缺少字段: {k}")
        return keys

    @staticmethod
    def _resolve_device(device_token=None, device_key=None):
        """优先用入参（来自 sessions.json）；否则临时随机（不写 txt）。"""
        dt = (device_token or "").strip()
        dk = (device_key or "").strip()
        if dt and dk:
            return dt, dk
        dt = "".join(random.choices(string.ascii_letters + string.digits, k=16))
        dk = "".join(random.choices(string.ascii_letters + string.digits, k=33))
        return dt, dk

    def apply_device(self, device_token, device_key):
        """切换账号设备指纹并刷新 par。"""
        self.device_token = device_token
        self.device_key = device_key
        self.dataa = '{"device_key":"' + self.device_key + '"}'
        self.par = self.secret(self.dataa, self.ak)

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
    def __init__(self, device_token=None, device_key=None):
        self.utils = Utils(device_token=device_token, device_key=device_key)
        self.random_string = ''.join(random.sample(string.ascii_letters + string.digits, 16))
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/x-www-form-urlencoded",
            "token": "",
            "devicetoken": self.utils.device_token,
            "Host": FM_API_HOST,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0",
            "os": "android",
            "fmver": "116",
            "par": self.utils.par,
            "pto": self.utils.pto,
        })

    def apply_device(self, device_token, device_key):
        self.utils.apply_device(device_token, device_key)
        self.session.headers["devicetoken"] = self.utils.device_token
        self.session.headers["par"] = self.utils.par
        self.session.headers["pto"] = self.utils.pto

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

    BASE = FM_API_BASE

    def __init__(self, token=""):
        # 强制小程序密钥
        self.utils = Utils(keys_file=Utils.MINI_KEYS_FILE)
        # 优先用小程序 JWT 内 device_id，避免与 APP device.txt 不一致
        device_id = self._device_id_from_jwt(token)
        if device_id and device_id != self.utils.device_key:
            self.utils.device_key = device_id
            self.utils.dataa = '{"device_key":"' + device_id + '"}'
            self.utils.par = self.utils.secret(self.utils.dataa, self.utils.ak)
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/x-www-form-urlencoded",
            "token": token or "",
            "Host": FM_API_HOST,
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

    def __init__(self, username="", password="", store=None):
        self.store = store or AccountStore()
        self.account = (username or "").strip()
        self.password = (password or "").strip()
        self.user_id = ""
        dt, dk = ("", "")
        if self.account:
            dt, dk = self.store.ensure_device(self.account)
        self.utils = Utils(device_token=dt or None, device_key=dk or None)
        self.request = Request(device_token=dt or None, device_key=dk or None)
        self.new_version = ""
        self.geetest_captcha_id = GEETEST_CAPTCHA_ID
        self.geetest_risk_type = GEETEST_RISK_TYPE

    def bind_account(self, username, password):
        """切换当前处理的账号（设备/token 从 sessions.json 加载）。"""
        self.account = username
        self.password = password
        self.user_id = ""
        dt, dk = self.store.ensure_device(username)
        self.request.apply_device(dt, dk)
        self.utils = self.request.utils
        token = (self.store.get(username).get("token") or "").strip()
        self.request.session.headers["token"] = token
        return token

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
        getVersionRes = self.request.post(f"{FM_API_BASE}/user-service/common/getAppUpdateInfo", {})
        server_version = getVersionRes['server_version']
        self.new_version = getVersionRes['new_version']
        self.request.session.headers.update({
            "fmver": server_version,
        })
        return getVersionRes

    # 获取geetest验证码
    def get_geetest_captcha(self):
        from geeked import Geeked

        last_err = None
        for attempt in range(1, GEETEST_MAX_RETRY + 1):
            try:
                geeked = Geeked(self.geetest_captcha_id, self.geetest_risk_type)
                seccode = geeked.solve()
                if seccode and seccode.get("lot_number") and seccode.get("captcha_output"):
                    return seccode
                last_err = seccode
                print(f"【geetest验证失败】第{attempt}次: {seccode}")
            except Exception as e:
                last_err = e
                print(f"【geetest验证异常】第{attempt}次: {e}")
        print(f"【geetest验证放弃】已重试{GEETEST_MAX_RETRY}次, last={last_err}")
        return None

    # 登录验证
    def login_verify(self):
        url = f"{FM_API_BASE}/user-service/passport/userLoginVerify"
        body = {
            "password": self.password,
            "username": self.account
        }
        try:
            loginVerifyRes = self.request.post(url, body)
            user_id = loginVerifyRes['list'][0]['user_id']
            return user_id
        except Exception as e:
            print(f"【登录验证失败】{e}")
            return False

    # 登录
    def login(self, max_retry=3):
        url = f"{FM_API_BASE}/user-service/passport/login"

        # 获取APP版本(更新 fmver/app_version)
        try:
            self.get_version()
        except Exception as e:
            print(f"【登录】get_version 失败: {e}")

        # 获取用户ID
        user_id = self.login_verify()
        if not user_id:
            print("【登录】获取用户ID失败")
            return False

        def build_body():
            return {
                "logintp": "phone",
                "app_version": self.new_version,
                "network": "WiFi",
                "password": self.password,
                "device_name": "Redmi  M2012K11AC",
                "sys_version": "Android 12",
                "user_id": user_id,
                "device_alias_name": "HUAWEI P40",
                "username": self.account,
            }

        def post_plain(body):
            # 明文 body(不加密 jt/sn), 否则服务器走 90001 H5 路径
            resp = self.request.session.post(url, data=body, timeout=30).json()
            if str(resp.get('status')) == '1':
                try:
                    data = self.request.utils.decrypt(resp['data'])
                except Exception as e:
                    print(f"【登录】解密失败: {e}")
                    return resp
                if isinstance(data, dict) and data.get('token'):
                    data['status'] = '1'
                    return data
                return resp
            return resp

        # 第一次：裸发，服务器返回 90002(极验) 要求验证
        loginRes = post_plain(build_body())
        for attempt in range(1, max_retry + 1):
            if isinstance(loginRes, dict) and loginRes.get('token'):
                print(f"【登录】成功")
                self.write_token(loginRes['token'], user_id=user_id)
                self.request.session.headers["token"] = loginRes['token']
                return loginRes
            if not isinstance(loginRes, dict) or str(loginRes.get('status')) not in ("90001", "90002"):
                print(f"【登录】{loginRes}")
                return False
            print(f"【登录】服务器要求验证({loginRes.get('status')}), 第{attempt}次解验证码...")
            captcha_data = self.get_geetest_captcha()
            if not captcha_data:
                print("【登录】获取验证码失败")
                return False
            # APK: requestCallForGTCaptcha 把验证字段加进 body 重放
            body = build_body()
            body.update({k: captcha_data[k] for k in ("lot_number", "captcha_output", "pass_token", "gen_time")})
            body["accessid"] = captcha_data.get('accessid', '')
            loginRes = post_plain(body)
            if isinstance(loginRes, dict) and loginRes.get('token'):
                print(f"【登录】成功")
                self.write_token(loginRes['token'], user_id=user_id)
                self.request.session.headers["token"] = loginRes['token']
                return loginRes
            print(f"【登录】重放失败: {loginRes}")
        print(f"【登录】已重试{max_retry}次仍失败")
        return False

    def write_token(self, token, user_id=None):
        """写入当前账号的 sessions.json 缓存。"""
        if not self.account:
            return
        fields = {
            "token": token,
            "device_token": self.request.utils.device_token,
            "device_key": self.request.utils.device_key,
        }
        if user_id is not None:
            fields["user_id"] = str(user_id)
            self.user_id = str(user_id)
        self.store.update(self.account, **fields)

    def read_token(self):
        if not self.account:
            return None
        return (self.store.get(self.account).get("token") or "").strip() or None

    # 获取用户信息
    def get_user_info(self):
        userInfoRes = self.request.post(f"{FM_API_BASE}/user-service/user/info", {})
        userId = userInfoRes['user_id']
        print(f"【获取用户信息】用户ID：{userId}")
        self.user_id = str(userId)
        if self.account:
            self.store.update(self.account, user_id=str(userId))
        return userId

    def _resolve_watch_token(self):
        """
        Header 登录 JWT（可与 APP 登录 token 相同）：
        1) 当前 APP 会话 token
        2) sessions.json 当前账号 token
        3) 环境变量 FM_MINI_TOKEN（调试）

        注意：登录 JWT 可复用；加密密钥不能复用（小程序接口必须 mini_keys.json）。
        """
        try:
            token = (self.request.session.headers.get("token") or "").strip()
            if token:
                return token, "app_session"
        except Exception:
            pass
        token = (self.read_token() or "").strip()
        if token:
            return token, "sessions.json"
        token = (os.environ.get("FM_MINI_TOKEN") or os.environ.get("fm_mini_token") or "").strip()
        if token:
            return token, "env"
        return "", ""

    def _fetch_jump_token_from_api(self):
        """
        从 APP taskInfoV2 取服务端下发的 video_ad_task_token。
        反编译确认：BenefitsNewFragment.j1 type=10 时
          openGetPoint(ctx, BenefitsNewBean.video_ad_task_token)
        客户端不本地签名。
        """
        try:
            res = self.request.post(
                f"{FM_API_BASE}/user-service/welfare/taskInfoV2", {}
            )
        except Exception as e:
            print(f"【APP看视频】taskInfoV2 请求失败: {e}")
            return "", ""
        if not isinstance(res, dict):
            return "", ""
        if self._is_biz_error(res):
            print(f"【APP看视频】taskInfoV2：{res.get('msg')}")
            return "", ""
        tok = res.get("video_ad_task_token") or ""
        daily = res.get("daily_tasks") or {}
        if isinstance(daily, dict):
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

    # 小程序首页看视频（无 jumpToken；密钥 mini_keys.json）
    def watch_ad(self):
        mini_token, token_src = self._resolve_watch_token()
        if not mini_token:
            print("【小程序看视频】缺少登录 token。请先 APP 登录，或设置 FM_MINI_TOKEN / mini_token.txt")
            return
        print(f"【小程序看视频】登录 token 来源: {token_src}（密钥: mini_keys.json）")

        mini = MiniRequest(token=mini_token)
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

    # APP 专属看视频（api.md §4.3：jumpToken + getAppTaskInfo + appletTaskInfo + appletTaskCallback）
    def watch_app_ad(self):
        login_token, login_src = self._resolve_watch_token()
        jump_token, jump_src = self._resolve_jump_token()
        if not login_token:
            print("【APP看视频】缺少登录 JWT。请先登录（sessions.json 会缓存 token）")
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
        print(f"【APP看视频】登录 JWT 来源: {login_src}（密钥: mini_keys.json，域名: {FM_API_HOST}）")

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
                    f"{FM_API_BASE}/user-service/user/info", {}
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
        signinRes = self.request.post(f"{FM_API_BASE}/user-service/welfare/signInApp", {})
        if 'msg' in signinRes:
            print(f'【APP签到】{signinRes["msg"]}')
            if '请先登录' in signinRes['msg']:
                print('【token过期】尝试登录')
                # 登录
                loginRes = self.login()
                if loginRes:
                    self.request.session.headers.update({
                        "token": loginRes['token']
                    })
                    # 签到
                    self.signin()
                else:
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
            superSigninRes = self.request.post(f"{FM_API_BASE}/user-service/welfare/signInSuper",
                                               superSigninData)
            print(f'超级签到：{superSigninRes}')

    # 任务详情
    def task_info(self):
        taskInfoRes = self.request.post(f"{FM_API_BASE}/user-service/welfare/taskInfo", {})
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
                f"{FM_API_BASE}/user-service/welfare/receiveWelfarePoints", data)
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
        who = self.account or "未知账号"
        body = {
            "token": PUSHPLUS_TOKEN,
            "title": "飞猫盘账号过期提醒",
            "content": f"{who} @ {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time()))} 已过期\n",
        }
        response = requests.post(url, headers=headers, json=body)
        res_json = response.json()
        print(f"【pushplus推送】{res_json['msg']}")


class Run:

    def __init__(self, accounts=None):
        self.store = AccountStore()
        self.accounts = accounts or parse_accounts(fm_account)
        self.function = Function(store=self.store)

    def do_task(self):
        # 获取APP最新版本
        self.function.get_version()
        # 签到
        self.function.signin()
        # APP 专属看视频（jumpToken 来自 taskInfoV2.video_ad_task_token）
        self.function.watch_app_ad()
        # 小程序首页看视频（无 jumpToken，独立日次数）
        self.function.watch_ad()
        # 任务详情 / 领取能量球（看视频奖励先进 energyBall_list）
        self.function.task_info()

    def run_one(self, username, password):
        print("\n" + "#" * 60)
        print(f"【账号】{username}")
        print("#" * 60)
        token = self.function.bind_account(username, password)
        self.function.get_version()
        if token:
            print("【sessions】存在缓存 token，尝试执行任务")
            try:
                self.do_task()
                return
            except Exception as e:
                print(f"【任务异常】{e}，尝试重新登录")
        print("【sessions】无 token 或任务失败，尝试登录")
        loginRes = self.function.login()
        if loginRes:
            self.do_task()
        else:
            print(f"【账号】{username} 登录失败，跳过")

    def run(self):
        if not self.accounts:
            print("无有效账号")
            return
        print(f"【账号列表】共 {len(self.accounts)} 个")
        for i, acc in enumerate(self.accounts, 1):
            try:
                self.run_one(acc["username"], acc["password"])
            except Exception as e:
                print(f"【账号异常】{acc.get('username')}: {e}")
            if i < len(self.accounts):
                time.sleep(1)

if __name__ == '__main__':
    _accounts = _require_runtime_env()
    run = Run(accounts=_accounts)
    run.run()
