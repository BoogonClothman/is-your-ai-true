"""is-your-ai-true：基于 PyQt6 的 AI 端点测试工具。

支持 OpenAI Chat Completions、OpenAI Responses 与 Anthropic Messages
三种协议，提供流式输出渲染与性能/用量统计。
"""

import sys

from PyQt6.QtWidgets import QApplication

from .ui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("is-your-ai-true")
    app.setApplicationDisplayName("Is Your AI True")
    app.setOrganizationName("IsYourAITrue")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
