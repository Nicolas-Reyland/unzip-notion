#!/usr//bin/env python3
import argparse
import logging
import re
import os
import shutil
import tempfile
import urllib.parse
import zipfile

FILE_HASH_SUFFIX_PATTERN = re.compile(b'(.*)( [0-9a-z]{32})(\\.md)?$')
MARKDOWN_HASH_SUFFIX_PATTERN = re.compile(b'%20[0-9a-z]{32}')
MARKDOWN_MD_LINK_PATTERN = re.compile(b'\\[(?P<name>[^]]*)]\\((?P<url>[^)]*.md)\\)')
MARKDOWN_IMG_LINK_PATTERN = re.compile(b'\\[(?P<name>[^]]*)]\\((?P<url>[^)]*.(?:png|jpg|jpeg|gif|bmp|tiff))\\)')
MARKDOWN_H1_PATTERN = re.compile(b'^# +(?P<title>.+)\r?\n')

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger()


def repair_name(name: bytes) -> bytes:
    return re.sub(
        FILE_HASH_SUFFIX_PATTERN,
        b'\\1\\3',
        name.replace(b'e\xa6\xfc', b'\xc3\xa9')
        .replace(b'e\xcc\x81', b'\xc3\xa9')
        .replace(b'\xc3\xa9', b'e')
    )


def repair_url_part(url_part: bytes) -> bytes:
    url_part = re.sub(MARKDOWN_HASH_SUFFIX_PATTERN, b'', url_part)
    url_part = url_part.lower()
    for old, new in [(b'%20', b' '), (b'e%cc%81', b'e')]:
        url_part = url_part.replace(old, new)
    url_words = url_part.split(b' ')
    return b'-'.join(filter(lambda word: word != b'-', url_words))


def repair_link(
        link_match: re.Match[bytes],
        invalid_link_prefixes: list[bytes] | None = None,
        url_prefix: list[bytes] | None = None,
        md_link: bool = True
) -> bytes:
    """
    Repair a Markdown or Image link.

    :param link_match: regex match object for a Markdown or Image link
    :param invalid_link_prefixes: prefixes that are not valid
    :param url_prefix: a prefix to add the resulting url
    :param md_link: the match matches a Markdown link (not an Image link)
    :return: the link with a repaired url part
    """
    if not invalid_link_prefixes:
        invalid_link_prefixes = []
    if not url_prefix:
        url_prefix = []

    name = link_match.group('name')
    url = link_match.group('url')

    result = urllib.parse.urlparse(url)
    if all([result.scheme, result.netloc]):
        return link_match.group(0)

    url_parts = url.removeprefix(b'/').split(b'/')

    # Rebuild new url
    new_url_parts = list(map(repair_url_part, url_prefix + url_parts))
    if new_url_parts:
        if new_url_parts[0] in invalid_link_prefixes:
            new_url_parts.pop(0)
        if md_link:
            new_url_parts[-1] = new_url_parts[-1].removesuffix(b'.md')
    new_url = b'/'.join(new_url_parts)
    logger.debug(f'Fixing link: {new_url} (old: {url})')
    return b'[' + name + b'](' + new_url + b')'


def repair_content(content: bytes, src: bytes, _: bytes, resource_dir_names: list[bytes] | None = None) -> bytes:
    file_basename = os.path.basename(src).removesuffix(b'.md')

    # extract title
    title_match = MARKDOWN_H1_PATTERN.search(content)
    if title_match:
        title = title_match.group('title')
        content = content[title_match.end():]
    else:
        title = file_basename

    link_prefix = bytes(urllib.parse.quote_from_bytes(file_basename), 'utf-8')
    # repair Markdown links
    md_match_offset = 0
    for match in MARKDOWN_MD_LINK_PATTERN.finditer(content):
        repaired_link = repair_link(match, resource_dir_names)
        content = content[:match.start() + md_match_offset] + repaired_link + content[match.end() + md_match_offset:]
        md_match_offset += len(repaired_link) - match.end() + match.start()

    # repair Image links
    img_match_offset = 0
    for match in MARKDOWN_IMG_LINK_PATTERN.finditer(content):
        repaired_link = repair_link(match, resource_dir_names, md_link=False)
        content = content[:match.start() + img_match_offset] + repaired_link + content[match.end() + img_match_offset:]
        img_match_offset += len(repaired_link) - match.end() + match.start()

    slug = urllib.parse.unquote_to_bytes(repair_url_part(link_prefix).removesuffix(b'.md'))

    return b'''---
title: ''' + title + b'''
slug: ''' + slug + b'''
---

''' + content


