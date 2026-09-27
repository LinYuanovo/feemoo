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

if fm_token is None:
    print("请设置环境变量fm_token")
    exit(1)


class Utils:
    def __init__(self):
        self.bits = 2048
        self.key_pair = RSA.generate(self.bits)
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

    # 看广告
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
        # 看广告
        self.function.watch_ad()
        # 任务详情
        self.function.task_info()


if __name__ == '__main__':
    run = Run()
    run.run()
