"""响应展示面板：思维链与正文渲染、原始事件流、核验信息与统计指标。"""

import html
import json
from pathlib import Path

import markdown
from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QApplication,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

STATS_KEYS = (
    "发起时间",
    "首 token (TTFT)",
    "总耗时",
    "生成耗时",
    "吞吐",
    "输出文本",
    "用量 (tokens)",
)

_BASE_STYLE = """
body { font-size: 14px; line-height: 1.55; }
pre { background: #f2f2f2; padding: 6px 8px; border-radius: 4px; }
code { font-family: Consolas, "Courier New", monospace; }
pre code { background: transparent; }
table { border-collapse: collapse; }
th, td { border: 1px solid #c8c8c8; padding: 2px 8px; }
blockquote { color: #555; border-left: 3px solid #c8c8c8; margin-left: 0;
             padding-left: 8px; }
/* 思维链：灰色区块与正文明确区分 */
.reasoning { color: #707070; background: #f7f7f7;
             border-left: 3px solid #d5d5d5; padding: 6px 10px;
             margin: 0 0 12px 0; }
.reasoning .rh { color: #9a9a9a; font-weight: 600; font-size: 12px;
                 letter-spacing: 1px; margin: 0 0 4px 0; }
.reasoning p { margin: 2px 0; }
.reasoning pre { background: #ececec; }
"""


def _selectable(label: QLabel) -> QLabel:
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


