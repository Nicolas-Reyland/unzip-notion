import re

g_exit_status = 0

def set_exit_status(exit_status: int):
    global g_exit_status
    g_exit_status = exit_status


def get_exit_status() -> int:
    return g_exit_status


def replace_match(
        match: re.Match[bytes], repl: bytes, content: bytes, match_offset: int = 0
) -> tuple[bytes, int]:
    content = (
            content[: match.start() + match_offset]
            + repl
            + content[match.end() + match_offset :]
    )
    match_offset += len(repl) - match.end() + match.start()
    return content, match_offset


