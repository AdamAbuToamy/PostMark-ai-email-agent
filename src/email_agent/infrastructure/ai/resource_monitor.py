"""Bounded Linux/WSL resource sampling; no model calls or process mutation."""
import os
import base64
import itertools
import json
import re
import subprocess
import threading
import time
from pathlib import Path


def advise_idle_model_cache(root=None):
    """Advise away clean, unmapped installed-weight pages, never the weights.

    DONTNEED is advisory: mapped/in-use pages remain protected by the kernel.
    Returned bytes describe files advised, not a claim of reclaimed RAM.
    """
    root=Path(root) if root is not None else Path.home()/'.ollama/models'
    digests=set();advised=0
    try:
        for manifest in itertools.islice((root/'manifests').rglob('*'),64):
            if not manifest.is_file() or manifest.is_symlink() or manifest.stat().st_size>262144:continue
            try:
                data=json.loads(manifest.read_text())
                if not isinstance(data,dict):continue
                for layer in data.get('layers',[])[:64]:
                    if isinstance(layer,dict) and layer.get('mediaType') in ('application/vnd.ollama.image.model','application/vnd.ollama.image.projector') and re.fullmatch(r'sha256:[0-9a-f]{64}',str(layer.get('digest',''))):digests.add(layer['digest'])
            except (OSError,ValueError,TypeError):continue
        for digest in digests:
            try:
                fd=os.open(root/'blobs'/digest.replace(':','-'),os.O_RDONLY|os.O_NOFOLLOW)
                try:
                    os.posix_fadvise(fd,0,0,os.POSIX_FADV_DONTNEED);advised+=os.fstat(fd).st_size
                finally:os.close(fd)
            except OSError:continue
    except OSError:pass
    return advised


