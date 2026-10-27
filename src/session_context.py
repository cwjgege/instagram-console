"""Only reuse a control session created by this app version on this Mac."""
import hashlib,platform,re,subprocess
from functools import lru_cache
from app_paths import APP_VERSION

@lru_cache(maxsize=1)
def current():
    host=platform.node()
    if platform.system()=='Darwin':
        try:
            raw=subprocess.check_output(['/usr/sbin/ioreg','-rd1','-c','IOPlatformExpertDevice'],text=True,timeout=3)
            match=re.search(r'"IOPlatformUUID"\s*=\s*"([A-Fa-f0-9-]+)"',raw)
            if match:host=match.group(1)
        except (OSError,subprocess.SubprocessError):pass
    return {'version':APP_VERSION,'host':hashlib.sha256(host.encode()).hexdigest()}