def copy_file(src: bytes, dst: bytes, resource_dir_names: list[bytes] | None = None, force: bool = False) -> None:
    if os.path.exists(dst) and not force:
        raise RuntimeError(f'File "{dst}" already exists. Use --force to overwrite')

    if resource_dir_names is None:
        resource_dir_names = []

    with open(src, 'rb') as infile, open(dst, 'wb') as outfile:
        content = infile.read()
        repaired_content = repair_content(content, src, dst, resource_dir_names)
        outfile.write(repaired_content)


def beautify(input_dir: bytes, markdown_dir: bytes, resources_dir: bytes, force: bool = False, depth: int = 0) -> None:
    def verb(*a):
        logger.debug(" " * depth + ' '.join(a))

    if not os.path.isdir(markdown_dir):
        os.mkdir(markdown_dir)
    elif not force:
        raise RuntimeError(f'Directory "{markdown_dir}" already exists. Use --force to overwrite')

    if not os.path.isdir(resources_dir):
        os.mkdir(resources_dir)
    elif not force:
        raise RuntimeError(f'Directory "{resources_dir}" already exists. Use --force to overwrite')

    verb(f"input: {input_dir}, output: {markdown_dir}")

    resource_dir_names: list[bytes] = []
    # process directories first, then files
    for name in sorted(os.listdir(input_dir), key=lambda name_: 0 if os.path.isdir(name_) else 1):
        # markdown directory
        repaired_name = repair_name(name).removesuffix(b'.md')
        repaired_name = repair_url_part(repaired_name)
        markdown_repaired_dir = os.path.join(markdown_dir, repaired_name)
        # resource directory
        resources_repaired_dir = os.path.join(resources_dir, repaired_name)
        resource_dir_names.append(repaired_name)

        verb(f"looking at {name} (repaired: {repaired_name})")
        path = os.path.join(input_dir, name)

        if os.path.isdir(path):
            verb(f"{os.path.basename(path)}: directory")
            beautify(path, markdown_repaired_dir, resources_repaired_dir, force, depth + 1)
        elif os.path.isfile(path):
            if name.endswith(b'.md'):
                if not os.path.isdir(markdown_repaired_dir):
                    os.mkdir(markdown_repaired_dir)
                verb(f"{os.path.basename(path)}: root markdown file")
                copy_file(path, os.path.join(markdown_repaired_dir, b'_index.md'), resource_dir_names, force)
            else:
                # verb(f"{os.path.basename(path)}: resource file")
                shutil.copy(path, resources_repaired_dir)
        else:
            print(f"{path}: unknown type")


def main():
    parser = argparse.ArgumentParser(description="unzip notion exports")
    parser.add_argument("-s", "--source", action="store_true", help="Input is the unzipped directory")
    parser.add_argument("-f", "--force", action="store_true", help="Overwrite existing files in the hugo directory")
    parser.add_argument("input")
    parser.add_argument("hugo_dir")

    args = parser.parse_args()

    input_dir: bytes
    tmp_folder: tempfile.TemporaryDirectory[str] | None = None
    if args.source:
        if not os.path.isdir(args.input):
            raise OSError("Input is not a directory")
        input_dir = bytes(args.input, 'utf-8')
    else:
        if not os.path.isfile(args.input):
            raise OSError("Input is not a file")
        tmp_folder = tempfile.TemporaryDirectory(prefix='notion-unzip-')
        with zipfile.ZipFile(args.input, 'r') as zip_ref:
            zip_ref.extractall(tmp_folder.name)
        input_dir = bytes(tmp_folder.name, 'utf-8')

    output_dir = bytes(args.hugo_dir, 'utf-8')
    beautify(input_dir, os.path.join(output_dir, b'content'), os.path.join(output_dir, b'static'), args.force)
    if tmp_folder:
        tmp_folder.cleanup()

    os.chdir(output_dir.decode('utf-8'))
    os.system(
        "rm -rf 'content/dm-01' content/_index.md static/dm-01 && mv content/ars/* content/ && mv "
        "static/ars/* static/ && rmdir content/ars && rmdir static/ars")


if __name__ == "__main__":
    main()
