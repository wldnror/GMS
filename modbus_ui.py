    # ---------- 모델 변경(레지스터 쓰기) ----------
    # ⚠️ 여기 주소/비트 규칙은 장비 펌웨어 스펙에 따라 반드시 맞춰야 합니다.
    # 예시: 40094에 bit0=0 -> ASGD3200, bit0=1 -> ASGD3210
    MODEL_SELECT_REG = 40094

    def change_device_model(self, box_index: int, bit0: int):
        """
        bit0: 0이면 ASGD3200, 1이면 ASGD3210 (예시)
        """
        ip = self.ip_vars[box_index].get()
        client = self.clients.get(ip)
        lock = self.modbus_locks.get(ip)
        if client is None or lock is None:
            messagebox.showwarning("모델 변경", "먼저 Modbus 연결을 해주세요.")
            return

        addr = self.reg_addr(self.MODEL_SELECT_REG)
        value = 1 if bit0 else 0

        try:
            with lock:
                r = client.write_register(addr, value)

            if isinstance(r, ExceptionResponse) or getattr(r, "isError", lambda: False)():
                messagebox.showerror("모델 변경", f"모델 변경 명령 실패\n{r}")
                return

            messagebox.showinfo(
                "모델 변경",
                "모델 변경 명령을 전송했습니다.\n장비가 재시작/재연결이 필요할 수 있습니다."
            )

            # (선택) 바로 모델 다시 읽어서 라벨 갱신 시도
            try:
                self.read_detector_model_from_device(box_index)
            except Exception:
                pass

        except Exception as e:
            messagebox.showerror("모델 변경", f"오류가 발생했습니다.\n{e}")
