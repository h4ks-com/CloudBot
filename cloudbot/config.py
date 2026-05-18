import json
import logging
import os
import sys
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("cloudbot")


@dataclass
class ProxyOptions:
    host: str
    port: int
    username: str | None
    password: str | None

    def __str__(self):
        if self.username is not None and self.password is not None:
            return f"http://{self.username}:{self.password}@{self.host}:{self.port}"
        else:
            return f"http://{self.host}:{self.port}"


class Config(OrderedDict):
    def __init__(self, bot, *, filename=None):
        super().__init__()
        logger.info("Initializing config with filename=%s", filename)
        if filename is None:
            filename = "config.json"
        self.filename = filename
        self.path = Path(self.filename).resolve()
        self.bot = bot

        self._api_keys = {}

        # populate self with config data
        self.load_config()

    def get_api_key(self, name, default=None):
        try:
            return self._api_keys[name]
        except LookupError:
            self._api_keys[name] = value = self.get("api_keys", {}).get(
                name, default
            )
            return value

    def get_proxy(self, plugin_name: str | None = None) -> ProxyOptions | None:
        if plugin_name is None:
            proxy = "default"
        else:
            proxy = self["plugins"].get(plugin_name, {}).get("http_proxy")
            if proxy is None:
                return None

        proxy_params = self.get("http_proxies")
        if proxy_params is None:
            raise ValueError("No http_proxies section found in config.json")

        proxy_options = proxy_params.get(proxy)
        if proxy_options is None:
            return None

        return ProxyOptions(**proxy_options)

    def load_config(self):
        """(re)loads the bot config from the config file"""
        self._api_keys.clear()
        if not self.path.exists():
            # if there is no config, show an error and die
            logger.critical(
                "No config file found, bot shutting down! Looked for '%s',"
                " CLOUDBOT_RUN_PATH=%s",
                self.path,
                os.environ["CLOUDBOT_RUN_PATH"],
            )
            print("No config file found! Bot shutting down in five seconds.")
            print("Copy 'config.default.json' to 'config.json' for defaults.")
            print(
                "For help, see htps://github.com/TotallyNotRobots/CloudBot. "
                "Thank you for using CloudBot!"
            )
            time.sleep(5)
            sys.exit()

        with self.path.open(encoding="utf-8") as f:
            data = json.load(f, object_pairs_hook=OrderedDict)

        self.update(data)
        logger.debug("Config loaded from file.")

    def save_config(self):
        """saves the contents of the config dict to the config file"""
        with self.path.open("w", encoding="utf-8") as f:
            json.dump(self, f, indent=4)

        logger.info("Config saved to file.")
