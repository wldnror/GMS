from pymodbus.client import ModbusTcpClient

# 1) 연결 정보 바꿔주세요
CLIENT_IP   = "109.3.55.1"
CLIENT_PORT = 502

client = ModbusTcpClient(CLIENT_IP, port=CLIENT_PORT, timeout=3)
if not client.connect():
    print("Modbus 연결 실패")
    exit(1)

# 2) 40022~40024 읽기 (0-based offset = 40022-1)
resp = client.read_holding_registers(40022-1, 3)
if resp.isError():
    print("레지스터 읽기 오류:", resp)
else:
    ver, status_bits, prog_rem = resp.registers
    progress = prog_rem & 0xFF
    remaining = prog_rem >> 8
    print(f"Version     = {ver}")
    print(f"Status bits = 0x{status_bits:04X}")
    print(f"Progress    = {progress}%")
    print(f"Remaining   = {remaining} s")

client.close()
