#!/usr//bin/env python3
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

FILE_HASH_SUFFIX_PATTERN = re.compile(b'(.*)( [0-9a-z]{32})(\\.md)?$')
MARKDOWN_HASH_SUFFIX_PATTERN = re.compile(b'%20[0-9a-z]{32}')
MARKDOWN_MD_LINK_PATTERN = re.compile(b'\\[(?P<name>[^]]*)]\\((?P<url>[^)]*\\.md)\\)')
MARKDOWN_DIR_LINK_PATTERN = re.compile(b'\\[(?P<name>[^]]*)]\\((?P<url>[^)/]+)\\)')
# This also matches markdown files. Therefore, markdown files should be processed before
MARKDOWN_RESOURCE_LINK_PATTERN = re.compile(b'\\[(?P<name>[^]]*)]\\((?P<url>[^)]*\\.(?!md)[^.\n)]+)\\)')
MARKDOWN_H1_PATTERN = re.compile(b'^# +(?P<title>.+)\r?\n')
MARKDOWN_CRIT_PATTERN = re.compile(b'~~[ \t]*crit[ \t]+(?P<crit>[^~]+)~~[ \t]*\n?')

DEFAULT_WEIGHT = b'99'

g_all_dm_tags = dict()
g_dm_tags = dict()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger()


def replace_match(match: re.Match[bytes], repl: bytes, content: bytes, match_offset: int = 0) -> tuple[bytes, int]:
    content = content[:match.start() + match_offset] + repl + content[match.end() + match_offset:]
    match_offset += len(repl) - match.end() + match.start()
    return content, match_offset


def repair_name(name: bytes) -> bytes:
    return re.sub(
        FILE_HASH_SUFFIX_PATTERN,
        b'\\1\\3',
        name.replace(b'e\xa6\xfc', b'\xc3\xa9')
            .replace(b'e\xcc\x81', b'\xc3\xa9')
            .replace(b'\xc3\xa9', b'e')
            .replace(b'\xc3\xa7', b'c')
            .replace(b'\xe2\x80\x99', b"_")
    )


def repair_url_part(url_part: bytes) -> bytes:
    url_part = re.sub(MARKDOWN_HASH_SUFFIX_PATTERN, b'', url_part)
    url_part = url_part.lower()
    for old, new in [(b'%20', b' '), (b'e%cc%81', b'e'), (b"%e2%80%99", b'_')]:
        url_part = url_part.replace(old, new)
    url_words = url_part.split(b' ')
    return b'-'.join(filter(lambda word: word != b'-', url_words))


