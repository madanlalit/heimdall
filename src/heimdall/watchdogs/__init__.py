"""
Heimdall Watchdogs Module.

Provides browser state monitoring with automatic event emission.
"""

from heimdall.watchdogs.base import BaseWatchdog
from heimdall.watchdogs.dom import DOMWatchdog
from heimdall.watchdogs.error import ErrorWatchdog
from heimdall.watchdogs.navigation import NavigationWatchdog
from heimdall.watchdogs.network import NetworkWatchdog

__all__ = [
    "BaseWatchdog",
    "NavigationWatchdog",
    "NetworkWatchdog",
    "DOMWatchdog",
    "ErrorWatchdog",
]