class ResponsePanel(QWidget):
    """右侧面板：展示思维链与正文、原始协议事件、核验信息与性能统计。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._buffer = ""
        self._reasoning_buffer = ""
        self._notice: tuple[str, str] | None = None  # (消息, 颜色)
        self._stats_labels: dict[str, QLabel] = {}
        self._build_ui()

        self._flush_timer = QTimer(self)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.setInterval(80)
        self._flush_timer.timeout.connect(self._flush_render)
        self.reset()

    # ---- UI 构建 -----------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        self.status_label = QLabel("就绪")
        self.clear_button = QPushButton("清空")
        self.copy_button = QPushButton("复制文本")

        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.addWidget(self.status_label, 1)
        header_row.addWidget(self.copy_button)
        header_row.addWidget(self.clear_button)
        layout.addLayout(header_row)

        self.tabs = QTabWidget()

        self.render_view = QTextBrowser()
        self.render_view.setOpenExternalLinks(True)
        self.tabs.addTab(self.render_view, "渲染")

        self.raw_view = QPlainTextEdit()
        self.raw_view.setReadOnly(True)
        self.raw_view.setMaximumBlockCount(5000)
        self.tabs.addTab(self.raw_view, "原始事件")
        layout.addWidget(self.tabs, 1)

        layout.addWidget(self._build_verify_box())
        layout.addWidget(self._build_stats_box())

        self.clear_button.clicked.connect(self.reset)
        self.copy_button.clicked.connect(self._copy_text)
        self.open_archive_button.clicked.connect(self._open_archive_dir)

    def _build_verify_box(self) -> QGroupBox:
        box = QGroupBox("核验与归档")
        form = QFormLayout(box)
        form.setContentsMargins(8, 8, 8, 8)

        self.model_check_label = QLabel("--")
        self.response_id_label = _selectable(QLabel("--"))
        self.fingerprint_label = _selectable(QLabel("--"))
        self.service_tier_label = _selectable(QLabel("--"))
        self.archive_path_label = _selectable(QLabel("--"))
        self.archive_path_label.setWordWrap(True)
        self.open_archive_button = QPushButton("打开目录")

        archive_row = QHBoxLayout()
        archive_row.setContentsMargins(0, 0, 0, 0)
        archive_row.addWidget(self.archive_path_label, 1)
        archive_row.addWidget(self.open_archive_button)

        form.addRow("模型核验", self.model_check_label)
        form.addRow("Response ID", self.response_id_label)
        form.addRow("系统指纹", self.fingerprint_label)
        form.addRow("服务层级", self.service_tier_label)
        form.addRow("归档", archive_row)
        return box

    def _build_stats_box(self) -> QGroupBox:
        box = QGroupBox("统计")
        form = QFormLayout(box)
        form.setContentsMargins(8, 8, 8, 8)
        for key in STATS_KEYS:
            label = QLabel("--")
            self._stats_labels[key] = label
            form.addRow(key, label)
        return box

    # ---- 状态 --------------------------------------------------------------

    def reset(self) -> None:
        self._buffer = ""
        self._reasoning_buffer = ""
        self._notice = None
        self._flush_timer.stop()
        self.render_view.setHtml("")
        self.raw_view.clear()
        for label in self._stats_labels.values():
            label.setText("--")
        for label in (
            self.model_check_label,
            self.response_id_label,
            self.fingerprint_label,
            self.service_tier_label,
            self.archive_path_label,
        ):
            label.setText("--")
        self.model_check_label.setStyleSheet("")
        self.status_label.setText("就绪。在左侧配置端点后点击「发送」。")

    def prepare(self) -> None:
        """开始新一次请求。"""
        self.reset()
        self.status_label.setText("请求中…")

    # ---- 数据流 ------------------------------------------------------------

    def append_delta(self, text: str) -> None:
        self._buffer += text
        self.status_label.setText(
            f"接收中… 正文 {len(self._buffer)} 字符 / 思考 {len(self._reasoning_buffer)} 字符"
        )
        if not self._flush_timer.isActive():
            self._flush_timer.start()

    def append_reasoning(self, text: str) -> None:
        self._reasoning_buffer += text
        self.status_label.setText(
            f"接收中… 正文 {len(self._buffer)} 字符 / 思考 {len(self._reasoning_buffer)} 字符"
        )
        if not self._flush_timer.isActive():
            self._flush_timer.start()

    def append_full_text(self, text: str) -> None:
        self._buffer += text
        self._flush_render()

    def append_event(self, name: str, data: dict) -> None:
        self.raw_view.appendPlainText(
            f"◆ {name}\n{json.dumps(data, ensure_ascii=False)}\n"
        )

    def set_usage(self, input_tokens: int, output_tokens: int) -> None:
        self._stats_labels["用量 (tokens)"].setText(
            f"输入 {input_tokens} / 输出 {output_tokens} / "
            f"合计 {input_tokens + output_tokens}"
        )

    def show_stats(self, stats: dict[str, str]) -> None:
        self._flush_render()
        for key, label in self._stats_labels.items():
            label.setText(stats.get(key, "--"))
        self.status_label.setText("完成")

    def show_record(self, record: dict) -> None:
        """显示本次回复的核验信息（请求模型 vs 响应报告模型、指纹、归档）。"""
        requested = record.get("requested_model", "")
        reported = record.get("reported_model", "")
        match = record.get("model_match")
        if match is True:
            self.model_check_label.setText(f"✓ 一致：{reported}")
            self.model_check_label.setStyleSheet("color:#1a7f37;font-weight:600;")
        elif match is False:
            self.model_check_label.setText(
                f"✗ 不一致：请求「{requested}」，实际返回「{reported}」"
            )
            self.model_check_label.setStyleSheet("color:#b00020;font-weight:600;")
        else:
            self.model_check_label.setText("—：响应未报告模型 ID")
            self.model_check_label.setStyleSheet("color:#666666;")
        self.response_id_label.setText(record.get("response_id") or "（未返回）")
        self.fingerprint_label.setText(
            record.get("system_fingerprint") or "（未返回）"
        )
        self.service_tier_label.setText(record.get("service_tier") or "（未返回）")
        self.archive_path_label.setText(record.get("archive_path") or "（未归档）")

    def show_error(self, message: str, detail: str = "") -> None:
        self._notice = (message, "#b00020")
        self._flush_render()
        self.status_label.setText(f"失败：{message}")
        if detail:
            self.raw_view.appendPlainText(f"◆ error\n{detail}\n")

    def show_cancelled(self) -> None:
        self._notice = ("用户已停止本次请求。", "#666666")
        self._flush_render()
        self.status_label.setText("已停止")

    # ---- 内部 ---------------------------------------------------------------

    def _flush_render(self) -> None:
        self._flush_timer.stop()
        parts: list[str] = []
        if self._reasoning_buffer.strip():
            reasoning_html = markdown.markdown(
                self._reasoning_buffer,
                extensions=["fenced_code", "tables", "sane_lists"],
            )
            parts.append(
                '<div class="reasoning"><div class="rh">思考过程</div>'
                f"{reasoning_html}</div>"
            )
        parts.append(
            markdown.markdown(
                self._buffer, extensions=["fenced_code", "tables", "sane_lists"]
            )
        )
        if self._notice:
            message, color = self._notice
            parts.append(
                "<hr>"
                f'<p style="color:{color};font-weight:600;">'
                f"⚠ {html.escape(message)}</p>"
            )
        bar = self.render_view.verticalScrollBar()
        was_at_bottom = bar.value() >= bar.maximum() - 8
        self.render_view.setHtml(
            "<html><head><meta charset='utf-8'>"
            f"<style>{_BASE_STYLE}</style></head><body>{''.join(parts)}"
            "</body></html>"
        )
        bar.setValue(bar.maximum() if was_at_bottom else bar.value())

    def _copy_text(self) -> None:
        text = self.render_view.toPlainText()
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(text)
        self.status_label.setText("已复制渲染区文本（含思考过程）")

    def _open_archive_dir(self) -> None:
        path = self.archive_path_label.text()
        if not path or path == "--":
            return
        directory = Path(path).parent
        directory.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))