class ResourceMonitor:
    def __init__(self):
        self.lock=threading.RLock();self.cached=None;self.sampled=0.;self.pause_until=0.;self.host=None;self.host_at=0.;self.model_state=None;self.model_at=0.
        self.idle_since=None;self.cache_advice_at=0.;self.cache_advised_bytes=0
        self.local_required_bytes=max(512,min(65536,int(os.getenv("POSTMARK_LOCAL_RAM_MB","3072"))))*1024**2

    def _release_idle_cache(self,now,runners,pressure):
        if any(r.get('name')=='llama-server' for r in runners):
            self.idle_since=None;return
        if not any(r.get('name')=='ollama' for r in runners):return
        if self.idle_since is None:self.idle_since=now
        if pressure!='Normal' and now-self.idle_since>=30 and now-self.cache_advice_at>=300:
            self.cache_advice_at=now;self.cache_advised_bytes=advise_idle_model_cache()

    def _sample_host(self):
        executable='/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe'
        if not Path(executable).exists():return
        code="$o=Get-CimInstance Win32_OperatingSystem;$v=Get-Process -Name vmmemWSL -ErrorAction SilentlyContinue;@{TotalVisibleMemorySize=$o.TotalVisibleMemorySize;FreePhysicalMemory=$o.FreePhysicalMemory;WslWorkingSetBytes=($v|Measure-Object WorkingSet64 -Sum).Sum;WslCommitBytes=($v|Measure-Object PrivateMemorySize64 -Sum).Sum}|ConvertTo-Json -Compress"
        try:
            response=subprocess.run([executable,'-NoProfile','-NonInteractive','-EncodedCommand',base64.b64encode(code.encode('utf-16-le')).decode()],capture_output=True,text=True,timeout=4,check=True)
            value=json.loads(response.stdout)
            with self.lock:self.host=value;self.host_measured_at=time.time()
        except (OSError,ValueError,subprocess.SubprocessError):pass

    @staticmethod
    def memory_detail(pid):
        try:
            d={k:int(v.split()[0])*1024 for k,v in (line.split(':',1) for line in Path(f'/proc/{pid}/smaps_rollup').read_text().splitlines() if ':' in line)}
            return dict(pss_bytes=d.get('Pss'),uss_bytes=sum(d.get(k,0) for k in ('Private_Clean','Private_Dirty','Private_Hugetlb')))
        except (OSError,ValueError):return dict(pss_bytes=None,uss_bytes=None)

    def snapshot(self):
        with self.lock:
            now=time.time()
            if self.cached and now-self.sampled<15:return dict(self.cached)
            fields={line.split(':')[0]:int(line.split()[1])*1024 for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith(('MemTotal:','MemAvailable:','SwapTotal:','SwapFree:','Cached:','Buffers:','SReclaimable:','Shmem:'))}
            try:psi=float(Path('/proc/pressure/memory').read_text().split('full ',1)[1].split()[0].split('=')[1])
            except (OSError,ValueError,IndexError):psi=None
            if now-self.host_at>=60:
                self.host_at=now
                threading.Thread(target=self._sample_host,daemon=True,name='postmark-host-sample').start()
            host_available=int(self.host['FreePhysicalMemory'])*1024 if self.host else None
            pressure='Critical' if fields['MemAvailable']<768*1024**2 or host_available is not None and host_available<max(768*1024**2,int(self.host['TotalVisibleMemorySize'])*1024*.05) or psi is not None and psi>=10 else 'High' if fields['MemAvailable']<1536*1024**2 or host_available is not None and host_available<max(1536*1024**2,int(self.host['TotalVisibleMemorySize'])*1024*.10) or psi is not None and psi>=2 else 'Normal'
            if pressure!='Normal':self.pause_until=max(self.pause_until,now+300)
            def rss(pid):
                try:return next(int(l.split()[1])*1024 for l in Path(f'/proc/{pid}/status').read_text().splitlines() if l.startswith('VmRSS:'))
                except (OSError,StopIteration):return 0
            runners=[]
            for path in Path('/proc').iterdir():
                if path.name.isdigit():
                    try:
                        name=path.joinpath('comm').read_text().strip()
                        if name in ('ollama','llama-server'):runners.append(dict(pid=int(path.name),name=name,rss_bytes=rss(path.name),**self.memory_detail(path.name)))
                    except OSError:pass
            loaded=any(r.get('name')=='llama-server' for r in runners)
            runner_pss=sum(r.get('pss_bytes') or 0 for r in runners if r.get('name')=='llama-server')
            if runner_pss>512*1024**2:self.local_required_bytes=max(self.local_required_bytes,runner_pss)
            host_reserve=max(1536*1024**2,int(self.host['TotalVisibleMemorySize'])*1024*.10) if self.host else 1536*1024**2
            load_deferred=not loaded and (fields['MemAvailable']<self.local_required_bytes+1536*1024**2 or host_available is not None and host_available<self.local_required_bytes+host_reserve)
            host_pending=self.host is None and Path('/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe').exists()
            self._release_idle_cache(now,runners,pressure)
            self.cached=dict(host_total_bytes=int(self.host['TotalVisibleMemorySize'])*1024 if self.host else None,host_available_bytes=host_available,measured_at=now,scope='Linux/WSL; host Windows RAM is separate',linux_total_bytes=fields['MemTotal'],linux_available_bytes=fields['MemAvailable'],swap_used_bytes=fields['SwapTotal']-fields['SwapFree'],memory_stall_percent=psi,pressure=pressure,backend_pid=os.getpid(),backend_rss_bytes=rss(os.getpid()),ollama_processes=runners,ollama_rss_bytes=sum(r['rss_bytes'] for r in runners),local_throttled=now<self.pause_until or load_deferred or host_pending,local_resume_check_at=self.pause_until if now<self.pause_until else None,reason='Waiting for Windows memory measurement' if host_pending else 'Local model load deferred: measured footprint would leave insufficient free RAM' if load_deferred else 'Local admission paused for memory pressure; active inference is allowed to finish' if now<self.pause_until else None)
            self.sampled=now
            self.cached.update(local_required_bytes=self.local_required_bytes,local_load_deferred=load_deferred,backend_memory=self.memory_detail(os.getpid()),host_measured_at=getattr(self,'host_measured_at',None),wsl_working_set_bytes=(self.host or {}).get('WslWorkingSetBytes'),wsl_commit_bytes=(self.host or {}).get('WslCommitBytes'),linux_cache_bytes=sum(fields.get(k,0) for k in ('Cached','Buffers','SReclaimable')),linux_shmem_bytes=fields.get('Shmem'),ollama_pss_bytes=sum(r.get('pss_bytes') or 0 for r in runners))
            self.cached.update(idle_cache_advice_at=self.cache_advice_at or None,idle_cache_advised_file_bytes=self.cache_advised_bytes)
            return dict(self.cached)

    def model_snapshot(self,fetch):
        with self.lock:
            if time.time()-self.model_at>=60:
                self.model_at=time.time()
                try:
                    data=fetch();self.model_state=[dict(model=m.get('model',m.get('name')),vram_bytes=m.get('size_vram'),resident_bytes=m.get('size'),context_length=m.get('context_length'),expires_at=m.get('expires_at')) for m in data.get('models',[])]
                except Exception:self.model_state=None
            return self.model_state

    def local_allowed(self):return not self.snapshot()['local_throttled']


resources=ResourceMonitor()
