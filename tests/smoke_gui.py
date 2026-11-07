"""GUI 冒烟（offscreen）：实例化主窗口并执行常用面板交互，不弹真实窗口。

覆盖单元测试触碰不到的 UI 路径：窗口构造、协议切换、消息编辑、
预设保存/读取、响应面板渲染。QSettings 定向到临时 ini 文件，
避免污染真实注册表。

运行：uv run python tests/smoke_gui.py
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QSettings, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication, QInputDialog, QMessageBox  # noqa: E402

from is_your_ai_true.core.models import (  # noqa: E402
    PROTOCOL_ANTHROPIC,
    PROTOCOL_OPENAI_CHAT,
    PROTOCOL_OPENAI_RESPONSES,
    ChatMessage,
    EndpointConfig,
)
from is_your_ai_true.ui import main_window as main_window_module  # noqa: E402
from is_your_ai_true.ui.main_window import MainWindow  # noqa: E402

# 让弹窗不再阻塞：保存/删除预设与确认框直接返回预设结果
QInputDialog.getText = staticmethod(  # type: ignore[method-assign]
    lambda *args, **kwargs: ("冒烟预设", True)
)
QMessageBox.question = staticmethod(  # type: ignore[method-assign]
    lambda *args, **kwargs: QMessageBox.StandardButton.Yes
)


def main() -> int:
    tmp_dir = Path(tempfile.mkdtemp(prefix="iyat-smoke-"))
    ini_path = tmp_dir / "settings.ini"

    # MainWindow 内部用原生格式（Windows 注册表）的 QSettings；
    # 这里替换成指向临时 ini 的实现，避免冒烟污染真实用户设置。
    class TmpSettings(QSettings):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(str(ini_path), QSettings.Format.IniFormat)

    original_settings = main_window_module.QSettings
    main_window_module.QSettings = TmpSettings

    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    failures: list[str] = []

    panel = window.request_panel
    response = window.response_panel

    def check(name: str, condition: bool, detail: str = "") -> None:
        if not condition:
            failures.append(f"{name}{(' — ' + detail) if detail else ''}")

    try:
        # 1) 三种协议各切换一次，验证默认 URL 与模型候选跟随
        for index in range(panel.protocol_combo.count()):
            panel.protocol_combo.setCurrentIndex(index)
        check(
            "协议切换填默认 URL",
            panel.base_url_edit.text().startswith("https://api."),
            panel.base_url_edit.text(),
        )
        check("协议切换填模型候选", panel.model_combo.count() > 0)

        # 2) 消息编辑：添加两条、删一条
        panel.content_edit.setText("第一条")
        panel._add_message()
        panel.content_edit.setText("第二条")
        panel._add_message()
        panel.message_list.setCurrentRow(0)
        panel._remove_selected_message()
        messages = panel.collect_messages()
        check("消息增删", len(messages) == 1 and messages[0].content == "第二条")

        # 3) 参数收集：取消勾选 temperature 后应收集为 None
        panel.temperature_check.setChecked(False)
        params = panel.collect_params()
        check("参数勾选控制", params.temperature is None and params.stream)

        # 4) apply_config 回填
        sent = EndpointConfig(
            protocol=PROTOCOL_ANTHROPIC,
            base_url="https://example.com",
            api_key="sk-x",
            model="claude-test",
            timeout=30,
        )
        panel.apply_config(sent)
        got = panel.collect_config()
        check("apply_config 回填", got.base_url == "https://example.com")
        check("apply_config 回填 API Key", got.api_key == "sk-x")

        # 5) 预设保存 -> 下拉列出 -> 选中后回填
        window._save_preset()
        check("预设保存后下拉包含", "冒烟预设" in [window.preset_combo.itemText(i) for i in range(window.preset_combo.count())])
        window.preset_combo.setCurrentText("冒烟预设")
        loaded = panel.collect_config()
        check("预设回填匹配", loaded.protocol == sent.protocol and loaded.model == sent.model, str(loaded))

        # 6) 响应面板：增量、思维链、统计、核验、错误、重置
        response.prepare()
        response.append_delta("你好，**世界**")
        response.append_delta("```python\nprint(1)\n```")
        response.append_reasoning("先想一下，**重点是**这个")
        response._flush_render()
        rendered = response.render_view.toHtml()
        check("思维链进入渲染区", "思考过程" in rendered and "先想一下" in rendered)
        rendered_text = response.render_view.toPlainText()
        check("正文与代码块进入渲染区", "你好" in rendered_text and "print(1)" in rendered_text)
        response.set_usage(10, 20)
        record = {
            "status": "success",
            "requested_model": "gpt-4o",
            "reported_model": "gpt-4o",
            "model_match": True,
            "response_id": "chatcmpl-x",
            "system_fingerprint": "fp_x",
            "service_tier": "default",
            "archive_path": str(tmp_dir / "records" / "responses.jsonl"),
        }
        response.show_record(record)
        check("核验一致显示", "一致" in response.model_check_label.text())
        record["reported_model"] = "gpt-4o-mini"
        record["model_match"] = False
        response.show_record(record)
        check(
            "核验不一致告警",
            "不一致" in response.model_check_label.text()
            and "#b00020" in response.model_check_label.styleSheet(),
        )
        check("指纹显示", response.fingerprint_label.text() == "fp_x")
        check("归档路径显示", "responses.jsonl" in response.archive_path_label.text())
        response.show_stats({"首 token (TTFT)": "120 ms", "吞吐": "5.0 tok/s"})
        response.show_error("模拟错误", '{"error": "x"}')
        check("错误提示进入视图", "模拟错误" in response.render_view.toHtml())
        response.reset()
        check("重置后缓冲清空", response.render_view.toPlainText() == "")
        check("重置后核验区清空", response.model_check_label.text() == "--")

        # 7) 运行状态切换
        panel.set_running(True)
        check("运行态按钮", not panel.send_button.isEnabled() and panel.stop_button.isEnabled())
        panel.set_running(False)

        # 8) 停止后再发一次留给异步渲染定时器
        response.append_delta("# 标题")
    except Exception as exc:  # noqa: BLE001 - 冒烟要捕获任何 UI 异常
        failures.append(f"UI 交互抛异常：{exc.__class__.__name__}: {exc}")

    # 让节流定时器跑完最终渲染，再退出
    QTimer.singleShot(300, app.quit)
    app.exec()
    # 主动关闭窗口：closeEvent 走 TmpSettings 写入临时 ini，
    # 避免窗口析构时再用原始 QSettings 污染注册表
    window.close()
    main_window_module.QSettings = original_settings

    for missing in [p for p in tmp_dir.rglob("*.ini")]:
        print(f"  预设文件: {missing.name}")

    if failures:
        for item in failures:
            print(f"FAIL: {item}")
        return 1
    print("PASS: GUI 冒烟（窗口构造 / 协议切换 / 消息编辑 / 预设 / 渲染面板）全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
