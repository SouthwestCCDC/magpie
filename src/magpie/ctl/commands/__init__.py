"""CTL commands for magpie server administration."""

from magpie.ctl.commands.flush_tag import flush_tag
from magpie.ctl.commands.gc import gc
from magpie.ctl.commands.init import init
from magpie.ctl.commands.sync import sync
from magpie.ctl.commands.token import token

__all__ = ["flush_tag", "gc", "init", "sync", "token"]
