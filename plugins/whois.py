"""
whois.py
Provides a command to allow users to look up information on domain names.
"""

from contextlib import suppress
from datetime import datetime

import asyncwhois

from cloudbot import hook


@hook.command
def whois(text, reply):
    """<domain> - Does a whois query on <domain>."""
    domain = text.strip().lower()

    try:
        _, data = asyncwhois.whois(domain)
    except Exception as e:
        reply(f"Invalid input or domain not found: {e}")
        raise

    info = []

    # We suppress errors here because different domains provide different data fields
    with suppress(KeyError, TypeError, IndexError):
        registrar = data.get("registrar")
        if isinstance(registrar, list):
            registrar = registrar[0]
        if registrar:
            info.append(("Registrar", registrar))

    with suppress(KeyError, TypeError, IndexError):
        creation_date = data.get("created")
        if isinstance(creation_date, list):
            creation_date = creation_date[0]
        if isinstance(creation_date, datetime):
            info.append(("Registered", creation_date.strftime("%d-%m-%Y")))

    with suppress(KeyError, TypeError, IndexError):
        expiration_date = data.get("expires")
        if isinstance(expiration_date, list):
            expiration_date = expiration_date[0]
        if isinstance(expiration_date, datetime):
            info.append(("Expires", expiration_date.strftime("%d-%m-%Y")))

    if not info:
        return "No information returned."

    info_text = ", ".join(f"\x02{name}\x02: {i}" for name, i in info)
    return f"{domain} - {info_text}"
