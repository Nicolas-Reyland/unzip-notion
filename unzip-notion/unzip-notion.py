import argparse
import logging
import re
import os
import shutil
import sys
import tempfile
import urllib
import urllib.parse
import zipfile

from logger import logger
from weights import set_weights, DEFAULT_WEIGHT
from utils import get_exit_status, replace_match


FILE_HASH_SUFFIX_PATTERN = re.compile(b"(.*)( [0-9a-z]{32})(\\.md)?$")
MARKDOWN_HASH_SUFFIX_PATTERN = re.compile(b"%20[0-9a-z]{32}")
MARKDOWN_MD_LINK_PATTERN = re.compile(b"\\[(?P<name>[^]]*)]\\((?P<url>[^)]*\\.md)\\)")
# This also matches markdown files. Therefore, markdown files should be processed before
MARKDOWN_RESOURCE_LINK_PATTERN = re.compile(
    b"\\[(?P<name>[^]]*)]\\((?P<url>[^)]*\\.(?!md)[^.\n)]+)\\)"
)
MARKDOWN_H1_PATTERN = re.compile(b"^# +(?P<title>.+)\r?(\n|$)")
MARKDOWN_CRIT_PATTERN = re.compile(b"~~[ \t]*(?P<crit_group>crit[ \t]+[^~]+)~~[ \t]*\n?")
MARKDOWN_CRIT_PATTERN_SELF = re.compile(b"crit[ \t]+(?P<crit>((?!crit)[^~])+)")

g_all_dm_tags = dict()
g_dm_tags = dict()


def repair_name(name: bytes) -> bytes:
    """
    Removes the hash suffix and replaces accents in file names.

    :param name: Name of a file.
    :return: Repaired name of the file.
    """
    return re.sub(
        FILE_HASH_SUFFIX_PATTERN,
        b"\\1\\3",
        name.replace(b"e\xa6\xfc", b"\xc3\xa9")
        .replace(b"e\xcc\x81", b"\xc3\xa9")
        .replace(b"\xc3\xa9", b"e")
        .replace(b"\xc3\xa7", b"c")
        .replace(b"\xe2\x80\x99", b"_"),
    )


def repair_url_part(url_part: bytes) -> bytes:
    """
    Repair an url, or a part of an url that points to a markdown file.
    Characters such as spaces and accents (mostly french) are removed.

    :param url_part: Part of an url.
    :return: Repaired url.
    """
    url_part = re.sub(MARKDOWN_HASH_SUFFIX_PATTERN, b"", url_part)
    url_part = url_part.lower()
    for old, new in [(b"%20", b" "), (b"e%cc%81", b"e"), (b"%e2%80%99", b"_"), (b"&", b"et"), (b",", b"_")]:
        url_part = url_part.replace(old, new)
    url_words = url_part.split(b" ")
    return b"-".join(filter(lambda word: word != b"-", url_words))


def repair_link(
    link_match: re.Match[bytes],
    old_link_prefix: bytes,
    parent_link_prefixes: list[bytes] | None = None,
    url_prefix: list[bytes] | None = None,
    md_link: bool = True,
) -> bytes:
    """
    Repair a Markdown or Resource link.

    :param link_match: regex match object for a Markdown or Resource link
    :param old_link_prefix: prefix that must be removed
    :param parent_link_prefixes: prefixes that are not valid
    :param url_prefix: a prefix to add the resulting url
    :param md_link: the match matches a Markdown link (not a Resource link)
    :return: the link with a repaired url part
    """
    if not parent_link_prefixes:
        parent_link_prefixes = []
    if not url_prefix:
        url_prefix = []

    name = link_match.group("name")
    url = link_match.group("url")

    try:
        url_parse_result = urllib.parse.urlparse(url)
    except UnicodeDecodeError as err:
        print(f'Failed to parse "{url}" with urllib.parse.urlparse', file=sys.stderr)
    else:
        if all([url_parse_result.scheme, url_parse_result.netloc]):
            return link_match.group(0)

    if md_link:
        url = url.removesuffix(b".md")
    url_parts = url.removeprefix(b"/").split(b"/")

    # Rebuild new url
    new_url_parts = list(map(repair_url_part, url_prefix + url_parts))
    if new_url_parts:
        if new_url_parts[0] == old_link_prefix:
            new_url_parts.pop(0)
        elif new_url_parts[0] == b".." or new_url_parts[0] in parent_link_prefixes:
            new_url_parts.insert(0, b"..")

    new_url = b"/".join(new_url_parts)
    logger.debug(f"Fixing link: {new_url} (old: {url})")
    return b"[" + name + b"](" + new_url + b")"


