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
    """
    Replace the content matched by the regex `match` object with `repl`.
    This function should be used in a loop, replacing a list of regex matches on the original value of `content`.
    The `match_offset` starts at zero and keeps up with the changes in the `content` variable. This is useful
    because the `match` start and end indices are invalidated once the `repl` has a different length than the
    matched content.

    :param match: Regex match object (on the original content, not updated between calls to this function).
    :param repl: Replacement bytes.
    :param content: Content in which the bytes matched by `match` are replaced with `repl`.
    :param match_offset: Offset used to compute the correct start and end positions of the `match`.
    :return: A tuple. The first element is the new content. The second element is new `match_offset`.
    """
    content = (
        content[: match.start() + match_offset]
        + repl
        + content[match.end() + match_offset :]
    )
    match_offset += len(repl) - match.end() + match.start()
    return content, match_offset
