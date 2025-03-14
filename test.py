import matplotlib
# 한글 지원 폰트 설정 (NanumGothic이 설치되어 있어야 함)
matplotlib.rc('font', family='NanumGothic')
import matplotlib.pyplot as plt
import matplotlib.animation as animation
import Adafruit_ADS1x15

# 사용 가능한 ADS1115 주소 (0x4A는 감지되지 않아 제외)
ADS1115_ADDRESSES = [0x48, 0x49, 0x4B]
GAIN = 2/3        # Full-scale 전압 6.144V 기준
BUS_NUM = 1

# 각 ADS1115 모듈 초기화
adcs = {}
for address in ADS1115_ADDRESSES:
    try:
        adcs[address] = Adafruit_ADS1x15.ADS1115(address=address, busnum=BUS_NUM)
        print(f"Initialized ADS1115 at address 0x{address:02X}")
    except Exception as e:
        print(f"Failed to init ADS1115 at address 0x{address:02X}: {e}")
        adcs[address] = None

# 각 모듈의 4개 채널에 대해 라벨 생성 (예: "0x48 Ch0")
labels = []
for address in ADS1115_ADDRESSES:
    for ch in range(4):
        labels.append(f"0x{address:02X} Ch{ch}")

# 12채널에 대한 실시간 막대그래프 생성
fig, ax = plt.subplots(figsize=(10, 6))
bars = ax.bar(labels, [0] * len(labels))
ax.set_ylim(0, 100)
ax.set_ylabel("활성화 (%)")
ax.set_title("실시간 채널 활성도 (0~100%)")
plt.xticks(rotation=45, ha='right')

# 최대 전류 (mA)를 100%에 해당하는 값으로 설정 (환경에 맞게 조정)
max_current = 10.0

def read_percentages():
    percentages = []
    # 각 ADS1115 모듈의 각 채널 읽기
    for address in ADS1115_ADDRESSES:
        adc = adcs.get(address)
        for ch in range(4):
            if adc is not None:
                try:
                    value = adc.read_adc(ch, gain=GAIN)
                    voltage = value * 6.144 / 32767  # 전압으로 변환
                    current = voltage / 250           # 250Ω 션트 기준 전류 (A)
                    milliamp = current * 1000         # mA 단위 변환
                    percent = min(max(milliamp / max_current * 100, 0), 100)
                except Exception as e:
                    print(f"Error reading 0x{address:02X} channel {ch}: {e}")
                    percent = 0
            else:
                percent = 0
            percentages.append(percent)
    return percentages

def animate(frame):
    percentages = read_percentages()
    for bar, percent in zip(bars, percentages):
        bar.set_height(percent)
    return bars

# 1초 간격으로 업데이트, cache_frame_data=False로 캐싱 경고 제거
ani = animation.FuncAnimation(fig, animate, interval=1000, cache_frame_data=False)

plt.tight_layout()
plt.show()