def repair_content(
    content: bytes,
    src: bytes,
    dst: bytes,
    resource_dir_names: list[bytes] | None = None,
) -> tuple[bytes, set[bytes]]:
    """
    The content of a markdown file is 'repaired'. The following changes are applied:
     - The taxonomies (https://gohugo.io/content-management/taxonomies/) for Hugo are added at the beginning of the file
     - Removal of the file title (if present in markdown). It is then added to the taxonomy section
     - All the links and references to other markdown pages are changed to reflect the path changes
     - Processing of the tags
    The content is supposed to be from the file located at `src` and is destined to be written to the file located
    at `dst`. This is not done by this function, but these arguments are useful for repairing links and references
    to other markdown files.

    :param content: Content of a markdown source file.
    :param src: Path of the source file.
    :param dst: Destination path of the markdown file.
    :param resource_dir_names: Directory names of other markdown resource files.
    :return: A tuple. The first element is the output content. The second element is the tags that have been found in the content.
    """
    file_basename = os.path.basename(src).removesuffix(b".md")

    # extract title
    title_match = MARKDOWN_H1_PATTERN.search(content)
    if title_match:
        title = title_match.group("title")
        content = content[title_match.end() :]
    else:
        logger.warning(
            f"Could not find title for {dst}. Using file basename {file_basename}"
        )
        title = file_basename

    old_link_prefix = repair_url_part(
        bytes(urllib.parse.quote_from_bytes(file_basename), "utf-8")
    )
    # repair Resource links
    resource_match_offset = 0
    for re_match_group in MARKDOWN_RESOURCE_LINK_PATTERN.finditer(content):
        repaired_link = repair_link(
            re_match_group, old_link_prefix, resource_dir_names, md_link=False
        )
        content, resource_match_offset = replace_match(
            re_match_group, repaired_link, content, resource_match_offset
        )

    # repair Markdown links
    md_match_offset = 0
    for re_match_group in MARKDOWN_MD_LINK_PATTERN.finditer(content):
        repaired_link = repair_link(re_match_group, old_link_prefix, resource_dir_names)
        content, md_match_offset = replace_match(
            re_match_group, repaired_link, content, md_match_offset
        )

    # tags & crit
    tags: set[bytes] = set()
    crit_match_offset = 0
    for re_match_group in MARKDOWN_CRIT_PATTERN.finditer(content):
        crit_group = re_match_group.group("crit_group").strip().replace(b'"', b"").replace(b"\n", b"")
        # remove links INSIDE crits/tags (yes, sometimes that happens :/)
        crit_link_match_offset = 0
        for link_inside_crit_match in MARKDOWN_RESOURCE_LINK_PATTERN.finditer(crit_group):
            crit_group, crit_link_match_offset = replace_match(
                link_inside_crit_match,
                link_inside_crit_match.group("name"),
                crit_group,
                crit_link_match_offset,
            )
        md_crit_list: list[bytes] = list()
        for re_match in MARKDOWN_CRIT_PATTERN_SELF.finditer(crit_group):
            crit = re_match.group("crit").strip()
            tags.add(crit)
            md_crit_list.append(b'{{< crit "' + crit + b'" >}}')
        content, crit_match_offset = replace_match(
            re_match_group, b"\n".join(md_crit_list), content, crit_match_offset
        )

    logger.debug(f"Found {len(tags)} tags")
    slug = urllib.parse.unquote_to_bytes(
        repair_url_part(old_link_prefix).removesuffix(b".md")
    )

    return (
        b"""---
title: """
        + title
        + b"""
slug: """
        + slug
        + b"""
weight: """
        + DEFAULT_WEIGHT
        + b"""
tags: [ """
        + b", ".join(b'"' + tag + b'"' for tag in tags)
        + b""" ]
---

"""
        + content,
        tags,
    )


