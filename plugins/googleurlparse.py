# Removed — Google URL redirect parser disabled per PR
# This plugin used to auto-expand Google redirect URLs in chat.
# import re
# from urllib.parse import unquote
# from cloudbot import hook
# 
# spamurl = re.compile(r".*(((www\.)?google\.com/url\?)[^ ]+)", re.I)
# 
# 
# @hook.regex(spamurl)
# def google_url(match):
#     ...
