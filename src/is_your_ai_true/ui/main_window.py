"""主窗口：组装左右面板，管理请求线程生命周期与端点预设。"""

import json
from pathlib import Path

from PyQt6.QtCore import QByteArray, QSettings, QStandardPaths, Qt
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QComboBox,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QSplitter,
)

from ..core.archive import Archive
from ..core.models import ChatMessage, EndpointConfig, RequestParams
from ..core.worker import RequestWorker
from .request_panel import RequestPanel
from .response_panel import ResponsePanel

SETTINGS_ORG = "IsYourAITrue"
SETTINGS_APP = "is-your-ai-true"


class MainWindow(QMainWindow):
    """应用主窗口：左侧构造请求，右侧查看响应与统计。"""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Is Your AI True — 你的AI保真吗？")
        self.resize(1280, 820)

        self._worker: RequestWorker | None = None
        self._presets: dict[str, EndpointConfig] = {}
        self._archive = Archive(self._archive_directory())

        self.request_panel = RequestPanel(self)
        self.response_panel = ResponsePanel(self)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.addWidget(self.request_panel)
        splitter.addWidget(self.response_panel)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([460, 820])
        self.setCentralWidget(splitter)

        self._build_toolbar()
        self._connect_signals()
        self._restore_state()

    # ---- UI 构建 -----------------------------------------------------------

    def _build_toolbar(self) -> None:
        toolbar = self.addToolBar("预设")
        toolbar.setMovable(False)
        toolbar.addWidget(QLabel(" 预设: "))
        self.preset_combo = QComboBox()
        self.preset_combo.setMinimumWidth(220)
        toolbar.addWidget(self.preset_combo)

        save_action = QAction("保存预设", self)
        save_action.setShortcut("Ctrl+S")
        save_action.triggered.connect(self._save_preset)
        toolbar.addAction(save_action)

        delete_action = QAction("删除预设", self)
        delete_action.triggered.connect(self._delete_preset)
        toolbar.addAction(delete_action)

    def _connect_signals(self) -> None:
        self.request_panel.sendRequested.connect(self._on_send)
        self.request_panel.stopRequested.connect(self._on_stop)
        self.preset_combo.currentIndexChanged.connect(self._load_preset)

    # ---- 请求生命周期 -------------------------------------------------------

    def _on_send(
        self,
        config: EndpointConfig,
        messages: list[ChatMessage],
        params: RequestParams,
    ) -> None:
        if self._worker is not None:
            return
        if not messages:
            QMessageBox.information(self, "提示", "请先在左侧添加至少一条消息。")
            return

        worker = RequestWorker(config, messages, params, self._archive, self)
        worker.delta.connect(self.response_panel.append_delta)
        worker.reasoning.connect(self.response_panel.append_reasoning)
        worker.usage.connect(self.response_panel.set_usage)
        worker.debug.connect(self.response_panel.append_event)
        worker.failed.connect(self._on_failed)
        worker.cancelled.connect(self.response_panel.show_cancelled)
        worker.succeeded.connect(self._on_succeeded)
        worker.finished.connect(self._on_worker_finished)
        self._worker = worker

        self.request_panel.set_running(True)
        self.response_panel.prepare()
        worker.start()

    def _on_succeeded(self, stats: dict, record: dict) -> None:
        self.response_panel.show_stats(stats)
        self.response_panel.show_record(record)

    def _archive_directory(self) -> Path:
        base = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.AppDataLocation
        )
        return Path(base) / "is-your-ai-true" / "records"

    def _on_stop(self) -> None:
        if self._worker is not None:
            self._worker.cancel()

    def _on_failed(self, message: str, detail: str) -> None:
        self.response_panel.show_error(message, detail)

    def _on_worker_finished(self) -> None:
        worker = self._worker
        self._worker = None
        self.request_panel.set_running(False)
        if worker is not None:
            worker.deleteLater()

    # ---- 预设管理 -----------------------------------------------------------

    def _save_preset(self) -> None:
        name, accepted = QInputDialog.getText(
            self,
            "保存预设",
            "预设名称：",
            text=self.preset_combo.currentText(),
        )
        if not accepted or not name.strip():
            return
        name = name.strip()
        config = self.request_panel.collect_config()
        config.name = name
        if name in self._presets:
            confirm = QMessageBox.question(
                self,
                "覆盖预设",
                f"预设「{name}」已存在，是否覆盖？",
            )
            if confirm != QMessageBox.StandardButton.Yes:
                return
        self._presets[name] = config
        self._refresh_preset_combo(select=name)
        self._persist_presets()

    def _delete_preset(self) -> None:
        name = self.preset_combo.currentText()
        if not name:
            QMessageBox.information(self, "提示", "没有可删除的预设。")
            return
        confirm = QMessageBox.question(
            self, "删除预设", f"删除预设「{name}」？"
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self._presets.pop(name, None)
        self._refresh_preset_combo()
        self._persist_presets()

    def _load_preset(self, index: int) -> None:
        if index < 0:
            return
        name = self.preset_combo.itemText(index)
        config = self._presets.get(name)
        if config is not None:
            self.request_panel.apply_config(config)

    def _refresh_preset_combo(self, select: str | None = None) -> None:
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        names = sorted(self._presets)
        self.preset_combo.addItems([""] + names)
        if select and select in names:
            self.preset_combo.setCurrentText(select)
        else:
            self.preset_combo.setCurrentIndex(0)
        self.preset_combo.blockSignals(False)

    # ---- 状态持久化 ---------------------------------------------------------

    def _restore_state(self) -> None:
        settings = QSettings(SETTINGS_ORG, SETTINGS_APP)
        geometry = settings.value("geometry")
        if isinstance(geometry, (QByteArray, bytes, bytearray)):
            self.restoreGeometry(QByteArray(geometry))

        raw_presets = settings.value("presets", "")
        if isinstance(raw_presets, str) and raw_presets:
            try:
                items = json.loads(raw_presets)
            except json.JSONDecodeError:
                items = None
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict):
                        config = EndpointConfig.from_dict(item)
                        self._presets[config.name] = config
        self._refresh_preset_combo()

        raw_last = settings.value("last_config", "")
        if isinstance(raw_last, str) and raw_last:
            try:
                last = json.loads(raw_last)
            except json.JSONDecodeError:
                last = None
            if isinstance(last, dict):
                self.request_panel.apply_config(EndpointConfig.from_dict(last))

    def _persist_presets(self) -> None:
        settings = QSettings(SETTINGS_ORG, SETTINGS_APP)
        settings.setValue(
            "presets",
            json.dumps(
                [c.to_dict() for c in self._presets.values()],
                ensure_ascii=False,
            ),
        )

    def _persist_all(self) -> None:
        self._persist_presets()
        settings = QSettings(SETTINGS_ORG, SETTINGS_APP)
        settings.setValue("last_config", json.dumps(self.request_panel.collect_config().to_dict()))
        settings.setValue("geometry", self.saveGeometry())

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if self._worker is not None:
            self._worker.cancel()
            self._worker.wait(3000)
        self._persist_all()
        super().closeEvent(event)
