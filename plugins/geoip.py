import re
import socket
from typing import Annotated

from cloudbot import hook
from cloudbot.hook import Arg
from cloudbot.util.web import get_session

URL = "https://json.geoiplookup.io/{}"


@hook.command()
def geoip(host: Annotated[str, Arg("Host name or IP address")]):
    """Looks up the physical location of the host or ip using Maxmind GeoLite"""
    if re.match(r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}", host):
        ip = host
    elif re.match(r"(([0-9a-fA-F]{0,4}:){1,7}[0-9a-fA-F]{0,4})", host):
        ip = host
    else:
        try:
            ip = socket.gethostbyname(host)
        except socket.gaierror:
            return "Invalid input."

    response = get_session().get(URL.format(ip))
    if not response.ok:
        return f"Error: {response.status_code}"

    data = response.json()

    return "{ip} - {hostname}: \x02Country:\x02 {country_name} ({country_code}), \x02Region:\x02 {region}, \x02District:\x02 {district}, \x02City:\x02 {city}, \x02ISP:\x02 {isp}".format(
        **data
    )
