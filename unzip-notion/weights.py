import os
import re

from logger import logger
from utils import set_exit_status, replace_match

MARKDOWN_DIR_LINK_PATTERN = re.compile(b"\\[(?P<name>[^]]*)]\\((?P<url>[^)/]+)\\)")

DEFAULT_WEIGHT = b"99"


def link_order_from_index_file(
    index_file_path: bytes, markdown_dir: bytes
) -> list[bytes]:
    with open(index_file_path, "rb") as index_file:
        content = index_file.read()
    link_order = list()
    for re_match in MARKDOWN_DIR_LINK_PATTERN.finditer(content):
        link_target = re_match.group("url")
        if link_target in link_order or not os.path.isdir(
            os.path.join(markdown_dir, link_target)
        ):
            continue
        logger.debug(f"Found direct link in {index_file_path}: {link_target}")
        link_order.append(link_target)
    return link_order


def set_page_weight(target_file_path, link_weight):
    if not os.path.isfile(target_file_path):
        if not target_file_path.endswith(b".png"):
            logger.error(
                f"File (target of link) {target_file_path} does not exist. "
                f"Its weight should have been: {link_weight}"
            )
            set_exit_status(1)
        return
    with open(target_file_path, "rb") as target_file:
        content = target_file.read()
    weight_re_match = re.search(
        b"^weight: " + DEFAULT_WEIGHT + b"$", content, re.MULTILINE
    )
    if weight_re_match is None:
        logger.warning(f"No weight tag found for {target_file_path}")
        return
    content, __ = replace_match(
        weight_re_match, b"weight: " + bytes(str(link_weight), "utf-8"), content
    )
    with open(target_file_path, "wb") as target_file:
        target_file.write(content)
    logger.debug(f"Updated weight to {link_weight} for {target_file_path}")


def set_weights(markdown_dir: bytes, depth: int = 0) -> None:

    for name in os.listdir(markdown_dir):
        child_path = os.path.join(markdown_dir, name)
        if os.path.isdir(child_path):
            set_weights(child_path, depth + 1)

    # set weights of pages depending on the order of appearance of the links
    # in the current directory's _index.md file
    if depth != 0:
        child_path = os.path.join(markdown_dir, b"_index.md")
        try:
            # figure out the order of links in the current file
            link_order = link_order_from_index_file(child_path, markdown_dir)
        except FileNotFoundError:
            logger.error(
                f"Failed to parse {child_path} to set the children's weights. File does not exist"
            )
            set_exit_status(1)
        else:
            # iterate once again over the all subdirectories and write weight values
            for link_weight, link_target in enumerate(link_order, 1):
                logger.debug(f"Setting weight of {link_target} to {link_weight}")
                set_page_weight(
                    os.path.join(markdown_dir, link_target, b"_index.md"), link_weight
                )
