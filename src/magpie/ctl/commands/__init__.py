"""CTL commands for magpie server administration."""

from magpie.ctl.commands.gc import gc
from magpie.ctl.commands.init import init
from magpie.ctl.commands.token import token

__all__ = ["gc", "init", "token"]
