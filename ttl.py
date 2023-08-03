import sys
sys.path.append('/home/mo/lib/python3.10/site-packages')
import logging
import threading
import serial.serialposix
import keyboard
import binascii
import time

# 设置日志格式
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
# 线程锁
lock = threading.Lock()
# 串口通讯
ser = None
# 最大速度
max_speed = 100
# AB点
position_a = None
position_b = None

# 停止运动的事件
STOP = threading.Event()

# 发送命令
def send_command(command,read_size = 4):
    # 线程锁
    with lock:
        # 引用全局变量 ser
        global ser
        # 向串口发送数据
        ser.write(command)
        # 读取返回命令
        return_command_bytes = ser.read(read_size)
        # 比特字节转换成16进制
        hex_data = binascii.hexlify(return_command_bytes).decode('utf-8')
        logging.info("发送命令:%s 返回16进制命令:%s 长度:%s", command.hex(),hex_data,len(hex_data))
        return hex_data

# 解析电机位置
def parse_position_response(response_hex):
    if not response_hex:
        return None
    try:
        # 01 36 00 00 00 00 00 6b
        # 解析符号和电机实时位置
        symbol = response_hex[4:6]
        position_hex = response_hex[6:14]
        position = int(position_hex, 16)
        # 将实时位置放大10倍
        position /= 10
        return position
    except ValueError:
        # 如果转换失败，返回 None
        return None
# 读取电机位置
def generate_read_position_command(address):
    # 将地址转换为字节
    address_byte = address.to_bytes(1, byteorder='big')
    # 将所有字节组合成命令
    command = address_byte + b'\x36'
    # 校验字节
    checksum_byte = b'\x6B'
    # 拼接校验字节
    command += checksum_byte
    return bytes(command)

# 紧急停止
def generate_stop_command(address, multi_sync=True):
    # 将地址转换为字节
    address_byte = address.to_bytes(1, byteorder='big')
    # 确定多机同步标志
    multi_sync_byte = b'\x01' if multi_sync else b'\x00'
    # 将所有字节组合成命令
    command = address_byte + b'\xFE\x98' + multi_sync_byte
    # 校验字节
    checksum_byte = b'\x6B'
    # 拼接校验字节
    command += checksum_byte
    return bytes(command)

# 梯形曲线位置模式控制
def generate_trapezoid_command(address, is_clockwise, acc_accel, dec_accel, max_speed, position, is_relative=False, multi_sync=True):
    # 将地址转换为字节
    address_byte = address.to_bytes(1, byteorder='big')
    # 确定方向
    direction_byte = b'\x01' if is_clockwise else b'\x00'
    # 将加速加速度、减速加速度和最大速度转换为字节
    acc_accel_byte = acc_accel.to_bytes(2, byteorder='big', signed=True)
    dec_accel_byte = dec_accel.to_bytes(2, byteorder='big', signed=True)
    max_speed_byte = int(max_speed * 10).to_bytes(2, byteorder='big', signed=True)
    # 计算负位置值
    position_bytes = int(position * 10).to_bytes(4, byteorder='big', signed=True)
    # 确定命令模式（相对或绝对位置）
    command_mode_byte = b'\x00' if is_relative else b'\x01'
    # 确定多机同步标志
    multi_sync_byte = b'\x01' if multi_sync else b'\x00'
    # 将所有字节组合成命令
    command = address_byte + b'\xFD' + direction_byte + acc_accel_byte + dec_accel_byte + max_speed_byte + position_bytes + command_mode_byte + multi_sync_byte
    # 校验字节
    checksum_byte = b'\x6B'
    # 拼接校验字节
    command += checksum_byte
    return bytes(command)

# 读取电机当前位置
def current_location():
    # 创建读取位置命令
    command_bytes = generate_read_position_command(address=1)
    # 发送读取位置命令 等待返回位置
    response_hex = send_command(command=command_bytes, read_size=8)
    # 解析16进制 位置
    position = parse_position_response(response_hex=response_hex)
    return position

# 最大速度求出百分比加减速
def speed_calculate_percentage():
    global max_speed
    percentage = (max_speed * 70) / 100
    return [int(percentage), int(max_speed)]

