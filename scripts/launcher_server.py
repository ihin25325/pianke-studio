"""Keep logs attached to the server after the GUI exits; run in the same PID."""
import argparse
import os
from pathlib import Path
import runpy
import sys


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--port',required=True,type=int)
    parser.add_argument('--log',required=True)
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    log=Path(args.log).resolve()
    if root/'work'/'launcher' not in log.parents:
        raise ValueError('Launcher logs must stay under project work/launcher')
    log.parent.mkdir(parents=True,exist_ok=True)
    with log.open('x',encoding='utf-8',buffering=1) as stream:
        sys.stdout=sys.stderr=stream
        sys.argv=[str(root/'scripts/editor_server.py'),'--port',str(args.port)]
        runpy.run_path(sys.argv[0],run_name='__main__')


if __name__=='__main__':main()
