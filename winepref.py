#!/usr/bin/env python3
from pathlib import Path
from dataclasses import dataclass
from argparse import ArgumentParser
from typing import Dict
from sys import exit
from configparser import ConfigParser, NoSectionError, NoOptionError
from os import environ, makedirs, getuid, execl, chdir
from os.path import expanduser
from pwd import getpwuid
import re
import subprocess
import shutil


def main():
    parser = ArgumentParser()

    def print_help(args, cfg, prefixen):
        parser.print_help()
        exit(1)

    parser.set_defaults(func=print_help)
    sub_parsers = parser.add_subparsers()

    def add_subcommand(fun, **kwargs):
        ret = sub_parsers.add_parser(fun.__name__, **kwargs)
        ret.set_defaults(func=fun)
        return ret

    add_subcommand(ls, help="List all prefix directories")

    browse_parser = add_subcommand(
        browse,
        help="Open shell in prefix directory with WINEPREFIX set to prefix dir"
    )
    browse_parser.add_argument("PREFIX")

    new_parser = add_subcommand(new, help="Create new wineprefix")
    new_parser.add_argument("PREFIX")

    run_parser = add_subcommand(
        run, help="Run executable in wine with WINEPREFIX set to PREFIX")
    run_parser.add_argument("PREFIX")
    run_parser.add_argument("EXE")

    shortcut_parser = add_subcommand(
        shortcut, help="Create desktop file that launches exe in prefix")
    shortcut_parser.add_argument("EXE", help=".exe to shortcut")
    shortcut_parser.add_argument(
        "PREFIX",
        help="WINEPREFIX set to what prefix",
        nargs="?",
    )
    shortcut_parser.add_argument(
        "-n",
        "--name",
        help="Set a different name instead of $x.exe in desktop file",
    )

    args = parser.parse_args()

    cfg = load_config()
    prefixen = load_prefixen(cfg)

    args.func(args, cfg, prefixen)


@dataclass
class Config:
    prefix_dir: Path
    shell: str


DEFAULT_CONFIG = """
[options]
// Directory for storing wineprefixes
prefix_dir = ~/.wineprefix
// The shell to use when using winepref browse
// If set to default then uses default shell of user from /etc/passwd
shell = default
""".lstrip()


def load_config() -> Config:
    config_parser = ConfigParser()
    config_path = Path(environ.get(
        "XDG_CONFIG_DIR", expanduser("~/.config"))) / "winepref" / "config.cfg"
    if not config_path.exists():
        makedirs(config_path.parent, exist_ok=True)
        with open(config_path, "w") as fh:
            fh.write(DEFAULT_CONFIG)
        print(f"Wrote default config to {config_path}")

    config_parser.read(config_path)
    try:
        prefix_dir = Path(config_parser.get("options",
                                            "prefix_dir")).expanduser()
        if not prefix_dir.is_absolute():
            exit(f"prefix_dir in config is not absolute")

        shell = config_parser.get("options", "shell")
        shell = getpwuid(getuid()).pw_shell if shell == "default" else shell

        return Config(
            prefix_dir=prefix_dir,
            shell=shell,
        )
    except (NoSectionError, NoOptionError) as e:
        exit(f"Error while reading config in {config_path}: {e}")


def load_prefixen(cfg: Config) -> Dict[str, Path]:
    try:
        return dict((prefix.name, prefix)
                    for prefix in cfg.prefix_dir.iterdir()
                    if prefix.is_dir() and PREFIX_RE.match(prefix.name))
    except FileNotFoundError:
        return {}


def current_env_with_wineprefix(prefix_path: Path) -> Dict[str, str]:
    ret = environ.copy()
    ret["WINEPREFIX"] = str(prefix_path)
    return ret


def get_prefix_dir(prefixen: Dict[str, Path], prefix: str) -> Path:
    try:
        return prefixen[prefix]
    except KeyError:
        all_prefixen = ", ".join(prefixen.keys())
        exit(
            f"Prefix '{prefix}' doesn't exist. Existing prefixen: {all_prefixen}"
        )


def ls(args, cfg: Config, prefixen: Dict[str, Path]):
    for prefix in prefixen.keys():
        print(prefix)


def browse(args, cfg: Config, prefixen: Dict[str, Path]):
    prefix_path = get_prefix_dir(prefixen, args.PREFIX)
    environ["WINEPREFIX"] = str(prefix_path)
    chdir(prefix_path)
    try:
        execl(cfg.shell, cfg.shell)
    except FileNotFoundError:
        exit(f"Can't find shell '{cfg.shell}' specified in config")


PREFIX_RE = re.compile(r"^[^\n \t/'\"]+$")


def new(args, cfg: Config, prefixen: Dict[str, Path]):
    prefix = args.PREFIX
    if prefix in prefixen:
        exit(f"Prefix '{prefix}' already exists")
    if not PREFIX_RE.match(prefix):
        exit(
            f"Invalid prefix name '{prefix}'. Name must match {PREFIX_RE.pattern}"
        )
    prefix_path = cfg.prefix_dir / prefix
    try:
        makedirs(prefix_path)
        subprocess.run(["winecfg"],
                       env=current_env_with_wineprefix(prefix_path),
                       check=True)
    except (subprocess.CalledProcessError, KeyboardInterrupt):
        shutil.rmtree(prefix_path)
        exit(1)


def run(args, cfg: Config, prefixen: Dict[str, Path]):
    (exe, prefix) = (args.EXE, args.PREFIX)
    prefix_path = get_prefix_dir(prefixen, prefix)
    subprocess.run(["wine", exe], env=current_env_with_wineprefix(prefix_path))


def shortcut(args, cfg: Config, prefixen: Dict[str, Path]):
    prefix = environ.get("WINEPREFIX", None)
    if args.PREFIX != None:
        prefix = args.PREFIX

    if prefix == None:
        exit("Don't know what wineprefix to use")

    create_desktop_file(
        prefix,
        args.EXE,
        args.name if args.name != None else Path(args.EXE).name,
    )


def escape_with_table(table: Dict[str, str], s: str) -> str:
    return ''.join(table.get(c, c) for c in s)


BASE_DESKTOP_QUOTE_TABLE = {
    "\"": "\\\"",
    "`": "\\`",
    "$": "\\$",
    "\\": "\\\\",
}

EXE_DESKTOP_QUOTE_TABLE = BASE_DESKTOP_QUOTE_TABLE.copy()
EXE_DESKTOP_QUOTE_TABLE["%"] = "%%"


def escape_exe(s: str) -> str:
    return escape_with_table(EXE_DESKTOP_QUOTE_TABLE, s)


def escape_base(s: str) -> str:
    return escape_with_table(BASE_DESKTOP_QUOTE_TABLE, s)


def create_desktop_file(prefix: str, exe: Path, name: str):
    cont = """
[Desktop Entry]
Type=Application
Name={name}
Exec=env "WINEPREFIX={prefix}" wine "{exe}"
Icon=wine
""".format(
        name=escape_base(name),
        exe=escape_exe(exe),
        prefix=escape_exe(prefix),
    ).lstrip()
    app_dir = Path("~/.local/share/applications").expanduser()
    makedirs(app_dir, exist_ok=True)
    with open(app_dir / f"{name}.desktop", "w") as fh:
        fh.write(cont)


if __name__ == "__main__":
    main()
