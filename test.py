import evdev
import RPi.GPIO as GPIO
import time
import logging
import threading
import serial.serialposix

# 设置日志格式
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

GPIO.setwarnings(False)

# 设置GPIO引脚模式
GPIO.setmode(GPIO.BOARD)

# 设置步进电机控制引脚
DIR_PIN = 12  # 控制方向 棕色
STEP_PIN = 18  # 控制步进 橙色
EN_PIN = 15  # 使能引脚，用于控制电机的启用和停用
RXD_PIN = 10  # 设置10号引脚为RXD

# 初始化步进电机控制引脚
GPIO.setup(DIR_PIN, GPIO.OUT)
GPIO.setup(STEP_PIN, GPIO.OUT)
GPIO.setup(EN_PIN, GPIO.OUT)

GPIO.setup(RXD_PIN, GPIO.IN)  # 将10号引脚设置为输入模式

STEP_64 = 12800
STEP_32 = 6400
STEP_16 = 3200
STEP_8 = 1600
STEP_4 = 800
STEP_2 = 400
STEP_1 = 200

#最大脉冲频率
max_khz = 120
#最小脉冲频率
min_khz = 1
# 当前脉冲
khz = 1
# 线程锁
lock = threading.Lock()
# 停止运动的事件
STOP = threading.Event()
# 滑轨总长 32细分6400*20  16细分3200*20  8细分1600*20
total_steps = STEP_8 * 20


# 用于记录电机当前位置的索引
current_index = 0

# 设置电机的方向（正向或反向）
def set_direction(direction):
    if direction == 'backward':
        GPIO.output(DIR_PIN, GPIO.LOW)
    elif direction == 'forward':
        GPIO.output(DIR_PIN, GPIO.HIGH)

# 设置电机的速度（控制步进的时间间隔）
def set_speed(speed_khz):
    # 将速度从KHz转换为Hz（每秒钟的脉冲次数）
    speed_hz = speed_khz * 1000
    # 控制脉冲信号的时间间隔，用于控制速度
    time_interval = 1 / (2 * speed_hz)  # speed_hz表示每秒的脉冲次数，这里将其转换为间隔 2 * speed_hz放大一倍的脉冲信号 因为高低为一个脉冲周期
    # 设置步进的时间间隔 一高一低为一次脉冲周期
    GPIO.output(STEP_PIN, GPIO.HIGH)  # 发送脉冲信号
    time.sleep(time_interval)
    GPIO.output(STEP_PIN, GPIO.LOW)  # 结束脉冲信号
    time.sleep(time_interval)

# 控制电机转动指定的步数
def move_steps(steps, direction):
    # 根据电机方向更新电机位置的索引
    global current_index
    global khz
    # 在方法中使用锁
    with lock:
        enable_motor()
        if "backward" == direction:
            steps = current_index
        elif "forward" == direction:
            steps = total_steps - current_index
        set_direction(direction)
        # 记录开始时间
        start_time = time.time()
        for i in range(steps):
            logging.info("i:"+str(i))
            # 检查是否收到停止事件
            if STOP.is_set():
                logging.info("停止事件")
                return
            set_speed(khz)
            if direction == 'forward':
                current_index = (current_index + 1)
            elif direction == 'backward':
                current_index = (current_index - 1)
        # 记录结束时间
        end_time = time.time()
        # 计算运行时间
        run_time = end_time - start_time
        print(f"代码运行时间：{run_time:.6f} 秒")

# 启用电机
def enable_motor():
    # 清除STOP事件，子线程可以继续运动
    STOP.clear()
    GPIO.output(EN_PIN, GPIO.HIGH)

# 停用电机
def disable_motor():
    # 设置STOP事件，通知子线程停止运动
    STOP.set()
    GPIO.output(EN_PIN, GPIO.LOW)

def calculate_checksum(data):
    # 计算校验字节
    checksum = sum(data) & 0xFF
    return checksum

def parse_response(response):
    # 解析返回的数据
    if len(response) != 3:
        return None

    address, command, checksum = response

    # 验证校验字节
    calculated_checksum = calculate_checksum([address, command])
    if calculated_checksum != checksum:
        return None

    # 解析命令状态
    command_status = command & 0x0F

    return command_status

def send_command(ser, command):
    # 向串口发送数据
    ser.write(command)
    print("发送:", command.hex())
    # 从串口接收数据
    response = ser.read(3)  # 期望读取3个字节的数据

    # 将返回的字节数据转换为整数列表
    response_data = [int(x) for x in response]

    command_status = parse_response(response_data)
    return command_status

