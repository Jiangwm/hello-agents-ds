"""Windows 下安全解码命令输出的 TerminalTool。

控制台默认编码常为 GBK，源文件内容常为 UTF-8。库里的 TerminalTool
用系统编码严格解码，遇到非法字节会丢掉整段输出。
"""

import os
import subprocess
from typing import Optional

from hello_agents.tools import TerminalTool


def decode_command_output(data: Optional[bytes]) -> str:
    """先按 UTF-8 解码，失败再按 GBK，仍失败则替换无法识别的字节。"""
    if not data:
        return ""
    for encoding in ("utf-8", "gbk"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


class CodebaseTerminalTool(TerminalTool):
    """按字节捕获命令输出，再选择合适的编码解码。"""

    def _execute_command(self, command: str) -> str:
        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=str(self.current_dir),
                capture_output=True,
                timeout=self.timeout,
                env=os.environ.copy(),
            )
            output = decode_command_output(result.stdout)
            stderr = decode_command_output(result.stderr)
            if stderr:
                output += f"\n[stderr]\n{stderr}"

            if len(output) > self.max_output_size:
                output = output[:self.max_output_size]
                output += f"\n\n⚠️ 输出被截断（超过 {self.max_output_size} 字节）"

            if result.returncode != 0:
                output = f"⚠️ 命令返回码: {result.returncode}\n\n{output}"

            return output if output else "✅ 命令执行成功（无输出）"
        except subprocess.TimeoutExpired:
            return f"❌ 命令执行超时（超过 {self.timeout} 秒）"
        except Exception as e:
            return f"❌ 命令执行失败: {e}"
