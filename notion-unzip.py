#!/usr//bin/env python3
import argparse
import re
import os
import tempfile
import zipfile

FILE_HASH_SUFFIX_PATTERN = re.compile(b'(.*)( [0-9a-z]{32})(\\.md)?$')
MARKDOWN_HASH_SUFFIX_PATTERN = re.compile(b'%20[0-9a-z]{32}')


def fix_name(name: bytes) -> bytes:
    return re.sub(FILE_HASH_SUFFIX_PATTERN, b'\\1\\3', name.replace(b'e\xa6\xfc', b'\xc3\xa9'))


def fix_content(content: bytes) -> bytes:
    return re.sub(MARKDOWN_HASH_SUFFIX_PATTERN, b'', content)


def copy_file(src: bytes, dst: bytes, force: bool) -> None:
    if os.path.exists(dst) and not force:
        raise RuntimeError(f'File "{dst}" already exists. Use --force to overwrite')

    with open(src, 'rb') as infile:
        content = infile.read()
        with open(dst, 'wb') as outfile:
            fixed_content = fix_content(content)
            outfile.write(fixed_content)


def beautify(input_dir: bytes, output_dir: bytes, force: bool = False, depth: int = 0) -> None:
    def verb(*a, **k):
        print(" " * depth, *a, **k)

    if not os.path.isdir(output_dir):
        os.mkdir(output_dir)
    elif not force:
        raise RuntimeError(f'Directory "{output_dir}" already exists. Use --force to overwrite')

    verb(f"input: {input_dir}, output: {output_dir}")
    for name in os.listdir(input_dir):
        fixed_name = fix_name(name)
        output_name = os.path.join(output_dir, fixed_name)
        verb(f"looking at {name} (fixed: {fixed_name})")
        path = os.path.join(input_dir, name)
        if os.path.isdir(path):
            verb(f"{os.path.basename(path)}: directory")
            beautify(path, output_name, force, depth + 1)
        elif os.path.isfile(path):
            verb(f"{os.path.basename(path)}: file")
            copy_file(path, output_name, force)
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

    beautify(input_dir, bytes(args.output, 'utf-8'), args.force)
    if tmp_folder:
        tmp_folder.cleanup()


if __name__ == "__main__":
    main()