def control():
    # 创建串口对象
    ser = serial.serialposix.Serial('/dev/ttyS0', baudrate=115200, timeout=1, stopbits=serial.STOPBITS_ONE)

    logging.info("监听手柄")
    # 连接手柄
    gamepad = evdev.InputDevice('/dev/input/event0')  # 请将 X 替换为手柄的设备号
    global khz
    # 让主线程持续运行
    try:
        for event in gamepad.read_loop():
            # logging.info("event.code: " + str(event.code) +" type:"+str(event.type)+ " value:" + str(event.value))
            if event.code == evdev.ecodes.ABS_HAT0X:
                # 十字按键 左右
                if event.type == 3 and event.value == 1:
                    logging.info("向右转动电机 event.type: " + str(event.type)+" value:"+str(event.value))
                    # 设置STOP事件，通知子线程停止运动
                    disable_motor()
                    # 电机往后移动 总长减去当前位置
                    motor_thread = threading.Thread(target=move_steps,
                                                    args=(move_steps, "forward"))
                    motor_thread.start()

                    # future = executor.submit(move_steps, "backward")
                elif event.type == 3 and event.value == -1:
                    logging.info("向左转动电机 event.type: " + str(event.type) + " value:" + str(event.value))
                    # 设置STOP事件，通知子线程停止运动
                    disable_motor()
                    # 电机初始位置是0位 往左移动使用当前位置 current_index
                    motor_thread = threading.Thread(target=move_steps,
                                                    args=(move_steps, "backward"))
                    motor_thread.start()
                    # future = executor.submit(move_steps, "forward")
                elif event.type == 3 and event.value ==0:
                    # logging.info("松开按钮 event.type: " + str(event.type) + " value:" + str(event.value))
                    pass
            elif event.code == evdev.ecodes.ABS_HAT0Y:
                # 十字按键 上下
                if event.type == 3 and event.value == -1:
                    khz += 1
                    logging.info(
                        "上 event.type: " + str(event.type) + " value:" + str(event.value) + " khz:" + str(khz))
                    # 发送加速信号
                elif event.type == 3 and event.value == 1:
                    temp_khz = khz - 1
                    if temp_khz < 1:
                        khz = 1
                    else:
                        khz = temp_khz

                    logging.info(
                        "下 event.type: " + str(event.type) + " value:" + str(event.value) + " khz:" + str(khz))
                    # 发送减速信号
                elif event.type == 3 and event.value == 0:
                    # logging.info("松开按钮 event.type: " + str(event.type) + " value:" + str(event.value))
                    pass
            elif event.code == evdev.ecodes.BTN_X:
                if event.type == 1 and event.value == 1:
                    logging.info("按下X")
                    # 发送停止信号
                    disable_motor()
                elif event.type ==1 and event.value == 0:
                    # logging.info("松开X")
                    pass
            elif event.code == evdev.ecodes.BTN_Y:
                if event.type == 1 and event.value == 1:
                    logging.info("按下Y")
                    print("current_index:" + str(current_index) + " 停止:" + str(STOP.is_set()) + " HKz:" + str(khz))

                    # 梯形曲线位置模式控制命令示例
                    trapezoid_command = bytes.fromhex("01 FD 01 01 FF 01 FA 27 10 00 00 8C A0 00 00 6B")
                    trapezoid_status = send_command(ser, trapezoid_command)
                    if trapezoid_status is not None:
                        print("梯形曲线位置模式控制状态:", trapezoid_status)

                elif event.type == 1 and event.value == 0:
                    logging.info("松开Y")
            elif event.code == evdev.ecodes.BTN_B:
                if event.type == 1 and event.value == 1:
                    logging.info("按下B")
                    khz = max_khz
                elif event.type == 1 and event.value == 0:
                    # logging.info("松开B")
                    pass
            elif event.code == evdev.ecodes.BTN_A:
                if event.type == 1 and event.value == 1:
                    logging.info("按下A")
                    khz = min_khz
                elif event.type == 1 and event.value == 0:
                    # logging.info("松开A")
                    pass
    except KeyboardInterrupt:
        # 在终端中按下Ctrl+C时停止电机并退出程序
        disable_motor()
        GPIO.cleanup()





if __name__ == "__main__":
    control()

# sudo systemctl restart bluetooth
# sudo bluetoothctl
# connect EC:83:50:C7:AD:37