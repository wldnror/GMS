class ModbusUI:
    def __init__(self, root, num_boxes):
        self.root = root
        self.virtual_keyboard = VirtualKeyboard(root)  # 가상 키보드 초기화
        # 기타 초기화 코드...

    def add_ip_row(self, frame, ip_var, index):
        entry = Entry(frame, textvariable=ip_var, width=16, highlightthickness=0)  # 길이를 16으로 설정
        placeholder_text = f"{index + 1}. IP를 입력해주세요."
        entry.insert(0, placeholder_text)
        entry.bind("<FocusIn>", lambda event, e=entry, p=placeholder_text: self.on_focus_in(e, p))
        entry.bind("<FocusOut>", lambda event, e=entry, p=placeholder_text: self.on_focus_out(e, p))
        entry.bind("<Button-1>", lambda event, e=entry: self.show_virtual_keyboard(e))  # 가상 키보드를 열도록 이벤트 추가
        entry.grid(row=0, column=0, padx=(0, 5))  # 입력 필드 배치, 간격을 5로 설정
        self.entries.append(entry)

        action_button = Button(frame, image=self.connect_image, command=lambda i=index: self.toggle_connection(i),
                               width=60, height=40,  # 버튼 크기 설정
                               bd=0, highlightthickness=0, borderwidth=0, relief='flat', bg='black', activebackground='black')
        action_button.grid(row=0, column=1, padx=(0, 0))  # 버튼 배치
        self.action_buttons.append(action_button)

    def show_virtual_keyboard(self, event):
        entry = event.widget
        self.virtual_keyboard.show(entry)
