"""CLI commands for magpie client."""

from magpie.cli.commands.amend import amend
from magpie.cli.commands.config_cmd import config_cmd
from magpie.cli.commands.flush_tag import flush_tag
from magpie.cli.commands.gc import gc
from magpie.cli.commands.get import get
from magpie.cli.commands.info import info
from magpie.cli.commands.ls import ls
from magpie.cli.commands.parse import ParseError, parse_artifact_path, parse_artifact_ref
from magpie.cli.commands.push import push
from magpie.cli.commands.status import status
from magpie.cli.commands.tag import tag
from magpie.cli.commands.untag import untag
from magpie.cli.commands.url import url

__all__ = [
    "amend",
    "config_cmd",
    "flush_tag",
    "gc",
    "get",
    "info",
    "ls",
    "parse_artifact_path",
    "parse_artifact_ref",
    "ParseError",
    "push",
    "status",
    "tag",
    "untag",
    "url",
]
