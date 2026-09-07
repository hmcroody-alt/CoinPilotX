"""Compare deployed staging application blobs to a precise local Git commit."""
import argparse
import base64
import json
import re
import subprocess
import zlib

from cj_staging_railway import ROOT, PROJECT, ENVIRONMENT, BACKEND, WORKER


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("service", choices=("backend", "worker"))
    parser.add_argument("sha")
    args = parser.parse_args()
    assert re.fullmatch(r"[a-f0-9]{40}", args.sha)
    targets = ["services", "bot.py", "supplier_worker.py", "cj_staging_backend.py",
               "cj_staging_runtime.py", "scripts/cj_staging_railway.py", "requirements.txt"]
    records = subprocess.check_output(["git", "ls-tree", "-r", "-z", args.sha, "--", *targets], cwd=ROOT)
    expected = {}
    for record in records.split(b"\0"):
        if record:
            metadata, name = record.split(b"\t", 1)
            mode, kind, digest = metadata.decode().split()
            assert kind == "blob" and mode in {"100644", "100755"}
            expected[name.decode()] = digest
    encoded = base64.b64encode(zlib.compress(json.dumps(expected).encode())).decode()
    remote = '''import base64,hashlib,json,pathlib,zlib
expected=json.loads(zlib.decompress(base64.b64decode(MANIFEST)))
missing=[]; different=[]
for name,digest in expected.items():
    path=pathlib.Path(name)
    if not path.is_file(): missing.append(name); continue
    value=path.read_bytes()
    actual=hashlib.sha1(b"blob "+str(len(value)).encode()+b"\\0"+value).hexdigest()
    if actual!=digest: different.append(name)
print(json.dumps({"checked_application_files":len(expected),"missing":missing,"different":different,"match":not missing and not different}))
'''.replace("MANIFEST", repr(encoded))
    result = subprocess.run(["railway", "ssh", "-p", PROJECT, "-e", ENVIRONMENT,
                             "-s", BACKEND if args.service == "backend" else WORKER,
                             "--", "python", "-c", remote], cwd=ROOT, capture_output=True, text=True, timeout=90)
    if result.returncode:
        print(json.dumps({"match": False, "error": "staging_ssh_failed"}))
        return 1
    lines = [line for line in result.stdout.splitlines() if line.startswith("{")]
    assert len(lines) == 1
    report = json.loads(lines[0])
    report.update(service=args.service, source_commit=args.sha)
    print(json.dumps(report))
    return 0 if report["match"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
