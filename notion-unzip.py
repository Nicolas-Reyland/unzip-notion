#!/usr//bin/env python3
import argparse
import re
import os
import shutil

HASH_SUFFIX_PATTERN = re.compile(b'(.*)( [0-9a-z]{32})(\\.md)?$')


def fix_name(name: bytes) -> bytes:
    return re.sub(HASH_SUFFIX_PATTERN, b'\\1\\3', name.replace(b'e\xa6\xfc', b'\xc3\xa9'))


def copy_file(src: bytes, dst: bytes) -> None:
    shutil.copy(src, dst)


def beautify(input_dir: bytes, output_dir: bytes, depth: int = 0) -> None:
    verb = lambda *a, **k: print(" " * depth, *a, **k)

    if not os.path.isdir(output_dir):
        os.mkdir(output_dir)

    verb(f"input: {input_dir}, output: {output_dir}")
    for name in os.listdir(input_dir):
        fixed_name = fix_name(name)
        output_name = os.path.join(output_dir, fixed_name)
        verb(f"looking at {name} (fixed: {fixed_name})")
        path = os.path.join(input_dir, name)
        if os.path.isdir(path):
            verb(f"{os.path.basename(path)}: directory")
            beautify(path, output_name, depth + 1)
        elif os.path.isfile(path):
            verb(f"{os.path.basename(path)}: file")
            copy_file(path, output_name)
        else:
            print(f"{path}: unknown type")


def main():
    parser = argparse.ArgumentParser(description="unzip notion exports")
    parser.add_argument("-s", "--source", action="store_true", help="Input is a directory")
    parser.add_argument("input")
    parser.add_argument("output")

    args = parser.parse_args()
    if not args.source:
        raise NotImplementedError("Option --source is not implemented")
    if not os.path.isdir(args.input):
        raise OSError("Input is not a directory")
    if os.path.isdir(args.output):
        ...
        # raise OSError("Output directory exists")

    beautify(bytes(args.input, 'utf-8'), bytes(args.output, 'utf-8'))


if __name__ == "__main__":
    main()