def copy_file(
    src: bytes,
    dst: bytes,
    resource_dir_names: list[bytes] | None = None,
    force: bool = False,
) -> set[bytes]:
    """
    Copy a file (src) to a destination path (dst). The content of the source file is modified and 'repaired'
    using the `repair_content` function.

    :param src: Source path.
    :param dst: Destination path.
    :param resource_dir_names: The resource directories found in the same directory as the source file.
    :param force: The destination file can be overwritten.
    :return: The list of tags found in the source file.
    """
    if os.path.exists(dst) and not force:
        raise RuntimeError(f'File "{dst}" already exists. Use --force to overwrite')

    if resource_dir_names is None:
        resource_dir_names = []

    with open(src, "rb") as infile, open(dst, "wb") as outfile:
        content = infile.read()
        repaired_content, tags = repair_content(content, src, dst, resource_dir_names)
        outfile.write(repaired_content)

    return tags


def write_dm_tags_section(markdown_dir: bytes, tags: dict[bytes, bytes]) -> None:
    """
    Write the `tags` section of the DM header page. This function should be executed on each DM page.
    The content is added at the end of the markdown file. Two newline characters are added before
    the tags contents.

    :param markdown_dir: The directory in which the '_index.md' file is located.
    :param tags: A dictionary of tags. The keys are the tag names and the
    values are the tag links (references to other markdown files).
    """
    index_file_path = os.path.join(markdown_dir, b"_index.md")
    additional_content = """

## Critères

""".encode(
        "utf-8"
    )
    for tag_index, (tag_name, tag_value) in enumerate(tags.items(), 1):
        additional_content += (
            bytes(f"{tag_index}. [", "utf-8")
            + tag_name
            + b"](/"
            + tag_value
            + b"#"
            + bytes(urllib.parse.quote_plus(tag_name.decode("utf-8")), "utf-8")
            + b")\n"
        )
    with open(index_file_path, "ab") as index_file:
        index_file.write(additional_content)


def extract_resource_dir_names(file_or_dir_names: list[bytes]) -> list[bytes]:
    """
    Extract the resource directory names from a list of files and directories.
    Markdown files will eventually be moved to a directory with the same name (without the .md suffix).
    This function returns a list of directory names, which can be used in hyperlinks and other references.

    :param file_or_dir_names: Names of files and directories.
    :return: List of directory names.
    """
    resource_dir_names: list[bytes] = list()
    for name in file_or_dir_names:
        if os.path.isdir(name):
            resource_dir_names.append(name)
        elif name.endswith(b".md"):
            resource_dir_names.append(name.removesuffix(b".md"))
    resource_dir_names = list(
        map(
            lambda resource_dir_name: repair_url_part(repair_name(resource_dir_name)),
            resource_dir_names,
        )
    )
    return resource_dir_names


