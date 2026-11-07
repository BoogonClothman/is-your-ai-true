"""请求构造面板：端点配置、消息列表与采样参数。"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..core.models import (
    DEFAULT_BASE_URLS,
    DEFAULT_MODELS,
    PROTOCOL_ANTHROPIC,
    PROTOCOL_LABELS,
    PROTOCOL_OPENAI_CHAT,
    PROTOCOL_OPENAI_RESPONSES,
    ROLES,
    ChatMessage,
    EndpointConfig,
    RequestParams,
)

MODEL_CANDIDATES: dict[str, list[str]] = {
    PROTOCOL_OPENAI_CHAT: [
        "gpt-4o",
        "gpt-4o-mini",
        "gpt-4.1",
        "gpt-4.1-mini",
        "gpt-4.1-nano",
        "o4-mini",
    ],
    PROTOCOL_OPENAI_RESPONSES: [
        "gpt-4o",
        "gpt-4o-mini",
        "gpt-4.1",
        "gpt-4.1-mini",
        "gpt-4.1-nano",
        "o4-mini",
    ],
    PROTOCOL_ANTHROPIC: [
        "claude-sonnet-4-20250514",
        "claude-opus-4-20250514",
        "claude-3-7-sonnet-20250219",
        "claude-3-5-haiku-20241022",
    ],
}


class RequestPanel(QWidget):
    """左侧面板：配置端点、编辑消息、调整采样参数并发起请求。"""

    sendRequested = pyqtSignal(EndpointConfig, list, RequestParams)
    stopRequested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._current_protocol: str | None = None
        self._build_ui()
        self._connect_signals()
        self._on_protocol_changed()

    # ---- UI 构建 -----------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        layout.addWidget(self._build_endpoint_box())
        layout.addWidget(self._build_messages_box(), 1)
        layout.addWidget(self._build_params_box())
        layout.addLayout(self._build_action_row())

    def _build_endpoint_box(self) -> QGroupBox:
        box = QGroupBox("端点")
        form = QFormLayout(box)

        self.protocol_combo = QComboBox()
        for key, label in PROTOCOL_LABELS.items():
            self.protocol_combo.addItem(label, key)

        self.base_url_edit = QLineEdit()
        self.base_url_edit.setPlaceholderText("https://api.openai.com/v1")

        self.api_key_edit = QLineEdit()
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_edit.setPlaceholderText("sk-…（留空则匿名访问）")
        self.show_key_check = QCheckBox("显示")

        key_row = QHBoxLayout()
        key_row.setContentsMargins(0, 0, 0, 0)
        key_row.addWidget(self.api_key_edit)
        key_row.addWidget(self.show_key_check)

        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)

        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(1, 3600)
        self.timeout_spin.setValue(120)
        self.timeout_spin.setSuffix(" 秒")

        self.extra_headers_edit = QTextEdit()
        self.extra_headers_edit.setPlaceholderText(
            "附加请求头：每行一个，格式 Key: Value（可留空）"
        )
        self.extra_headers_edit.setFixedHeight(64)
        self.extra_headers_edit.setAcceptRichText(False)

        form.addRow("协议", self.protocol_combo)
        form.addRow("Base URL", self.base_url_edit)
        form.addRow("API Key", key_row)
        form.addRow("模型", self.model_combo)
        form.addRow("超时", self.timeout_spin)
        form.addRow("附加头", self.extra_headers_edit)
        return box

    def _build_messages_box(self) -> QGroupBox:
        box = QGroupBox("消息")

        self.message_list = QListWidget()

        self.role_combo = QComboBox()
        self.role_combo.addItems(ROLES)
        self.content_edit = QLineEdit()
        self.content_edit.setPlaceholderText("输入消息内容，按 Enter 添加")
        add_button = QPushButton("添加")
        add_button.clicked.connect(self._add_message)
        self.content_edit.returnPressed.connect(self._add_message)

        input_row = QHBoxLayout()
        input_row.setContentsMargins(0, 0, 0, 0)
        input_row.addWidget(self.role_combo)
        input_row.addWidget(self.content_edit, 1)
        input_row.addWidget(add_button)

        remove_button = QPushButton("删除选中")
        remove_button.clicked.connect(self._remove_selected_message)
        clear_button = QPushButton("清空")
        clear_button.clicked.connect(self.message_list.clear)
        list_row = QHBoxLayout()
        list_row.setContentsMargins(0, 0, 0, 0)
        list_row.addWidget(remove_button)
        list_row.addStretch(1)
        list_row.addWidget(clear_button)

        messages_layout = QVBoxLayout(box)
        messages_layout.setContentsMargins(8, 8, 8, 8)
        messages_layout.addWidget(self.message_list)
        messages_layout.addLayout(input_row)
        messages_layout.addLayout(list_row)
        return box

    def _build_params_box(self) -> QGroupBox:
        box = QGroupBox("采样参数")
        grid = QGridLayout(box)
        grid.setContentsMargins(8, 8, 8, 8)
        grid.setHorizontalSpacing(8)

        self.temperature_check = QCheckBox("覆盖")
        self.temperature_check.setChecked(True)
        self.temperature_spin = _make_double_spin(0.0, 2.0, 0.05, 1.0)
        self.top_p_check = QCheckBox("覆盖")
        self.top_p_check.setChecked(True)
        self.top_p_spin = _make_double_spin(0.0, 1.0, 0.01, 1.0)
        self.max_tokens_check = QCheckBox("覆盖")
        self.max_tokens_check.setChecked(True)
        self.max_tokens_spin = QSpinBox()
        self.max_tokens_spin.setRange(1, 100000)
        self.max_tokens_spin.setValue(1024)

        grid.addWidget(QLabel("temperature"), 0, 0)
        grid.addWidget(self.temperature_check, 0, 1)
        grid.addWidget(self.temperature_spin, 0, 2)
        grid.addWidget(QLabel("top_p"), 1, 0)
        grid.addWidget(self.top_p_check, 1, 1)
        grid.addWidget(self.top_p_spin, 1, 2)
        grid.addWidget(QLabel("max_tokens"), 2, 0)
        grid.addWidget(self.max_tokens_check, 2, 1)
        grid.addWidget(self.max_tokens_spin, 2, 2)
        self.stream_check = QCheckBox("流式输出（SSE）")
        self.stream_check.setChecked(True)
        grid.addWidget(self.stream_check, 3, 0, 1, 3)
        return box

    def _build_action_row(self) -> QHBoxLayout:
        self.send_button = QPushButton("发送")
        self.stop_button = QPushButton("停止")
        self.stop_button.setEnabled(False)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.send_button, 1)
        row.addWidget(self.stop_button)
        return row

    def _connect_signals(self) -> None:
        self.protocol_combo.currentIndexChanged.connect(
            lambda _index: self._on_protocol_changed()
        )
        self.show_key_check.toggled.connect(self._toggle_key_echo)
        self.send_button.clicked.connect(self._emit_send_requested)
        self.stop_button.clicked.connect(self.stopRequested.emit)

    # ---- 行为 ---------------------------------------------------------------

    def _on_protocol_changed(self) -> None:
        key = self.protocol_combo.currentData()
        old = self._current_protocol
        if old == key:
            return
        old_default_url = DEFAULT_BASE_URLS.get(old, "") if old else None
        old_default_model = DEFAULT_MODELS.get(old, "") if old else None
        current_url = self.base_url_edit.text().strip()
        current_model = self.model_combo.currentText().strip()

        self._current_protocol = key
        candidates = list(MODEL_CANDIDATES.get(key, []))
        if current_model and current_model not in candidates:
            candidates.insert(0, current_model)
        self.model_combo.clear()
        self.model_combo.addItems(candidates)

        if old is None:
            if not current_url:
                self.base_url_edit.setText(DEFAULT_BASE_URLS.get(key, ""))
            if not current_model:
                self.model_combo.setCurrentText(DEFAULT_MODELS.get(key, ""))
        else:
            if old_default_url is not None and current_url == old_default_url:
                self.base_url_edit.setText(DEFAULT_BASE_URLS.get(key, ""))
            if old_default_model is not None and current_model == old_default_model:
                self.model_combo.setCurrentText(DEFAULT_MODELS.get(key, ""))

    def _toggle_key_echo(self, visible: bool) -> None:
        self.api_key_edit.setEchoMode(
            QLineEdit.EchoMode.Normal
            if visible
            else QLineEdit.EchoMode.Password
        )

    def _add_message(self) -> None:
        content = self.content_edit.text()
        if not content:
            return
        message = ChatMessage(role=self.role_combo.currentText(), content=content)
        preview = content.replace("\n", " ")[:60]
        if len(content) > 60:
            preview += "…"
        item = QListWidgetItem(f"{message.role}: {preview}")
        item.setToolTip(content)
        item.setData(Qt.ItemDataRole.UserRole, message)
        self.message_list.addItem(item)
        self.message_list.setCurrentRow(self.message_list.count() - 1)
        self.content_edit.clear()
        self.content_edit.setFocus()

    def _remove_selected_message(self) -> None:
        row = self.message_list.currentRow()
        if row >= 0:
            self.message_list.takeItem(row)

    def _emit_send_requested(self) -> None:
        if not self.base_url_edit.text().strip():
            self.base_url_edit.setFocus()
            return
        if not self.model_combo.currentText().strip():
            self.model_combo.setFocus()
            return
        self.sendRequested.emit(
            self.collect_config(), self.collect_messages(), self.collect_params()
        )

    # ---- 数据收集 / 应用 ----------------------------------------------------

    def collect_config(self) -> EndpointConfig:
        return EndpointConfig(
            protocol=self.protocol_combo.currentData(),
            base_url=self.base_url_edit.text().strip(),
            api_key=self.api_key_edit.text().strip(),
            model=self.model_combo.currentText().strip(),
            timeout=float(self.timeout_spin.value()),
            extra_headers=self._collect_extra_headers(),
        )

    def _collect_extra_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        for raw in self.extra_headers_edit.toPlainText().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            name, _, value = line.partition(":")
            headers[name.strip()] = value.strip()
        return headers

    def collect_messages(self) -> list[ChatMessage]:
        messages: list[ChatMessage] = []
        for row in range(self.message_list.count()):
            item = self.message_list.item(row)
            message = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(message, ChatMessage):
                messages.append(message)
        return messages

    def collect_params(self) -> RequestParams:
        return RequestParams(
            temperature=(
                self.temperature_spin.value()
                if self.temperature_check.isChecked()
                else None
            ),
            max_tokens=(
                self.max_tokens_spin.value()
                if self.max_tokens_check.isChecked()
                else None
            ),
            top_p=(
                self.top_p_spin.value()
                if self.top_p_check.isChecked()
                else None
            ),
            stream=self.stream_check.isChecked(),
        )

    def apply_config(self, config: EndpointConfig) -> None:
        """把已保存的端点配置填入面板。"""
        index = self.protocol_combo.findData(config.protocol)
        self.protocol_combo.setCurrentIndex(index if index >= 0 else 0)
        self.base_url_edit.setText(config.base_url)
        self.api_key_edit.setText(config.api_key)
        self.model_combo.setCurrentText(config.model)
        self.timeout_spin.setValue(max(1, int(config.timeout)))
        self.extra_headers_edit.setPlainText(
            "\n".join(f"{name}: {value}" for name, value in config.extra_headers.items())
        )

    def set_messages(self, messages: list[ChatMessage]) -> None:
        """设置消息列表（用于测试或预设恢复）。"""
        self.message_list.clear()
        for message in messages:
            preview = message.content.replace("\n", " ")[:60]
            item = QListWidgetItem(f"{message.role}: {preview}")
            item.setToolTip(message.content)
            item.setData(Qt.ItemDataRole.UserRole, message)
            self.message_list.addItem(item)

    def set_running(self, running: bool) -> None:
        self.send_button.setEnabled(not running)
        self.stop_button.setEnabled(running)


def _make_double_spin(
    minimum: float, maximum: float, step: float, value: float
) -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setRange(minimum, maximum)
    spin.setSingleStep(step)
    spin.setValue(value)
    spin.setDecimals(2)
    return spin
