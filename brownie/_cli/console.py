#!/usr/bin/python3

import code
import importlib
import sys

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.history import FileHistory
from prompt_toolkit.lexers import PygmentsLexer
from prompt_toolkit.styles.pygments import style_from_pygments_cls
from pygments import highlight
from pygments.formatters import get_formatter_by_name
from pygments.lexers import PythonLexer
from pygments.styles import get_style_by_name

import brownie
from brownie import network, project
from brownie._config import CONFIG, _get_data_folder, _update_argv_from_docopt
from brownie.utils import color
from brownie.utils.docopt import docopt

__doc__ = f"""Usage: brownie console [options]

Options:
  --network <name>        Use a specific network (default {CONFIG.settings['networks']['default']})
  --tb -t                 Show entire python traceback on exceptions
  --help -h               Display this message

Connects to the network and opens the brownie console.
"""


def main():
    args = docopt(__doc__)
    _update_argv_from_docopt(args)

    if project.check_for_project():
        active_project = project.load()
        active_project.load_config()
        print(f"{active_project._name} is the active project.")
    else:
        active_project = None
        print("No project was loaded.")

    network.connect(CONFIG.argv["network"])

    shell = Console(active_project)
    shell.interact(banner="Brownie environment is ready.", exitmsg="")


class Console(code.InteractiveConsole):
    def __init__(self, project=None):
        locals_dict = dict((i, getattr(brownie, i)) for i in brownie.__all__)
        locals_dict["dir"] = self._dir

        if project:
            project._update_and_register(locals_dict)

        # only make GUI available if Tkinter is installed
        try:
            Gui = importlib.import_module("brownie._gui").Gui
            locals_dict["Gui"] = Gui
        except ModuleNotFoundError:
            pass

        self.lexer = PythonLexer()
        fmt_name = "terminal"
        try:
            import curses

            curses.setupterm()
            if curses.tigetnum("colors") == 256:
                fmt_name = "terminal256"
        except Exception:
            # if curses won't import we are probably using Windows
            pass
        self.formatter = get_formatter_by_name(fmt_name, style=CONFIG.settings["color_style"])

        history_file = str(_get_data_folder().joinpath(".history").absolute())
        kwargs = {}
        if CONFIG.settings["show_colors"]:
            kwargs.update(
                lexer=PygmentsLexer(PythonLexer),
                style=style_from_pygments_cls(get_style_by_name(CONFIG.settings["color_style"])),
                include_default_pygments_style=False,
            )
        if CONFIG.settings["auto_suggest"]:
            kwargs["auto_suggest"] = AutoSuggestFromHistory()
        self.prompt_session = PromptSession(
            history=SanitizedFileHistory(history_file, locals_dict), **kwargs
        )
        super().__init__(locals_dict)

    # console dir method, for simplified and colorful output
    def _dir(self, obj=None):
        if obj is None:
            results = [(k, v) for k, v in self.locals.items() if not k.startswith("_")]
        elif hasattr(obj, "__console_dir__"):
            results = [(i, getattr(obj, i)) for i in obj.__console_dir__]
        else:
            results = [(i, getattr(obj, i)) for i in dir(obj) if not i.startswith("_")]
        results = sorted(results, key=lambda k: k[0])
        self.write(f"[{f'{color}, '.join(_dir_color(i[1]) + i[0] for i in results)}{color}]\n")

    def _console_write(self, obj):
        text = repr(obj)
        try:
            if obj and isinstance(obj, dict):
                text = color.pretty_dict(obj)
            elif obj and isinstance(obj, (tuple, list, set)):
                text = color.pretty_sequence(obj)
        except (SyntaxError, NameError):
            pass
        if CONFIG.settings["show_colors"]:
            text = highlight(text, self.lexer, self.formatter)
        self.write(text)

    def raw_input(self, prompt=""):
        return self.prompt_session.prompt(prompt)

    def showsyntaxerror(self, filename):
        tb = color.format_tb(sys.exc_info()[1])
        self.write(tb + "\n")

    def showtraceback(self):
        tb = color.format_tb(sys.exc_info()[1], start=1)
        self.write(tb + "\n")

    def runsource(self, source, filename="<input>", symbol="single"):
        try:
            code = self.compile(source, filename, "single")
        except (OverflowError, SyntaxError, ValueError):
            self.showsyntaxerror(filename)
            return False

        if code is None:
            return True

        try:
            self.compile(source, filename, "eval")
            code = self.compile(f"__ret_value__ = {source}", filename, "exec")
        except Exception:
            pass
        self.runcode(code)
        if "__ret_value__" in self.locals and self.locals["__ret_value__"] is not None:
            self._console_write(self.locals["__ret_value__"])
            del self.locals["__ret_value__"]
        return False


def _dir_color(obj):
    if type(obj).__name__ == "module":
        return color("brownie blue")
    if hasattr(obj, "_dir_color"):
        return color(obj._dir_color)
    if not callable(obj):
        return color("bright blue")
    return color("bright cyan")


class SanitizedFileHistory(FileHistory):
    """
    FileHistory subclass to strip sensetive information prior to writing to disk.

    Any callable containing a `_private` attribute will have it's input arguments
    removed prior to inclusion in the history file. For example, when the user
    input is:

        Accounts.add("0x1234...")

    The line saved to the history file is:

        Accounts.add()

    The original value is still available within the in-memory history while the
    session is active.
    """

    def __init__(self, filename, local_dict):
        self.locals = local_dict
        super().__init__(filename)

    def store_string(self, line):
        try:
            cls_, method = line[: line.index("(")].split(".")
            method = getattr(self.locals[cls_], method)
            if hasattr(method, "_private"):
                line = line[: line.index("(")] + "()"
        except (ValueError, AttributeError, KeyError):
            pass
        return super().store_string(line)