def beautify(
    base_markdown_dir: bytes,
    input_dir: bytes,
    markdown_dir: bytes,
    static_dir: bytes,
    force: bool = False,
    depth: int = 0,
) -> None:
    """
    Beautifies content from the `input_dir` and writes the output to the `markdown_dir` and `static_dir`.

    :param base_markdown_dir: Initial value of the `markdown_dir`.
    Since this is a recursive function, the value of `markdown_dir` will change.
    :param input_dir: Path to the directory that is currently being processed.
    :param markdown_dir: Path to the output directory for the markdown files.
    :param static_dir: Path to the output directory for the static files.
    :param force: Overwrite existing files.
    :param depth: Current depth (this is a recursive function).
    """
    global g_all_dm_tags, g_dm_tags

    def verb(*a):
        logger.debug(" " * depth + " ".join(a))

    markdown_dir_basename = os.path.basename(markdown_dir)
    is_dm_dir = depth == 2 and markdown_dir_basename.startswith(b"dm-")
    if is_dm_dir:
        g_dm_tags = dict()

    if not os.path.isdir(markdown_dir):
        os.mkdir(markdown_dir)
    elif not force:
        raise RuntimeError(
            f'Directory "{markdown_dir}" already exists. Use --force to overwrite'
        )

    if not os.path.isdir(static_dir):
        os.mkdir(static_dir)
    elif not force:
        raise RuntimeError(
            f'Directory "{static_dir}" already exists. Use --force to overwrite'
        )

    verb(f"input: {input_dir}, output: {markdown_dir}")

    names = os.listdir(input_dir)
    resource_dir_names = extract_resource_dir_names(names)

    # process directories first, then files
    for name in sorted(names, key=lambda name_: 0 if os.path.isdir(name_) else 1):
        # the first directory should not create a subdirectory
        if depth != 0:
            # markdown directory
            repaired_name = repair_name(name).removesuffix(b".md")
            repaired_name = repair_url_part(repaired_name)
            markdown_repaired_dir = os.path.join(markdown_dir, repaired_name)
            # static directory
            static_repaired_dir = os.path.join(static_dir, repaired_name)

            verb(f"looking at {name} (repaired: {repaired_name})")
        else:
            markdown_repaired_dir = markdown_dir
            static_repaired_dir = static_dir
        path = os.path.join(input_dir, name)

        if os.path.isdir(path):
            verb(f"{os.path.basename(path)}: directory")
            beautify(
                base_markdown_dir,
                path,
                markdown_repaired_dir,
                static_repaired_dir,
                force,
                depth + 1,
            )
        elif os.path.isfile(path):
            if name.endswith(b".md"):
                if not os.path.isdir(markdown_repaired_dir):
                    os.mkdir(markdown_repaired_dir)
                verb(f"{os.path.basename(path)}: root markdown file")
                if dst_file_tags := copy_file(
                    path,
                    os.path.join(markdown_repaired_dir, b"_index.md"),
                    resource_dir_names,
                    force,
                ):
                    # register all tags with the right markdown directory
                    tag_value = os.path.relpath(markdown_repaired_dir, base_markdown_dir)
                    for tag in dst_file_tags:
                        g_dm_tags[tag] = tag_value
            else:
                # verb(f"{os.path.basename(path)}: resource file")
                shutil.copy(path, static_repaired_dir)
        else:
            print(f"{path}: unknown type")

    # collect tags
    if depth == 2 and markdown_dir_basename.startswith(b"dm-"):
        g_all_dm_tags[markdown_dir_basename] = g_dm_tags.copy()

