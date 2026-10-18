"""Tell the published site where the Overfield server is, and rebuild it.

    python scripts/publish_backend_url.py https://quiet-river-1234.trycloudflare.com

Sets the repository variables API_BASE (the accounts, admin and /v1 routes) and SIGNUP_URL (the sign-up page on the
same server), then runs the Pages workflow so the live site picks them up (about two minutes). With --clear the
variables are removed and the site runs without accounts. Needs the GitHub CLI signed in (gh auth status).
"""
import argparse
import shutil
import subprocess
import sys
import urllib.request

GH = shutil.which("gh") or r"C:\Program Files\GitHub CLI\gh.exe"


def gh(*args):
    out = subprocess.run([GH, *args], capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit(f"gh {' '.join(args)} failed: {out.stderr.strip() or out.stdout.strip()}")
    return out.stdout.strip()


def main():
    ap = argparse.ArgumentParser(description="Publish the Overfield server address to the site.")
    ap.add_argument("url", nargs="?", help="public https address of the inbound server (the tunnel)")
    ap.add_argument("--clear", action="store_true", help="remove the variables; the site runs without accounts")
    ap.add_argument("--no-deploy", action="store_true", help="set the variables but do not run the workflow")
    a = ap.parse_args()
    if a.clear:
        for name in ("API_BASE", "SIGNUP_URL"):
            subprocess.run([GH, "variable", "delete", name], capture_output=True, text=True)
        print("variables removed")
    else:
        if not a.url or not a.url.startswith("https://"):
            ap.error("give the public https address of the server, for example https://quiet-river-1234.trycloudflare.com")
        base = a.url.rstrip("/")
        try:
            with urllib.request.urlopen(base + "/v1/health", timeout=15) as r:
                print("server answers:", r.read(200).decode("utf-8", "replace"))
        except Exception as e:  # keep going: the tunnel may still be warming up
            print(f"warning: {base}/v1/health did not answer yet ({e})")
        gh("variable", "set", "API_BASE", "--body", base)
        gh("variable", "set", "SIGNUP_URL", "--body", base + "/signup")
        print(f"API_BASE={base}  SIGNUP_URL={base}/signup")
    if not a.no_deploy:
        gh("workflow", "run", "pages.yml")
        print("Pages workflow started; the site updates in about two minutes")


if __name__ == "__main__":
    main()
