from crack import Crack
import time
crack = Crack("5b0562a75492b420626b745b229e83a4", "https://static.geetest.com/v4/static/v1.8.9-2b7f0f/js/gcaptcha4.js")
# crack = Crack("d7e4dfd8691a3cf54ab4df96787c4fef", "https://static.geetest.com/v4/static/v1.8.7-0a36ba/js/gcaptcha4.js") # 第二个参数须填入最新的JS地址

crack.load()
# time.sleep(1) 似乎等不等都行
res = crack.verify()
print(res)