def repair_link(
        link_match: re.Match[bytes],
        old_link_prefix: bytes,
        parent_link_prefixes: list[bytes] | None = None,
        url_prefix: list[bytes] | None = None,
        md_link: bool = True
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

    name = link_match.group('name')
    url = link_match.group('url')

    url_parse_result = None
    try:
        url_parse_result = urllib.parse.urlparse(url)
    except UnicodeDecodeError as err:
        print(f'Failed to parse "{url}" with urllib.parse.urlparse', file=sys.stderr)
    if url_parse_result and all([url_parse_result.scheme, url_parse_result.netloc]):
        return link_match.group(0)

    if md_link:
        url = url.removesuffix(b'.md')
    url_parts = url.removeprefix(b'/').split(b'/')

    # Rebuild new url
    new_url_parts = list(map(repair_url_part, url_prefix + url_parts))
    if new_url_parts:
        if new_url_parts[0] == old_link_prefix:
            new_url_parts.pop(0)
        elif new_url_parts[0] == b'..' or new_url_parts[0] in parent_link_prefixes:
            new_url_parts.insert(0, b'..')

    new_url = b'/'.join(new_url_parts)
    logger.debug(f'Fixing link: {new_url} (old: {url})')
    return b'[' + name + b'](' + new_url + b')'


def repair_content(
        content: bytes,
        src: bytes,
        _: bytes,
        resource_dir_names: list[bytes] | None = None) -> tuple[bytes, set[bytes]]:
    file_basename = os.path.basename(src).removesuffix(b'.md')

    # extract title
    title_match = MARKDOWN_H1_PATTERN.search(content)
    if title_match:
        title = title_match.group('title')
        content = content[title_match.end():]
    else:
        title = file_basename

    old_link_prefix = repair_url_part(bytes(urllib.parse.quote_from_bytes(file_basename), 'utf-8'))
    # repair Resource links
    resource_match_offset = 0
    for re_match in MARKDOWN_RESOURCE_LINK_PATTERN.finditer(content):
        repaired_link = repair_link(re_match, old_link_prefix, resource_dir_names, md_link=False)
        content, resource_match_offset = replace_match(re_match, repaired_link, content, resource_match_offset)

    # repair Markdown links
    md_match_offset = 0
    for re_match in MARKDOWN_MD_LINK_PATTERN.finditer(content):
        repaired_link = repair_link(re_match, old_link_prefix, resource_dir_names)
        content, md_match_offset = replace_match(re_match, repaired_link, content, md_match_offset)

    # tags & crit
    tags: set[bytes] = set()
    crit_match_offset = 0
    for re_match in MARKDOWN_CRIT_PATTERN.finditer(content):
        crit = re_match.group('crit').strip().replace(b'"', b'').replace(b'\n', b'')
        tags.add(crit)
        md_crit = b'{{< crit "' + crit + b'" >}}'
        content, crit_match_offset = replace_match(re_match, md_crit, content, crit_match_offset)

    logger.debug(f'Found {len(tags)} tags')
    slug = urllib.parse.unquote_to_bytes(repair_url_part(old_link_prefix).removesuffix(b'.md'))

    return b'''---
title: ''' + title + b'''
slug: ''' + slug + b'''
weight: ''' + DEFAULT_WEIGHT + b'''
tags: [ ''' + b', '.join(b'"' + tag + b'"' for tag in tags) + b''' ]
---

''' + content, tags


def copy_file(src: bytes, dst: bytes, resource_dir_names: list[bytes] | None = None, force: bool = False) -> set[bytes]:
    if os.path.exists(dst) and not force:
        raise RuntimeError(f'File "{dst}" already exists. Use --force to overwrite')

    if resource_dir_names is None:
        resource_dir_names = []

    with open(src, 'rb') as infile, open(dst, 'wb') as outfile:
        content = infile.read()
        repaired_content, tags = repair_content(content, src, dst, resource_dir_names)
        outfile.write(repaired_content)

    return tags


def link_order_from_index_file(index_file_path: bytes) -> list[bytes]:
    with open(index_file_path, 'rb') as index_file:
        content = index_file.read()
    link_order = list()
    for re_match in MARKDOWN_DIR_LINK_PATTERN.finditer(content):
        link_target = re_match.group('url')
        if link_target in link_order:
            continue
        logger.debug(f"Found direct link in {index_file_path}: {link_target}")
        link_order.append(link_target)
    return link_order


def set_page_weight(target_file_path, link_weight):
    if not os.path.isfile(target_file_path):
        if not target_file_path.endswith(b'.png'):
            logger.error(f'File (target of link) "{target_file_path}" does not exist. "'
                         f'"Its weight should have been: {link_weight}')
        return
    with open(target_file_path, 'rb') as target_file:
        content = target_file.read()
    weight_re_match = re.search(b'^weight: ' + DEFAULT_WEIGHT + b'$', content, re.MULTILINE)
    if weight_re_match is None:
        logger.warning(f'No weight tag found for "{target_file_path}"')
        return
    content, __ = replace_match(weight_re_match, b'weight: ' + bytes(str(link_weight), 'utf-8'), content)
    with open(target_file_path, 'wb') as target_file:
        target_file.write(content)
    logger.info(f"Updated weight to {link_weight} for {target_file_path}")


def write_tags_section(markdown_dir: bytes, tags: dict[bytes, bytes]):
    index_file_path = os.path.join(markdown_dir, b'_index.md')
    additional_content = """

## Critères

""".encode("utf-8")
    for tag_index, (tag_name, tag_value) in enumerate(tags.items(), 1):
        additional_content += bytes(f"{tag_index}. [", "utf-8") + tag_name + b"](/" + tag_value + b"#" + \
            bytes(urllib.parse.quote_plus(tag_name.decode("utf-8")), "utf-8") + b")\n"
    with open(index_file_path, "ab") as index_file:
        index_file.write(additional_content)


def beautify(
        base_dir: bytes,
        input_dir: bytes,
        markdown_dir: bytes,
        resources_dir: bytes,
        force: bool = False,
        depth: int = 0) -> None:
    global g_all_dm_tags, g_dm_tags

    def verb(*a):
        logger.debug(" " * depth + ' '.join(a))

    markdown_dir_basename = os.path.basename(markdown_dir)
    is_dm_dir = depth == 2 and markdown_dir_basename.startswith(b'dm-')
    if is_dm_dir:
        g_dm_tags = dict()

    if not os.path.isdir(markdown_dir):
        os.mkdir(markdown_dir)
    elif not force:
        raise RuntimeError(f'Directory "{markdown_dir}" already exists. Use --force to overwrite')

    if not os.path.isdir(resources_dir):
        os.mkdir(resources_dir)
    elif not force:
        raise RuntimeError(f'Directory "{resources_dir}" already exists. Use --force to overwrite')

    verb(f"input: {input_dir}, output: {markdown_dir}")

    names = os.listdir(input_dir)
    resource_dir_names: list[bytes] = list()
    for name in names:
        if os.path.isdir(name):
            resource_dir_names.append(name)
        elif name.endswith(b'.md'):
            resource_dir_names.append(name.removesuffix(b'.md'))
    resource_dir_names = list(map(
        lambda resource_dir_name: repair_url_part(repair_name(resource_dir_name)),
        resource_dir_names
    ))
    # process directories first, then files
    for name in sorted(names, key=lambda name_: 0 if os.path.isdir(name_) else 1):
        # first directory should not create a subdirectory
        if depth != 0:
            # markdown directory
            repaired_name = repair_name(name).removesuffix(b'.md')
            repaired_name = repair_url_part(repaired_name)
            markdown_repaired_dir = os.path.join(markdown_dir, repaired_name)
            # resource directory
            resources_repaired_dir = os.path.join(resources_dir, repaired_name)

            verb(f"looking at {name} (repaired: {repaired_name})")
        else:
            markdown_repaired_dir = markdown_dir
            resources_repaired_dir = resources_dir
        path = os.path.join(input_dir, name)

        if os.path.isdir(path):
            verb(f"{os.path.basename(path)}: directory")
            beautify(base_dir, path, markdown_repaired_dir, resources_repaired_dir, force, depth + 1)
        elif os.path.isfile(path):
            if name.endswith(b'.md'):
                if not os.path.isdir(markdown_repaired_dir):
                    os.mkdir(markdown_repaired_dir)
                verb(f"{os.path.basename(path)}: root markdown file")
                if dst_file_tags := copy_file(path, os.path.join(markdown_repaired_dir, b'_index.md'), resource_dir_names, force):
                    # register all tags with the right markdown directory
                    tag_value = os.path.relpath(markdown_repaired_dir, base_dir)
                    for tag in dst_file_tags:
                        g_dm_tags[tag] = tag_value
            else:
                # verb(f"{os.path.basename(path)}: resource file")
                shutil.copy(path, resources_repaired_dir)
        else:
            print(f"{path}: unknown type")

    # set weights of pages depending on the order of appearance of the links in the _index.md file
    if depth != 0:
        # figure out the order of links in the current file
        link_order = link_order_from_index_file(os.path.join(markdown_dir, b'_index.md'))
        # iterate once again over the all subdirectories and write weight values
        for link_weight, link_target in enumerate(link_order, 1):
            logger.info(f"Setting weight of {link_target} to {link_weight}")
            set_page_weight(os.path.join(markdown_dir, link_target, b'_index.md'), link_weight)

    # collect tags
    if depth == 2 and markdown_dir_basename.startswith(b'dm-'):
        g_all_dm_tags[markdown_dir_basename] = g_dm_tags.copy()

    # write tags
    if depth == 0:
        for dm_dir_name, tag_dict in g_all_dm_tags.items():
            logger.debug(f"Found {len(g_dm_tags)} tags for {dm_dir_name}")
            write_tags_section(os.path.join(markdown_dir, dm_dir_name), tag_dict)


def main():
    parser = argparse.ArgumentParser(description="unzip notion exports")
    parser.add_argument("-s", "--source", action="store_true", help="Input is the unzipped directory")
    parser.add_argument("-f", "--force", action="store_true", help="Overwrite existing files in the hugo directory")
    parser.add_argument("-o", "--overwrite", type=str, help="Path to an existing hugo directory containing the "
                                                            "content and static folders (and possibly a hugo.toml file) to "
                                                            "overwrite generated files.")
    parser.add_argument("-v", "--verbose", action="store_true", help="Increase verbosity")
    parser.add_argument("--keep-tmp-folder", action="store_true", help="Don't remove the temp folder at the end")
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
            raise NotADirectoryError(f'"{args.overwrite}" (--overwrite)')
        content_overwrite = os.path.join(args.overwrite, 'content')
        if not os.path.isdir(content_overwrite):
            logger.warning(f'Did not find the "content" directory in "{args.overwrite}". Creating it.')
        static_overwrite = os.path.join(args.overwrite, 'content')
        if not os.path.isdir(static_overwrite):
            logger.warning(f'Did not find the "static" directory in "{args.overwrite}". Creating it.')
        toml_overwrite = os.path.join(args.overwrite, 'hugo.toml')
        if not os.path.isfile(toml_overwrite):
            logger.info(f'Did not find the "hugo.toml" file in "{args.overwrite}".')
            toml_overwrite = None
    if args.source and args.keep_tmp_folder:
        raise ValueError(f"When using '--source', you don't use a tmp folder for extracting a zip file.")

    input_dir: bytes
    tmp_folder: tempfile.TemporaryDirectory[str] | None = None
    if args.source:
        if not os.path.isdir(args.input):
            raise NotADirectoryError("Input is not a directory")
        input_dir = bytes(args.input, 'utf-8')
    else:
        if not os.path.isfile(args.input):
            raise FileNotFoundError("Input is not a file")
        tmp_folder = tempfile.TemporaryDirectory(prefix='notion-unzip-')
        with zipfile.ZipFile(args.input, 'r') as zip_ref:
            zip_ref.extractall(tmp_folder.name)
        input_dir = bytes(tmp_folder.name, 'utf-8')

    # Generate content and static directories
    output_dir = bytes(args.hugo_dir, 'utf-8')
    content_output_dir = os.path.join(output_dir, b'content')
    static_output_dir = os.path.join(output_dir, b'static')
    beautify(content_output_dir, input_dir, content_output_dir, static_output_dir, args.force)

    # Clean up file generation
    if tmp_folder:
        if args.keep_tmp_folder:
            print(f"Not removing the tmp folder: {tmp_folder}")
            shutil.copytree(tmp_folder.name, os.path.join("/tmp", "notion-unzip"))
        else:
            tmp_folder.cleanup()

    # Overwrite
    # This code is written in a way that makes future implementations of the --overwrite option easier
    if content_overwrite:
        logger.info('Overwriting content directory with pre-existing files')
        shutil.copytree(content_overwrite, content_output_dir.decode('utf-8'), dirs_exist_ok=True)
    if static_overwrite:
        logger.info('Overwriting static directory with pre-existing files')
        shutil.copytree(static_overwrite, static_output_dir.decode('utf-8'), dirs_exist_ok=True)
    if toml_overwrite:
        logger.info('Overwriting toml file')
        shutil.copy(toml_overwrite, args.hugo_dir)


if __name__ == "__main__":
    main()
