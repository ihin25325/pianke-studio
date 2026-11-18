"""Explicit opt-in current-user install/upgrade/uninstall test in project work/.

Temporarily creates the product's HKCU uninstall registration. Never run if that
AppId is already installed; never uninstall or overwrite another installation.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
APP_KEY=r'Software\Microsoft\Windows\CurrentVersion\Uninstall\{F8D35343-2519-44B6-A6CA-70048C3A25F7}_is1'


def installed():
    import winreg
    for hive in (winreg.HKEY_CURRENT_USER,winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY,winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive,APP_KEY,0,winreg.KEY_READ|view):return True
            except FileNotFoundError:pass
    return False


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(setup,media_root,consent=False):
    if not consent:raise ValueError('Explicit --allow-user-registration consent is required')
    if os.name!='nt' or installed():raise ValueError('Windows required and no existing Pianke Studio installation may exist')
    expected=setup.with_suffix('.exe.sha256').read_text().split()[0]
    if sha(setup)!=expected:raise ValueError('Setup SHA256 mismatch')
    folder=ROOT/'work/windows_install_verification';folder.mkdir(parents=True,exist_ok=True)
    parent=Path(tempfile.mkdtemp(prefix='install_',dir=folder));root=parent/'PiankeStudio'
    if root.exists() or parent.resolve() not in root.resolve().parents:raise ValueError('Unsafe test destination')
    args=[str(setup),'/CURRENTUSER','/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/NOICONS','/TASKS=',
          '/DIR='+str(root),'/LOG='+str(parent/'install.log')]
    subprocess.run(args,check=True,timeout=180)
    keep={};proof={};uninstalled=False
    try:
        if not installed():raise ValueError('Expected current-user test registration not found')
        shutil.copytree(media_root/'tools/ffmpeg',root/'tools/ffmpeg')
        fonts=['Oswald/Oswald-Regular.ttf','Oswald/Oswald-SemiBold.ttf',
               'SourceHanSans/SourceHanSansCN-Regular.otf','SourceHanSans/SourceHanSansCN-Bold.otf']
        for name in fonts:
            source=media_root/'assets/fonts'/name;target=root/'assets/fonts'/name
            if target.exists():raise ValueError('Unexpected pre-existing font binary in fresh install')
            shutil.copy2(source,target)
        exe=root/'PiankeStudio.exe';python=root/'tools/python/python.exe'
        env=dict(os.environ,PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1',PYTHONIOENCODING='utf-8',
                 PATH=str(Path(os.environ['WINDIR'])/'System32')+os.pathsep+os.environ['WINDIR'],
                 HTTP_PROXY='http://127.0.0.1:9',HTTPS_PROXY='http://127.0.0.1:9',NO_PROXY='127.0.0.1,localhost')
        subprocess.run([str(exe),'--smoke-test'],check=True,timeout=90,cwd=root,env=env)
        subprocess.run([str(python),'-B','scripts/verify_clean_release.py'],check=True,timeout=240,cwd=root,env=env)
        # Create only synthetic private state, never a real API credential.
        fixture='from pathlib import Path; import json; import editor_server as a; e=a.Editor(start_worker=False); e.connections.save({"api_key":"sk-local-test-not-valid-network"}); Path("configs/typography.json").write_text(json.dumps({"title_scale":1.15}),encoding="utf-8"); Path("data/retention-check.txt").write_text("synthetic user data",encoding="utf-8")'
        subprocess.run([str(python),'-B','-c',fixture],check=True,timeout=30,cwd=root,env=env)
        for name in ['data/editor/queue.json','data/editor/private_api/deepseek.json','configs/typography.json','data/retention-check.txt']:
            keep[name]=sha(root/name)
        output=json.loads((root/'work/release_smoke/proof.json').read_text(encoding='utf-8'))['output']
        if root.resolve() not in Path(output).resolve().parents:raise ValueError('Unexpected render path')
        keep[Path(output).relative_to(root).as_posix()]=sha(Path(output))
        # Simulate an active backend lock and verify the installer refuses it.
        lock_code='import msvcrt,sys; f=open("data/editor/server.lock","r+b"); msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1); print("locked",flush=True); sys.stdin.readline()'
        locker=subprocess.Popen([str(python),'-B','-c',lock_code],stdin=subprocess.PIPE,stdout=subprocess.PIPE,cwd=root,env=env,text=True,encoding='utf-8')
        try:
            if locker.stdout.readline().strip()!='locked':raise ValueError('Test lock failed')
            if subprocess.run([str(exe),'--can-update'],timeout=15,cwd=root,env=env).returncode==0:raise ValueError('Running backend lock was not detected')
            blocked=subprocess.run(args[:-1]+['/LOG='+str(parent/'blocked-upgrade.log')],timeout=40)
            if blocked.returncode==0:raise ValueError('Installer did not block a live backend')
            proof['active_upgrade_blocked']=True
        finally:
            locker.communicate('\n',timeout=10)
        subprocess.run(args[:-1]+['/LOG='+str(parent/'upgrade.log')],check=True,timeout=180)
        if any(sha(root/name)!=value for name,value in keep.items()):raise ValueError('Upgrade changed private data')
        proof['upgrade_preserved_private_files']=len(keep)
    finally:
        uninstaller=root/'unins000.exe'
        if uninstaller.is_file():
            subprocess.run([str(uninstaller),'/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/LOG='+str(parent/'uninstall.log')],check=True,timeout=180)
            for _ in range(50):
                if not installed():break
                time.sleep(.2)
            if installed():raise ValueError('Temporary product registration remains after uninstall')
            uninstalled=True
    if not uninstalled:raise ValueError('Temporary installation could not be uninstalled')
    if any(not (root/name).is_file() or sha(root/name)!=value for name,value in keep.items()):raise ValueError('Uninstall removed private data')
    proof.update(ok=True,installer=str(setup),installer_sha256=sha(setup),test_root=str(root),
                 uninstall_preserved_private_files=len(keep),registration_removed=True,
                 note='Real current-user silent install/upgrade/uninstall; no desktop click-through recording or clean VM claim.')
    path=parent/'proof.json';path.write_text(json.dumps(proof,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Installer verification proof: '+str(path),flush=True)
    return proof


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('setup',type=Path);parser.add_argument('--media-from',required=True,type=Path)
    parser.add_argument('--allow-user-registration',action='store_true');args=parser.parse_args()
    verify(args.setup.resolve(),args.media_from.resolve(),args.allow_user_registration)
