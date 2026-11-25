"""Build/install a wheel in a clean venv outside the checkout."""
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import os
import subprocess
import sys
import venv


def main():
    root=Path(__file__).resolve().parents[2]
    with TemporaryDirectory(prefix="atlaspm-wheel-") as tmp:
        work=Path(tmp)
        env={k:v for k,v in os.environ.items() if k not in {"PYTHONPATH","PYTHONHOME"}}
        subprocess.run([sys.executable,"-m","pip","wheel","--no-deps","--wheel-dir",
                        str(work),str(root)],check=True,env=env,cwd=work,timeout=300)
        wheels=list(work.glob("atlaspm-*.whl"))
        if len(wheels)!=1:
            raise RuntimeError("EXPECTED_SINGLE_ATLASPM_WHEEL")
        venv.EnvBuilder(with_pip=True).create(work/"venv")
        python=work/"venv"/("Scripts/python.exe" if os.name=="nt" else "bin/python")
        subprocess.run([str(python),"-I","-m","pip","install",str(wheels[0])],
                       check=True,env=env,cwd=work,timeout=300)
        subprocess.run([str(python),"-I","-m","pip","check"],check=True,env=env,cwd=work,timeout=60)
        def run(*args):
            return subprocess.check_output([str(python),"-I",*args],env=env,cwd=work,
                                           timeout=60,text=True,encoding="utf-8")
        location=Path(run("-c","import atlaspm; print(atlaspm.__file__)").strip()).resolve()
        if not location.is_relative_to((work/"venv").resolve()):
            raise RuntimeError("WHEEL_IMPORT_NOT_ISOLATED")
        architecture=json.loads(run("-m","atlaspm.cli","architecture"))
        if architecture["status"]!="OFFLINE_FOUNDATION_CONTRACT_PASS" or architecture["errors"]:
            raise RuntimeError("WHEEL_ARCHITECTURE_FAILED")
        for name in ("atlaspm.data.sqlite_recovery","atlaspm.data.lake_recovery","atlaspm.framework.runtime_health",
                     "atlaspm.research.current_research"):
            run("-m",name,"--help")
        print(json.dumps({"status":"INSTALLED_WHEEL_SMOKE_PASS",
                          "wheel":wheels[0].name,"isolated_import":True,
                          "live_execution_enabled":False,"broker_order_submission_enabled":False}))


if __name__=="__main__":
    main()
