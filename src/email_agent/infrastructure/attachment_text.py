"""Local-only, resource-limited extraction. Never uploads or executes files."""
import io
import json
import subprocess
import sys
from pathlib import Path

MAX_BYTES=512*1024
MAX_CHARACTERS=100000


def extract_local(data:bytes,mime:str)->str:
    if len(data)>MAX_BYTES:raise ValueError('Attachment text extraction limit is512KiB')
    mime=mime.split(';',1)[0].lower()
    if mime not in {'text/plain','text/html','text/csv','application/json','application/pdf'}:raise ValueError('Unsupported attachment type; binary content remains unreviewed')
    try:
        result=subprocess.run([sys.executable,'-m','email_agent.infrastructure.attachment_text',mime],input=data,capture_output=True,timeout=8,check=False,env={'PYTHONPATH':str(Path(__file__).resolve().parents[2]),'LANG':'C.UTF-8'})
    except subprocess.TimeoutExpired as exc:raise ValueError('Attachment extraction exceeded local time limit') from exc
    if result.returncode or len(result.stdout)>MAX_CHARACTERS*6+1000:raise ValueError('Attachment encrypted, malformed, unsupported or over extraction limits')
    value=json.loads(result.stdout)
    if not isinstance(value,str) or len(value)>MAX_CHARACTERS:raise ValueError('Invalid extracted attachment text')
    return value


def main():
    import resource
    resource.setrlimit(resource.RLIMIT_AS,(256*1024*1024,256*1024*1024));resource.setrlimit(resource.RLIMIT_CPU,(4,4))
    data=sys.stdin.buffer.read(MAX_BYTES+1)
    if len(data)>MAX_BYTES:raise SystemExit(2)
    mime=sys.argv[1]
    if mime=='application/pdf':
        from pypdf import PdfReader
        reader=PdfReader(io.BytesIO(data),strict=True)
        if reader.is_encrypted or len(reader.pages)>10:raise SystemExit(2)
        chunks=[];length=0
        for page in reader.pages:
            text=page.extract_text() or '';length+=len(text)
            if length>MAX_CHARACTERS:raise SystemExit(2)
            chunks.append(text)
        text='\n'.join(chunks)
    else:
        text=data.decode('utf-16' if data.startswith((b'\xff\xfe',b'\xfe\xff')) else 'utf-8-sig',errors='strict')
        if mime=='text/html':
            from email_agent.infrastructure.gmail.content_reader import EvidenceHTMLText
            parser=EvidenceHTMLText(include_quotes=True);parser.feed(text);text=parser.text()
    if len(text)>MAX_CHARACTERS:raise SystemExit(2)
    print(json.dumps(text,ensure_ascii=False))

if __name__=='__main__':
    try:main()
    except Exception:raise SystemExit(2)
