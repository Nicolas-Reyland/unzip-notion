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
MARKDOWN_LINK_PATTERN = re.compile(b'\\[(?P<name>[^]]*)]\\((?P<url>[^)]*)\\)')

logger = logging.getLogger('notion-unzip')
logger.setLevel(logging.DEBUG)


def fix_name(name: bytes) -> bytes:
    return re.sub(
        FILE_HASH_SUFFIX_PATTERN,
        b'\\1\\3',
        name.replace(b'e\xa6\xfc', b'\xc3\xa9')
            .replace(b'e\xcc\x81', b'\xc3\xa9')
            .replace(b'\xc3\xa9', b'e')
    )


def fix_url_part(url_part: bytes) -> bytes:
    url_part = re.sub(MARKDOWN_HASH_SUFFIX_PATTERN, b'', url_part)
    url_part = url_part.lower()
    for old, new in [(b'%20', b' '), (b'e%cc%81', b'e')]:
        url_part = url_part.replace(old, new)
    url_words = url_part.split(b' ')
    return b'-'.join(filter(lambda word: word != b'-', url_words))


def fix_link(link_match: re.Match[bytes], link_prefix: bytes) -> bytes:

    name = link_match.group('name')
    url = link_match.group('url')

    result = urllib.parse.urlparse(url)
    if all([result.scheme, result.netloc]):
        return link_match.group(0)

    url_parts = url.removeprefix(b'/').split(b'/')
    if url_parts:
        if url_parts[0] == link_prefix:
            url_parts.pop(0)
        if url_parts[-1].endswith(b'.md'):
            url_parts[-1] = url_parts[-1].removesuffix(b'.md')

    # Rebuild new url
    new_url = b'/'.join(map(fix_url_part, url_parts))
    logger.debug(f'Fixing link: {new_url} (old: {url})')
    return b'[' + name + b'](' + new_url + b')'


def fix_content(content: bytes, src: bytes, _: bytes) -> bytes:
    link_prefix = os.path.basename(src).removesuffix(b'.md')
    link_prefix = bytes(urllib.parse.quote_from_bytes(link_prefix), 'utf-8')
    match_offset = 0
    for match in MARKDOWN_LINK_PATTERN.finditer(content):
        fixed_link = fix_link(match, link_prefix)
        content = content[:match.start() + match_offset] + fixed_link + content[match.end() + match_offset:]
        match_offset += len(fixed_link) - match.end() + match.start()
    slug = urllib.parse.unquote_to_bytes(fix_url_part(link_prefix).removesuffix(b'.md'))

    return b'''---
slug: ''' + slug + b'''
---

''' + content


def copy_file(src: bytes, dst: bytes, force: bool) -> None:
    if os.path.exists(dst) and not force:
        raise RuntimeError(f'File "{dst}" already exists. Use --force to overwrite')

    with open(src, 'rb') as infile, open(dst, 'wb') as outfile:
        content = infile.read()
        fixed_content = fix_content(content, src, dst)
        outfile.write(fixed_content)


def beautify(input_dir: bytes, output_dir: bytes, force: bool = False, depth: int = 0) -> None:
    def verb(*a, **k):
        print(" " * depth, *a, **k)

    if not os.path.isdir(output_dir):
        os.mkdir(output_dir)
    elif not force:
        raise RuntimeError(f'Directory "{output_dir}" already exists. Use --force to overwrite')

    verb(f"input: {input_dir}, output: {output_dir}")
    for name in sorted(os.listdir(input_dir), key=lambda name_: 0 if os.path.isdir(name_) else 1):
        fixed_name = fix_name(name)
        output_name = os.path.join(output_dir, fixed_name)
        if not name.endswith(b'.png'):
            verb(f"looking at {name} (fixed: {fixed_name})")
        path = os.path.join(input_dir, name)

        if os.path.isdir(path):
            verb(f"{os.path.basename(path)}: directory")
            beautify(path, output_name, force, depth + 1)
        elif os.path.isfile(path):
            if name.endswith(b'.md'):
                dst_dir = output_name.removesuffix(b'.md')
                if not os.path.isdir(dst_dir):
                    os.mkdir(dst_dir)
                verb(f"{os.path.basename(path)}: root markdown file")
                copy_file(path, os.path.join(dst_dir, b'_index.md'), force)
                # copy_file(path, output_name, force)
            else:
                # verb(f"{os.path.basename(path)}: resource file")
                shutil.copy(path, output_name)
        else:
            print(f"{path}: unknown type")


def main():
    parser = argparse.ArgumentParser(description="unzip notion exports")
    parser.add_argument("-s", "--source", action="store_true", help="Input is the unzipped directory")
    parser.add_argument("-f", "--force", action="store_true", help="Overwrite existing files in the output directory")
    parser.add_argument("input")
    parser.add_argument("output")

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
    if os.path.isdir(args.output) and not args.force:
        raise OSError("Output directory exists. Use --force to overwrite")

    output_dir = bytes(args.output, 'utf-8')
    beautify(input_dir, output_dir, args.force)
    if tmp_folder:
        tmp_folder.cleanup()

    os.system("cd ../ars && rm -rf 'content/DM - 01' && mv content/ARS/* content/ && rmdir content/ARS")


if __name__ == "__main__":
    main()
