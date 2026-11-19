#!/usr/bin/env bash
# Install QNX Software Center, the QNX SDP 8.0 baseline and the QEMU quick
# start image on a Windows host, from Git Bash, without the GUI.
#
# Credentials come from MYQNX_USER / MYQNX_PASSWORD if set, otherwise they are
# prompted for and never written to disk.
#
# Usage:
#   bash qemu/install-sdp.sh             # install QSC, then list matching packages
#   bash qemu/install-sdp.sh --install   # also install the 8.0 baseline + QEMU image

set -euo pipefail

QNX_SETUP_URL="${QNX_SETUP_URL:-https://www.qnx.com/download/download/79443/qnx-setup-2.0.4-202501021438-win.exe}"
INSTALL_ROOT="${QNX_INSTALL_ROOT:-/c/QNX}"
QSC_DIR="$INSTALL_ROOT/QNX Software Center"
QSC="$QSC_DIR/qnxsoftwarecenter_clt.bat"
# QSC rejects paths containing spaces and its default workspace lives under
# C:\Users\<name>, so the workspace and the install both sit at the drive root.
QSC_WS='C:\qnxws'
SDP_DIR='C:\qnx800'
BASELINE="${QNX_BASELINE:-com.qnx.qnx800}"
QEMU_PKG="${QNX_QEMU_PKG:-com.qnx.qnx800.quickstart.qemu}"
WORK="${TMPDIR:-/tmp}/qnx-setup"
COOKIES="$HOME/.qnx/myqnxcookies.auth"

if [[ -z "${MYQNX_USER:-}" ]]; then
  read -r -p "myQNX email: " MYQNX_USER
fi
if [[ -z "${MYQNX_PASSWORD:-}" ]]; then
  read -r -s -p "myQNX password: " MYQNX_PASSWORD
  echo
fi

qsc() {
  "$QSC" -data "$QSC_WS" -myqnx.user "$MYQNX_USER" -myqnx.password "$MYQNX_PASSWORD" "$@"
}

mkdir -p "$HOME/.qnx" "$WORK"

if [[ ! -f "$QSC" ]]; then
  SETUP="$WORK/qnx-setup.exe"
  if [[ ! -s "$SETUP" ]]; then
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

    echo "== downloading the QNX Software Center installer"
    curl -sS -L --cookie "$COOKIES" -o "$SETUP" "$QNX_SETUP_URL"
  fi
  ls -l "$SETUP"

  echo "== installing the QNX Software Center into C:\\QNX"
  # Running the installer straight from this shell fails with "Device or
  # resource busy" while the download is still open, so let a second shell wait.
  (cd "$WORK" && cmd //c "start /wait qnx-setup.exe /CurrentUser /S /D=C:\\QNX")
fi

if [[ ! -f "$QSC" ]]; then
  echo "QNX Software Center not found at $QSC" >&2
  exit 1
fi

echo "== $("$QSC" -data "$QSC_WS" -version | head -1)"

echo "== packages mentioning qemu or the 8.0 baseline"
out="$WORK/list.txt"
qsc -list >"$out" 2>&1 || true
if grep -q "allrepos" "$out"; then
  cat >&2 <<'EOF'
QSC could not reach the package server. It reports an unauthenticated or
unentitled account that way, so check on https://www.qnx.com/account/dashboard/
that the QNX Everywhere licence is accepted and an SDP 8.0 key is deployed to
this myQNX user.
EOF
  exit 1
fi
grep -Ei "qnx800|quickstart|qemu" "$out" | head -40 || true

if [[ "${1:-}" != "--install" ]]; then
  cat <<EOF

Re-run with --install to add the baseline and the QEMU image, or run:

  qnxsoftwarecenter_clt -data $QSC_WS -destination $SDP_DIR -installBaseline $BASELINE
  qnxsoftwarecenter_clt -data $QSC_WS -destination $SDP_DIR -installPackage $QEMU_PKG
EOF
  exit 0
fi

echo "== installing the 8.0 baseline into $SDP_DIR"
qsc -destination "$SDP_DIR" -installBaseline "$BASELINE"

echo "== installing the QEMU quick start image"
qsc -destination "$SDP_DIR" -installPackage "$QEMU_PKG"

echo
echo "Done. Next:"
echo "  qemu/run-qnx.sh $SDP_DIR/images/qemu/qemu"
