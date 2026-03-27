import json
import re
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Deque, Literal

from cloudbot.util import formatting, web

RoleType = Literal["user", "assistant"]

APP_HTML_PROMPT_SUFFIX = (
    "\nMake sure to put everything in a single html file so it can be a single code block"
    " meant to be directly used in a browser as it is. Do not explain, just show the code."
)

_HISTORY_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>__TITLE__</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark.min.css">
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0d1117;color:#c9d1d9;line-height:1.65;padding:1.5rem 1rem}
.wrap{max-width:860px;margin:0 auto}
.hdr{display:flex;justify-content:space-between;align-items:center;padding-bottom:1rem;border-bottom:1px solid #21262d;margin-bottom:1.5rem}
.hdr h1{font-size:1rem;font-weight:500;color:#8b949e}
.msg{display:flex;gap:.75rem;margin-bottom:1.2rem;align-items:flex-start}
.msg.user{flex-direction:row-reverse}
.av{width:30px;height:30px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:.7rem;font-weight:700;flex-shrink:0;text-transform:uppercase}
.user .av{background:#1f6feb;color:#fff}
.bot .av{background:#21262d;color:#58a6ff;border:1px solid #30363d}
.bub{position:relative;padding:.7rem .95rem;border-radius:12px;font-size:.875rem;word-break:break-word;max-width:82%}
.user .bub{background:#1c2d42;border:1px solid #1f6feb40;border-top-right-radius:3px}
.bot .bub{background:#161b22;border:1px solid #21262d;border-top-left-radius:3px}
.bub-content{overflow:hidden}
.bub.collapsed .bub-content{max-height:22em;-webkit-mask-image:linear-gradient(to bottom,black 55%,transparent 100%);mask-image:linear-gradient(to bottom,black 55%,transparent 100%)}
.bub-content p{margin:.3em 0}.bub-content p:first-child{margin-top:0}.bub-content p:last-child{margin-bottom:0}
.bub-content pre{background:#0d1117;border:1px solid #30363d;border-radius:8px;padding:.8rem 1rem;overflow-x:auto;margin:.5em 0;font-size:.82em;white-space:pre;word-break:normal}
.bub-content pre code{background:none!important;border:none!important;padding:0!important;font-size:inherit;white-space:pre}
.bub-content code{background:#0d1117;padding:.1em .35em;border-radius:4px;font-size:.85em;font-family:'SFMono-Regular',Consolas,monospace;border:1px solid #30363d}
.bub-content ul,.bub-content ol{padding-left:1.4em;margin:.3em 0}.bub-content li{margin:.1em 0}
.bub-content h1,.bub-content h2,.bub-content h3,.bub-content h4{margin:.5em 0 .25em;color:#e6edf3}
.bub-content h1{font-size:1.1em}.bub-content h2{font-size:1em}.bub-content h3,.bub-content h4{font-size:.95em}
.bub-content a{color:#58a6ff;text-decoration:none}.bub-content a:hover{text-decoration:underline}
.bub-content blockquote{border-left:3px solid #30363d;padding-left:.75em;color:#8b949e;margin:.4em 0}
.bub-content table{border-collapse:collapse;width:100%;margin:.4em 0;font-size:.9em}
.bub-content th,.bub-content td{border:1px solid #30363d;padding:.3em .6em;text-align:left}
.bub-content th{background:#21262d;color:#e6edf3}
.bub-content hr{border:none;border-top:1px solid #21262d;margin:.6em 0}
.toggle{display:block;width:100%;margin-top:.5rem;background:none;border:none;border-top:1px solid #30363d;color:#58a6ff;font-size:.75rem;cursor:pointer;padding:.35rem 0 0;text-align:center;transition:color .1s}
.toggle:hover{color:#79c0ff}
.cbtn{position:absolute;top:.35rem;right:.35rem;background:#0d1117cc;border:1px solid #30363d;border-radius:4px;padding:.1rem .45rem;color:#8b949e;font-size:.72rem;cursor:pointer;line-height:1.5;transition:color .1s,border-color .1s,opacity .12s;white-space:nowrap;opacity:0}
.msg:hover .cbtn{opacity:1}
.cbtn:hover{color:#e6edf3;border-color:#8b949e}
.cbtn.ok{color:#3fb950!important;border-color:#3fb950!important}
.hcbtn{background:none;border:1px solid #30363d;border-radius:4px;padding:.1rem .45rem;color:#8b949e;font-size:.72rem;cursor:pointer;line-height:1.5;transition:color .1s,border-color .1s;white-space:nowrap}
.hcbtn:hover{color:#e6edf3;border-color:#8b949e}
.hcbtn.ok{color:#3fb950!important;border-color:#3fb950!important}
</style>
</head>
<body>
<div class="wrap">
<div class="hdr">
  <h1 id="hdr-title"></h1>
  <button class="hcbtn" id="copy-all">Copy all</button>
</div>
<div id="chat"></div>
</div>
<script src="https://cdn.jsdelivr.net/npm/marked@12/marked.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
<script>
const TITLE=__TITLE_JSON__;
const MSGS=__MESSAGES_JSON__;
marked.use({breaks:true,gfm:true,html:false});
document.getElementById('hdr-title').textContent=TITLE;

function flash(btn,label){
  btn.textContent='\u2713 Copied';btn.classList.add('ok');
  setTimeout(()=>{btn.textContent=label;btn.classList.remove('ok');},1500);
}
function copy(text,btn,label){
  navigator.clipboard.writeText(text).then(()=>flash(btn,label));
}

MSGS.forEach((m,i)=>{
  const isLast=i===MSGS.length-1;
  const lines=m.content.split('\\n').length;
  const shouldCollapse=lines>20&&!isLast;
  const wrap=document.createElement('div');
  wrap.className='msg '+m.role;
  const av=document.createElement('div');
  av.className='av';
  av.textContent=m.role==='user'?m.label.slice(0,2):'AI';
  const bub=document.createElement('div');
  bub.className='bub'+(shouldCollapse?' collapsed':'');
  const content=document.createElement('div');
  content.className='bub-content';
  content.innerHTML=marked.parse(m.content);
  const cb=document.createElement('button');
  cb.className='cbtn';cb.textContent='Copy';
  cb.onclick=()=>copy(m.content,cb,'Copy');
  bub.append(content,cb);
  if(lines>20){
    const tog=document.createElement('button');
    tog.className='toggle';
    tog.textContent=shouldCollapse?'Show more \u25be':'Collapse \u25b4';
    tog.onclick=()=>{
      const now=bub.classList.toggle('collapsed');
      tog.textContent=now?'Show more \u25be':'Collapse \u25b4';
    };
    bub.appendChild(tog);
  }
  wrap.append(av,bub);
  document.getElementById('chat').appendChild(wrap);
});

document.querySelectorAll('pre code').forEach(el=>hljs.highlightElement(el));

document.getElementById('copy-all').onclick=function(){
  const text=MSGS.map(m=>'['+m.role+']: '+m.content).join('\\n\\n');
  copy(text,this,'Copy all');
};
</script>
</body>
</html>
"""


@dataclass
class Message:
    role: RoleType
    content: str
    timestamp: float = field(default_factory=lambda: datetime.now().timestamp())

    def as_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


def detect_code_blocks(markdown_text: str) -> list[str]:
    """Extract fenced code blocks. Falls back to unclosed blocks, then full text."""
    closed = re.compile(r"```\S*(.*?)```", re.DOTALL).findall(markdown_text)
    if closed:
        return closed
    unclosed = re.compile(r"```(.*)", re.DOTALL).findall(markdown_text)
    return unclosed if unclosed else [markdown_text]


def get_or_create_history(
    cache: dict[tuple[str, str], Deque[Message]],
    chan: str,
    nick: str,
    maxlen: int,
) -> Deque[Message]:
    channick = (chan, nick)
    if channick not in cache:
        cache[channick] = deque(maxlen=maxlen)
    return cache[channick]


def clear_history(
    cache: dict[tuple[str, str], Deque[Message]],
    chan: str,
    nick: str,
) -> str:
    channick = (chan, nick)
    if channick in cache:
        cache.pop(channick)
        return "Conversation cache cleared."
    return "No conversation cache to clear."


def copy_history(
    cache: dict[tuple[str, str], Deque[Message]],
    chan: str,
    nick: str,
    target: str,
    maxlen: int,
) -> str:
    target_channick = (chan, target)
    if target_channick not in cache:
        return f"No conversation history found for {target}."
    cache[(chan, nick)] = deque(cache[target_channick], maxlen=maxlen)
    return f"Copied {target}'s conversation history into yours ({len(cache[(chan, nick)])} messages)."


def _js_safe_json(obj) -> str:
    # </script> inside a JSON string terminates the <script> block in HTML.
    # Replacing </ with <\/ is valid JS and invisible to the HTML parser.
    return json.dumps(obj).replace("</", "<\\/")


def upload_history(nick: str, messages: list[Message], header: str) -> str:
    """Render conversation as a formatted HTML page and upload. Returns URL."""
    msgs_data = [
        {"role": m.role, "content": m.content, "label": nick}
        for m in messages
    ]
    html = (
        _HISTORY_HTML
        .replace("__TITLE__", header)
        .replace("__TITLE_JSON__", _js_safe_json(header))
        .replace("__MESSAGES_JSON__", _js_safe_json(msgs_data))
    )
    return web.paste(html.encode("utf-8"), ext="html")


def truncate_or_paste(
    response: str,
    nick: str,
    messages: list[Message],
    header: str,
    prefix: str = "",
    max_len: int = 350,
) -> str:
    """Truncate for IRC. If truncated, upload full conversation and append URL."""
    truncated = formatting.truncate_str(response, max_len)
    result = f"{prefix}{truncated}" if prefix else truncated
    if len(truncated) < len(response):
        paste_url = upload_history(nick, messages, header)
        return f"{result} (full response: {paste_url})"
    return result


def upload_html_app(html_code: str, model_prefix: str = "") -> str:
    """Upload an HTML app and return IRC-ready paste + preview URL string."""
    url = web.paste(html_code.encode("utf-8").strip(), ext="html")
    paste_url = url.removesuffix(".html") + "/p"
    result = f"{paste_url} - Try online: {url}"
    return f"[{model_prefix}] {result}" if model_prefix else result