# 移动电机到position点
def move_to(position, new_thread=True):
    if new_thread:
        # 异步执行
        command_bytes = generate_trapezoid_command(address=1, is_clockwise=True,
                                                   acc_accel=speed_calculate_percentage()[0],
                                                   dec_accel=speed_calculate_percentage()[0],
                                                   max_speed=speed_calculate_percentage()[1], position=position,
                                                   is_relative=False, multi_sync=False)
        # 启动新线程执行
        threading.Thread(target=send_command, args={command_bytes}).start()
    else:
        command = generate_trapezoid_command(address=1, is_clockwise=True,
                                             acc_accel=speed_calculate_percentage()[0],
                                             dec_accel=speed_calculate_percentage()[0],
                                             max_speed=speed_calculate_percentage()[1], position=position,
                                             is_relative=False, multi_sync=False)
        send_command(command)

# 紧急停止
def stop():
    # 紧急停止
    command_bytes = generate_stop_command(address=1, multi_sync=False)
    threading.Thread(target=send_command, args={command_bytes}).start()
    # AB循环线程停止
    STOP.clear()

# 梯形曲线位置模式控制中的加速加速度与减速加速度是由speed_calculate_percentage函数中的percentage变量控制
# 最大速度加速
def acc_accel():
    global max_speed
    with lock:
        # 3720
        max_speed += 30
        if max_speed >=500:
            max_speed = 500
        logging.info("最大速度:%s",max_speed)

# 最大速度减速
def dec_accel():
    global max_speed
    with lock:
        max_speed -= 30
        if max_speed < 30:
            max_speed = 30
        logging.info("最大速度:%s", max_speed)

# AB点循环
def ab_loop():
    # 开关 切换A点与B点
    switch = True
    # 先移动到A点
    temp_position = position_a
    # 实时位置
    while STOP.is_set():
        logging.info("目标位置:%s",temp_position)
        # 移动
        move_to(temp_position,new_thread=False)
        # 等待0.2秒
        time.sleep(0.2)
        # 获取实时位置
        current_position = current_location()
        # 实时读取位置 可能会失败
        if current_position is None:
            continue
        logging.info("当前位置:%s",current_position)
        # 电机是否到移动到位置
        if int(current_position) == int(temp_position):
            logging.info("已到达:%s", temp_position)
            if switch:
                # 设置电机移动到B点
                temp_position = position_b
                switch = False
            else:
                # 设置电机移动到A点
                temp_position = position_a
                switch = True

# 监听按键
def on_key_event(event):
    if event.event_type == keyboard.KEY_DOWN:
        # print(f"键盘按下：{event.name}")
        pass
    elif event.event_type == keyboard.KEY_UP:
        # print(f"键盘松开：{event.name}")
        pass

# 设置A点
def set_a_point():
    # 这里是设置滑轨A点
    global position_a
    position_a = current_location()
    logging.info("A:%s", position_a)

# 设置B点
def set_b_point():
    # 这里是设置滑轨B点
    global position_b
    position_b = current_location()
    logging.info("B:%s", position_b)

# AB循环
def start_ab_loop():
    logging.info("A:%s B:%s %s",position_a,position_b,STOP.is_set())
    # AB点正在运行 则停止
    if STOP.is_set():
        STOP.clear()
    else:
        # AB点没有运行 则运行
        STOP.set()
        threading.Thread(target=ab_loop).start()

# 移动到滑轨右侧
def move_to_right():
    # 移动-7500度
    move_to(7500)

# 移动到滑轨左侧
def move_to_left():
    # 回到0度位置
    move_to(0)

# 监听键盘
def monitor_keyboard():
    logging.info("监听键盘")
    keyboard.hook(on_key_event)
    # 捕捉键盘按键事件
    # 设置A点
    keyboard.add_hotkey('a', set_a_point)
    # 设置B点
    keyboard.add_hotkey('b', set_b_point)
    # AB点循环
    keyboard.add_hotkey('space', start_ab_loop)
    # 右移动
    keyboard.add_hotkey('right', move_to_right)
    # 左移动
    keyboard.add_hotkey('left', move_to_left)
    # 加速
    keyboard.add_hotkey('up', acc_accel)
    # 减速
    keyboard.add_hotkey('down', dec_accel)
    # 急停
    keyboard.add_hotkey('enter', stop)
    try:
        while True:
            pass
    except KeyboardInterrupt:
        # 在终端中按下Ctrl+C时退出程序
        pass
    finally:
        # 清除键盘监听
        keyboard.unhook_all()
# 打开串口
def open_ttl():
    # 创建串口对象
    global ser
    ser = serial.Serial('/dev/ttyS0', baudrate=115200, timeout=5)
    logging.info("成功打开串口:%s 比特率:%s",ser.name,ser.baudrate)

if __name__ == "__main__":
    # 打开串口
    open_ttl()
    # 启动监听键盘
    threading.Thread(target=monitor_keyboard).start()