def main():
    parser = argparse.ArgumentParser(description="unzip notion exports")
    parser.add_argument(
        "-c",
        "--clean",
        action="store_true",
        help="Clean output 'content' and 'static' " "directories beforehand",
    )
    parser.add_argument(
        "--clean-content",
        action="store_true",
        help="Clean output 'content' directory beforehand",
    )
    parser.add_argument(
        "--clean-static",
        action="store_true",
        help="Clean output 'static' directory beforehand",
    )
    parser.add_argument(
        "-s", "--source", action="store_true", help="Input is the unzipped directory"
    )
    parser.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="Overwrite existing files in the hugo directory",
    )
    parser.add_argument(
        "-o",
        "--overwrite",
        type=str,
        help="Path to an existing hugo directory containing the "
        "content and static folders (and possibly a hugo.toml "
        "file) to overwrite generated files.",
    )
    parser.add_argument(
        "--keep-tmp-folder",
        action="store_true",
        help="Don't remove the temp folder at the end",
    )
    parser.add_argument(
        "--dm",
        action="store_true",
        help="The input zip file (or source directory) is a sub-section, a DM (fr. Devoir Maison)",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Increase verbosity"
    )
    parser.add_argument("input")
    parser.add_argument("hugo_dir")

    args = parser.parse_args()

    # Verbose
    if args.verbose:
        logger.setLevel(logging.DEBUG)

    # Argument processing
    content_overwrite: str | None = None
    static_overwrite: str | None = None
    toml_overwrite: str | None = None
    if args.overwrite:
        if not os.path.isdir(args.overwrite):
            raise NotADirectoryError(f"{args.overwrite} (--overwrite)")
        content_overwrite = os.path.join(args.overwrite, "content")
        if not os.path.isdir(content_overwrite):
            logger.warning(
                f'Did not find the "content" directory in "{args.overwrite}". Creating it.'
            )
        static_overwrite = os.path.join(args.overwrite, "content")
        if not os.path.isdir(static_overwrite):
            logger.warning(
                f'Did not find the "static" directory in "{args.overwrite}". Creating it.'
            )
        toml_overwrite = os.path.join(args.overwrite, "hugo.toml")
        if not os.path.isfile(toml_overwrite):
            logger.info(f'Did not find the "hugo.toml" file in "{args.overwrite}".')
            toml_overwrite = None
    if args.source and args.keep_tmp_folder:
        raise ValueError(
            f"When using '--source', you don't use a tmp folder for extracting a zip file."
        )

    input_dir: bytes
    tmp_folder: tempfile.TemporaryDirectory[str] | None = None
    if args.source:
        if not os.path.isdir(args.input):
            raise NotADirectoryError("Input is not a directory")
        input_dir = bytes(args.input, "utf-8")
    else:
        if not os.path.isfile(args.input):
            raise FileNotFoundError("Input is not a file")
        tmp_folder = tempfile.TemporaryDirectory(prefix="unzip-notion-")
        with zipfile.ZipFile(args.input, "r") as zip_ref:
            zip_ref.extractall(tmp_folder.name)
        input_dir = bytes(tmp_folder.name, "utf-8")

    # Generate content and static directories
    output_dir = bytes(args.hugo_dir, "utf-8")
    content_output_dir = os.path.join(output_dir, b"content")
    static_output_dir = os.path.join(output_dir, b"static")
    dm_content_output_dir: bytes | None = None
    dm_static_output_dir: bytes | None = None

    if args.dm:
        logger.debug("DM mode is enabled. Trying to figure out what the output path is...")
        dm_files = set(
            filter(lambda f: f.endswith(b".md") and os.path.isfile(os.path.join(input_dir, f)), os.listdir(input_dir)))
        if len(dm_files) != 1:
            logger.error(f"No files in '{input_dir}'. Please make sure this is the export of a DM.")
            raise RuntimeError(f"'{input_dir}' is not a DM export.")
        dm_folder_name = repair_url_part(repair_name(dm_files.pop())).removesuffix(b".md")
        dm_content_output_dir = os.path.join(content_output_dir, dm_folder_name)
        dm_static_output_dir = os.path.join(static_output_dir, dm_folder_name)
        logger.info(f"DM content output path is '{dm_content_output_dir}' and static path is '{dm_static_output_dir}'")

    # clean output content dir
    if args.clean or args.clean_content:
        if not args.dm:
            logger.info(f"Clearing output 'content' dir: {content_output_dir}")
            shutil.rmtree(content_output_dir, ignore_errors=True)
        else:
            logger.info(f"Clearing output 'content' subdir (DM mode): {dm_content_output_dir}")
            shutil.rmtree(dm_content_output_dir, ignore_errors=True)
    if args.clean or args.clean_static:
        if not args.dm:
            logger.info(f"Clearing output 'static' dir: {static_output_dir}")
            shutil.rmtree(static_output_dir, ignore_errors=True)
        else:
            logger.info(f"Clearing output 'static' subdir (DM mode): {dm_static_output_dir}")
            shutil.rmtree(dm_static_output_dir, ignore_errors=True)

    # main function
    if not args.dm:
        beautify(
            content_output_dir, input_dir, content_output_dir, static_output_dir, args.force
        )
    else:
        beautify(
            content_output_dir, input_dir, content_output_dir, static_output_dir, args.force, depth=1
        )

    # write tags
    for dm_dir_name, tag_dict in g_all_dm_tags.items():
        logger.debug(f"Found {len(g_dm_tags)} tags for {dm_dir_name}")
        write_dm_tags_section(os.path.join(content_output_dir, dm_dir_name), tag_dict)

    # write weights
    set_weights(content_output_dir)

    # Clean up file generation
    if tmp_folder:
        if args.keep_tmp_folder:
            print(f"Not removing the tmp folder: {tmp_folder}")
            shutil.copytree(tmp_folder.name, os.path.join("/tmp", "unzip-notion"))
        else:
            tmp_folder.cleanup()

    # Overwrite
    # This code is written in a way that makes future implementations of the --overwrite option easier
    if content_overwrite:
        logger.info("Overwriting content directory with pre-existing files")
        shutil.copytree(
            content_overwrite, content_output_dir.decode("utf-8"), dirs_exist_ok=True
        )
    if static_overwrite:
        logger.info("Overwriting static directory with pre-existing files")
        shutil.copytree(
            static_overwrite, static_output_dir.decode("utf-8"), dirs_exist_ok=True
        )
    if toml_overwrite:
        logger.info("Overwriting toml file")
        shutil.copy(toml_overwrite, args.hugo_dir)

    return get_exit_status()


if __name__ == "__main__":
    sys.exit(main())
