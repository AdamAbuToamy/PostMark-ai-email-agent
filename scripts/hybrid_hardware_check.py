"""Read-only host inventory; never installs, downloads or submits inference."""
import json
from pathlib import Path
import shutil
import subprocess
from email_agent.infrastructure.ai.hybrid_providers import LocalModelProvider

memory={k:int(v.split()[0]) for line in Path('/proc/meminfo').read_text().splitlines() if ':' in line for k,v in [line.split(':',1)] if k in ('MemTotal','MemAvailable','SwapTotal','SwapFree')}
cpu=next((line.split(':',1)[1].strip() for line in Path('/proc/cpuinfo').read_text().splitlines() if line.startswith('model name')),'unknown')
gpu=None
try:
 gpu=subprocess.run([shutil.which('nvidia-smi') or '/usr/lib/wsl/lib/nvidia-smi','--query-gpu=name,memory.total,memory.free,driver_version,compute_cap','--format=csv,noheader'],capture_output=True,text=True,timeout=8,check=True).stdout.strip()
except (OSError,subprocess.SubprocessError):pass
try:models=LocalModelProvider().discover();ollama='metadata_connected'
except Exception:models=[];ollama='offline'
print(json.dumps(dict(cpu=cpu,memory_kib=memory,disk_free_bytes=shutil.disk_usage('.').free,gpu=gpu,cuda_toolkit=bool(shutil.which('nvcc')),ollama=ollama,installed_local_models=models,notes='Current host only; run again on the HP server before choosing context/offload. No performance/quality claim from metadata alone.'),ensure_ascii=False,indent=2))
