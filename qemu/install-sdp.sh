#!/usr/bin/env bash
# Install the QNX Software Center and QNX SDP 8.0 (plus the QEMU quick start
# image) on Windows, without the GUI.
#
# Steps mirror "Unattended installation on Windows" in the QNX Software Center
# documentation. Credentials are read from MYQNX_USER / MYQNX_PASSWORD if set,
# otherwise prompted for and never written to disk.
#
# Usage:
#   bash qemu/install-sdp.sh            # prompt, then check what is available
#   bash qemu/install-sdp.sh --install  # also install SDP + QEMU image

set -euo pipefail

QNX_SETUP_URL="${QNX_SETUP_URL:-https://www.qnx.com/download/download/79443/qnx-setup-2.0.4-202501021438-win.exe}"
INSTALL_DIR="${QNX_INSTALL_DIR:-/c/QNX}"
COOKIES="$HOME/.qnx/myqnxcookies.auth"
WORK="${TMPDIR:-/tmp}/qnx-setup"

if [[ -z "${MYQNX_USER:-}" ]]; then
  read -r -p "myQNX email: " MYQNX_USER
fi
if [[ -z "${MYQNX_PASSWORD:-}" ]]; then
  read -r -s -p "myQNX password: " MYQNX_PASSWORD
  echo
fi

mkdir -p "$HOME/.qnx" "$WORK"

echo "== logging in as $MYQNX_USER"
curl -sS -X POST https://www.qnx.com/account/login.html \
  --form "userlogin=$MYQNX_USER" \
  --form "password=$MYQNX_PASSWORD" \
  --cookie-jar "$COOKIES" \
  -o "$WORK/login.html"

if ! grep -qi "logout" "$WORK/login.html"; then
  echo "login did not return a session; check the credentials or open this by hand:" >&2
  echo "  $WORK/login.html" >&2
  exit 1
fi

SETUP="$WORK/qnx-setup.exe"
if [[ ! -s "$SETUP" ]]; then
  echo "== downloading the QNX Software Center installer"
  curl -sS -L --cookie "$COOKIES" -o "$SETUP" "$QNX_SETUP_URL"
fi
ls -l "$SETUP"

QSC="$INSTALL_DIR/qnxsoftwarecenter/qnxsoftwarecenter_clt.bat"
if [[ ! -f "$QSC" ]]; then
  echo "== installing the QNX Software Center into $INSTALL_DIR"
  "$SETUP" /CurrentUser /S /D="C:\\QNX"
fi

if [[ ! -f "$QSC" ]]; then
  echo "QNX Software Center not found at $QSC" >&2
  ls "$INSTALL_DIR" >&2 || true
  exit 1
fi

auth=(-myqnx.user="$MYQNX_USER" -myqnx.password="$MYQNX_PASSWORD")

echo "== packages mentioning qemu or the 8.0 baseline"
"$QSC" "${auth[@]}" -list 2>/dev/null | grep -Ei "qnx800|quickstart|qemu" | head -40 || true

if [[ "${1:-}" != "--install" ]]; then
  cat <<'EOF'

Re-run with --install to add the SDP and the QEMU image, or run the two
commands below once the package ids above look right:

  qnxsoftwarecenter_clt -destination %USERPROFILE%\qnx800 \
      -installBaseline com.qnx.qnx800 <auth>
  qnxsoftwarecenter_clt -destination %USERPROFILE%\qnx800 \
      -installPackage com.qnx.qnx800.quickstart.qemu <auth>
EOF
  exit 0
fi

SDP_DIR="${QNX_SDP_DIR:-$HOME/qnx800}"
echo "== installing SDP 8.0 into $SDP_DIR"
"$QSC" "${auth[@]}" -destination "$SDP_DIR" -installBaseline com.qnx.qnx800

echo "== installing the QEMU quick start image"
"$QSC" "${auth[@]}" -destination "$SDP_DIR" -installPackage com.qnx.qnx800.quickstart.qemu

echo
echo "Done. Images should be under $SDP_DIR/images/qemu/qemu/output"
