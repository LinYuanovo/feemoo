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
DDDD_SLIDE_URL = os.environ.get("DDDD_SLIDE_URL") or "" # DDDD滑块验证码识别服务地址

if fm_account == "" or DDDD_SLIDE_URL == "":
    print("请设置环境变量fm_account和DDDD_SLIDE_URL")
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
        self.pto = self.re(self.ak, self.p)
        self.device_token, self.device_key = self.read_device()
        self.dataa = '{"device_key":"' + self.device_key + '"}'
        self.par = self.secret(self.dataa, self.ak)
        # pto和par写死即可，并不校验
        # self.pto = fm_pto
        # self.par = fm_par

    # 读取设备token和key
    def read_device(self):
        if os.path.exists('device.txt'):
            with open('device.txt', 'r') as f:
                device_token = f.readline().strip()
                device_key = f.readline().strip()
                return device_token, device_key
        else:
            # 生成一个随机device_token和device_key
            device_token = ''.join(random.choices(string.ascii_letters + string.digits, k=16))
            device_key = ''.join(random.choices(string.ascii_letters + string.digits, k=33))
            with open('device.txt', 'w') as f:
                f.write(f"{device_token}\n{device_key}")
            print(f"【生成设备token和key】{device_token} {device_key} 已保存到device.txt")
            return device_token, device_key

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
            "token": "",
            "devicetoken": self.utils.device_token,
            "Host": "fmpapi.feimaoyun.com",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0",
            "os": "android",
            "fmver": "116",
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
        self.new_version = ""
        self.account = fm_account.split('&')[0]
        self.password = fm_account.split('&')[1]
        # 添加geetest相关配置
        self.geetest_captcha_id = "36df6e46b1d10baf1858267b6f468a63"  # 飞猫盘的geetest captcha_id
        self.geetest_js_url = "https://static.geetest.com/v4/static/v1.8.9-2b7f0f/js/gcaptcha4.js"

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
        self.new_version = getVersionRes['new_version']
        self.request.session.headers.update({
            "fmver": server_version,
        })
        return getVersionRes

    # 获取geetest验证码
    def get_geetest_captcha(self):
        try:
            # 导入geetest crack模块
            import sys
            import os
            sys.path.append(os.path.join(os.path.dirname(__file__), 'geetest-v4-slide-crack'))
            from crack import Crack
            crack = Crack(self.geetest_captcha_id, self.geetest_js_url, DDDD_SLIDE_URL)
            crack.load()
            res = crack.verify()

            if res.get('status') == 'success':
                if res['data']['result'] == 'success':
                    return res['data']['seccode']
                else:
                    return self.get_geetest_captcha()
            else:
                print(f"【geetest验证失败】{res}")
                return None
        except Exception as e:
            print(f"【geetest验证异常】{e}")
            return None

    # 登录验证
    def login_verify(self):
        url = "https://fmpapi.feimaoyun.com/user-service/passport/userLoginVerify"
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
    def login(self):
        url = "https://fmpapi.feimaoyun.com/user-service/passport/login"

        # 获取geetest验证码
        captcha_data = self.get_geetest_captcha()
        if not captcha_data:
            print("【登录】获取验证码失败")
            return False

        # 获取用户ID
        user_id = self.login_verify()
        if not user_id:
            print("【登录】获取用户ID失败")
            return False

        body = {
            "logintp": "phone",
            "app_version": self.new_version,
            "gen_time": captcha_data['gen_time'],
            "captcha_output": captcha_data['captcha_output'],
            "lot_number": captcha_data['lot_number'],
            "network": "WiFi",
            "password": self.password,
            "device_name": "Redmi  M2012K11AC",
            "sys_version": "Android 12",
            "user_id": user_id,
            "device_alias_name": "HUAWEI P40",
            "pass_token": captcha_data['pass_token'],
            "username": self.account
        }
        try:
            loginRes = self.request.post(url, body)
            token = loginRes.get('token', '')
            if loginRes.get('token', ''):
                print(f"【登录】成功")
                # 写入token
                self.write_token(token)
                return loginRes
            else:
                print(f"【登录】{loginRes}")
                return False
        except Exception as e:
            print(f"【登录失败】{e}")
            return False

    # token写入文件
    def write_token(self, token):
        # 如果文件不存在，创建文件
        if not os.path.exists('fm_token.txt'):
            with open('fm_token.txt', 'w') as f:
                f.write(token)
        else:
            with open('fm_token.txt', 'w') as f:
                f.write(token)

    # token读取文件
    def read_token(self):
        if os.path.exists('fm_token.txt'):
            with open('fm_token.txt', 'r') as f:
                return f.read()
        else:
            return None

    # 获取用户信息
    def get_user_info(self):
        userInfoRes = self.request.post("https://fmpapi.feimaoyun.com/user-service/user/info", {})
        # print(f"【获取用户信息】{userInfoRes}")
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

    def do_task(self):
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

    def run(self):
        # 获取APP最新版本
        self.function.get_version()
        # 读取token
        token = self.function.read_token()
        # 检查token是否过期
        if token:
            print('【读取token文件】存在，尝试执行任务')
            self.function.request.session.headers.update({
                "token": token
            })
            self.do_task()
        else:
            print('【读取token文件】不存在，尝试登录')
            # 登录
            loginRes = self.function.login()
            if loginRes:
                self.function.request.session.headers.update({
                    "token": loginRes['token']
                })
                # 执行任务
                self.do_task()

if __name__ == '__main__':
    run = Run()
    run.run()
